"""Typed runtime adapter for Open Banking Dynamic Client Registration 3.4."""

from __future__ import annotations

import base64
import json
import re
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography import x509
from joserfc import jwk, jwt
from joserfc.errors import InvalidKeyTypeError, JoseError

from conformance.approved_releases import ApprovedReleasePolicy
from conformance.catalogue import CatalogueExecutionStep, CatalogueTestCase, CompiledTestPlan
from conformance.execution_log import ExecutionLogger
from conformance.http import JsonHttpClientError, JsonHttpResponse, send_json
from conformance.json_types import JsonObject, JsonValue
from conformance.masking import MASKED_VALUE, mask_form_fields, mask_headers, mask_json_value
from conformance.plan_configuration import ClientAuthMethod, DcrPlanConfiguration
from conformance.results import CheckStatus, SmokeCheckResult, StepResult, build_smoke_check_result
from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri

_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
"""OAuth JWT client assertion type used by DCR token requests."""

_JWT_LIFETIME = timedelta(minutes=5)
"""Short validity period for registration and client-assertion JWTs."""

_DCR_AUTH_METHODS = frozenset({"tls_client_auth", "private_key_jwt", "client_secret_jwt", "client_secret_basic"})
"""Token endpoint authentication methods implemented by this adapter."""

_DCR_DN_PATTERN = re.compile(r"^(?:(?i:CN)|2\.5\.4\.3)=[^,]+(?:,[^,\s]+=[^,]+)*$")
"""Conservative DCR 3.4 subject-DN syntax boundary."""


class DcrExecutionError(ValueError):
    """Raised when DCR runtime configuration or protocol data is invalid."""


@dataclass(frozen=True)
class DcrDiscoveryMetadata:
    """Strictly validated DCR discovery metadata.

    Attributes:
        issuer: HTTPS authorization-server issuer.
        registration_endpoint: HTTPS dynamic registration endpoint.
        token_endpoint: HTTPS OAuth token endpoint.
        jwks_uri: HTTPS authorization-server JWKS endpoint.
        token_auth_methods: Advertised token client-auth methods.
        token_auth_signing_algorithms: Advertised client-assertion algorithms.
        jwks: Validated public JSON Web Key Set.
    """

    issuer: str
    registration_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_auth_methods: tuple[str, ...]
    token_auth_signing_algorithms: tuple[str, ...]
    jwks: JsonObject


@dataclass
class DcrScenarioState:
    """Mutable protocol state isolated to one DCR scenario.

    Attributes:
        signed_registration_jose: Most recently generated compact registration JWS.
        registration_claims: Claims covered by the most recent registration JWS.
        client_id: Dynamically issued client identifier.
        client_secret: Dynamically issued client secret.
        registration_access_token: Registration access token returned at creation.
        client_credentials_access_token: OAuth client-credentials access token.
        registration_response: Most recent registration or management JSON body.
        token_response: Most recent token response.
        management_url: Validated dynamic registration management URL.
        last_response: Most recent HTTP response used by assertion steps.
        deleted: Whether a successful DELETE has completed.
    """

    signed_registration_jose: str | None = None
    registration_claims: JsonObject | None = None
    client_id: str | None = None
    client_secret: str | None = None
    registration_access_token: str | None = None
    client_credentials_access_token: str | None = None
    registration_response: JsonObject | None = None
    token_response: JsonObject | None = None
    management_url: str | None = None
    last_response: JsonHttpResponse | None = None
    deleted: bool = False


@dataclass
class DcrCatalogueExecutionAdapter:
    """Execute protocol-neutral DCR catalogue steps through shared result/log paths.

    Attributes:
        compiled_plan: Compiled DCR catalogue graph to execute sequentially.
        config: Validated shared and DCR-specific runtime configuration.
        execution_logger: Shared structured execution-log sink.
        client: Optional caller-owned mTLS HTTP client.
        clock: Injectable UTC clock for JOSE claim construction.
        jwt_id_factory: Injectable unique JWT identifier factory.
        approved_release_policy: Optional approved-release policy used for
            certification eligibility.
    """

    compiled_plan: CompiledTestPlan
    config: DcrPlanConfiguration
    execution_logger: ExecutionLogger
    client: httpx.Client | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    jwt_id_factory: Callable[[], str] = lambda: uuid4().hex
    approved_release_policy: ApprovedReleasePolicy | None = None
    _discovery: DcrDiscoveryMetadata | None = field(default=None, init=False)
    _signing_key: jwk.Key | None = field(default=None, init=False)

    def run(self) -> SmokeCheckResult:
        """Execute selected DCR scenarios in deterministic catalogue order.

        Returns:
            Shared immutable smoke-check result with one entry per catalogue
            execution step.

        Raises:
            DcrExecutionError: If the adapter is used with a non-DCR plan or
                transport configuration cannot be initialized.
        """
        self._validate_plan_boundary()
        started_at = datetime.now(UTC)
        owned_client = self.client is None
        active_client = self.client or build_dcr_mtls_client(self.config)
        self.client = active_client
        try:
            steps = self._run_scenarios()
        finally:
            if owned_client:
                active_client.close()
                self.client = None
        return build_smoke_check_result(
            steps,
            started_at=started_at,
            certification_coverage="complete",
            compiled_plan=self.compiled_plan,
            non_certifying_reasons=self.compiled_plan.traceability.non_certifying_reasons,
            approved_release_policy=self.approved_release_policy,
        )

    def build_registration_jose(
        self,
        *,
        variant: str = "valid",
        overrides: Mapping[str, JsonValue] | None = None,
    ) -> tuple[str, JsonObject]:
        """Build a compact PS256 Open Banking registration request.

        Args:
            variant: Catalogue negative-test variant to apply.
            overrides: Explicit claim replacements applied after the variant.

        Returns:
            Compact JWS and the exact JSON claims it covers.

        Raises:
            DcrExecutionError: If discovery, SSA, certificate, key, or variant
                data is invalid.
        """
        self._require_discovery()
        now = self._utc_now()
        ssa = self._read_ssa()
        ssa_claims = decode_compact_jwt_claims(ssa)
        issuer = self.config.dynamic_client_registration.registration_issuer_override
        if issuer is None:
            issuer = _required_string(ssa_claims, "software_id", location="software statement")
        audience = self.config.dynamic_client_registration.registration_audience
        redirect_uris = self.config.dynamic_client_registration.redirect_uris_override
        if not redirect_uris:
            redirect_uris = _required_string_array(
                ssa_claims,
                "software_redirect_uris",
                location="software statement",
            )
        for redirect_uri in redirect_uris:
            try:
                validate_oauth_redirect_uri(redirect_uri, label="DCR redirect_uri")
            except HttpsUrlValidationError as error:
                raise DcrExecutionError(str(error)) from error

        auth_method = self._auth_method()
        claims: JsonObject = {
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + _JWT_LIFETIME).timestamp()),
            "jti": self._jwt_id(),
            "software_statement": ssa,
            "application_type": "web",
            "redirect_uris": list(redirect_uris),
            "grant_types": ["client_credentials", "authorization_code"],
            "response_types": ["code id_token"],
            "scope": "accounts openid",
            "token_endpoint_auth_method": auth_method,
            "id_token_signed_response_alg": "PS256",
            "request_object_signing_alg": "PS256",
        }
        if auth_method == "tls_client_auth":
            claims["tls_client_auth_subject_dn"] = certificate_subject_dn(
                self.config.shared.mtls.client_certificate_path,
                override=self.config.dynamic_client_registration.transport_certificate_subject_dn_override,
                numeric_oids=self.config.dynamic_client_registration.use_numeric_oid_subject_dn,
            )
        elif auth_method == "private_key_jwt":
            claims["token_endpoint_auth_signing_alg"] = "PS256"  # noqa: S105 - JOSE algorithm identifier.
        elif auth_method == "client_secret_jwt":
            claims["token_endpoint_auth_signing_alg"] = "HS256"  # noqa: S105 - JOSE algorithm identifier.

        signing_algorithm = "PS256"
        if variant == "registration-signing-alg-rs256":
            signing_algorithm = "RS256"
            claims["id_token_signed_response_alg"] = "RS256"  # noqa: S105 - JOSE algorithm identifier.
        else:
            _apply_registration_variant(claims, variant=variant, now=now)
        if overrides is not None:
            claims.update(overrides)
        key_id = self.config.shared.signing.key_id
        if key_id is None:
            raise DcrExecutionError("DCR signing key id is required")
        try:
            compact = jwt.encode(
                {"alg": signing_algorithm, "kid": key_id, "typ": "JWT"},
                claims,
                self._load_signing_key(),
                algorithms=[signing_algorithm],
            )
        except (JoseError, TypeError, ValueError) as error:
            raise DcrExecutionError("Unable to sign DCR registration JOSE with the configured key") from error
        return compact, claims

    def _run_scenarios(self) -> list[StepResult]:
        """Execute scenarios with isolated state and dependency propagation.

        Returns:
            Ordered shared step results.
        """
        grouped_cases: dict[str, list[CatalogueTestCase]] = {}
        for test_case in self.compiled_plan.test_cases:
            if test_case.trace_group is None:
                raise DcrExecutionError(f"DCR case {test_case.test_case_id} is missing scenario trace metadata")
            grouped_cases.setdefault(test_case.trace_group.group_id, []).append(test_case)

        results: list[StepResult] = []
        case_statuses: dict[str, str] = {}
        for scenario_id, cases in grouped_cases.items():
            state = DcrScenarioState()
            for test_case in cases:
                dependency_failure = next(
                    (dependency for dependency in test_case.dependencies if case_statuses.get(dependency) != "passed"),
                    None,
                )
                if dependency_failure is not None:
                    case_results = [
                        self._skipped_result(
                            test_case,
                            step,
                            f"prerequisite case {dependency_failure} did not pass",
                        )
                        for step in test_case.execution_steps
                    ]
                else:
                    case_results = self._run_case(test_case, state)
                results.extend(case_results)
                case_statuses[test_case.test_case_id] = (
                    "passed" if all(result.status == "passed" for result in case_results) else "failed"
                )
            self._best_effort_cleanup(scenario_id, state)
        return results

    def _run_case(self, test_case: CatalogueTestCase, state: DcrScenarioState) -> list[StepResult]:
        """Execute one case sequentially and skip steps after its first failure.

        Args:
            test_case: Compiled DCR case to execute.
            state: Mutable state owned only by the parent scenario.

        Returns:
            Ordered result for every execution step in the case.
        """
        results: list[StepResult] = []
        failure_step: str | None = None
        for step in test_case.execution_steps:
            if failure_step is not None:
                result = self._skipped_result(test_case, step, f"earlier step {failure_step} failed")
            else:
                result = self._execute_step(test_case, step, state)
                if result.status != "passed":
                    failure_step = step.step_id
            results.append(result)
        return results

    def _execute_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Execute one typed DCR operation with shared lifecycle log events.

        Args:
            test_case: Catalogue case owning the operation.
            step: Protocol-neutral operation to execute.
            state: Scenario-local mutable protocol state.

        Returns:
            Shared step result with masked evidence.
        """
        self.execution_logger.emit("step-started", step_id=step.step_id)
        try:
            result = self._execute_step_inner(test_case, step, state)
        except (DcrExecutionError, JsonHttpClientError, HttpsUrlValidationError) as error:
            result = self._result(test_case, step, status="failed", message=str(error))
            self.execution_logger.emit("application-error", step_id=step.step_id, payload={"message": str(error)})
        self.execution_logger.emit(
            "step-completed",
            step_id=step.step_id,
            payload={
                "status": result.status,
                "message": result.message,
                **({"statusCode": result.status_code} if result.status_code is not None else {}),
            },
        )
        return result

    def _execute_step_inner(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Dispatch one DCR catalogue operation.

        Args:
            test_case: Catalogue case owning the operation.
            step: Operation to dispatch.
            state: Scenario-local mutable state.

        Returns:
            Completed shared result.

        Raises:
            DcrExecutionError: If the operation or required state is invalid.
            JsonHttpClientError: If an HTTP exchange fails.
        """
        handlers: Mapping[
            str,
            Callable[[CatalogueTestCase, CatalogueExecutionStep, DcrScenarioState], StepResult],
        ] = MappingProxyType(
            {
                "validate-registration-endpoint": self._validate_discovery_step,
                "generate-registration-jose": self._generate_registration_step,
                "post-registration": self._post_registration_step,
                "parse-registration-success": self._parse_registration_step,
                "request-client-credentials-token": self._token_step,
                "delete-client": self._delete_step,
                "get-client": self._get_step,
                "put-client": self._put_step,
                "validate-registration-response-34": self._validate_response_step,
                "parse-retrieved-client": self._parse_retrieved_step,
                "set-empty-bearer-token": self._empty_bearer_step,
                "validate-registration-error": self._validate_error_step,
            }
        )
        if step.definition_id.startswith("assert-status-"):
            return self._assert_status_step(test_case, step, state)
        handler = handlers.get(step.definition_id)
        if handler is None:
            raise DcrExecutionError(f"Unsupported DCR execution operation: {step.definition_id}")
        return handler(test_case, step, state)

    def _validate_discovery_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Fetch and validate strict discovery and JWKS metadata.

        Args:
            test_case: Owning catalogue case.
            step: Discovery assertion step.
            state: Unused scenario state.

        Returns:
            Passed result with non-sensitive discovery evidence.
        """
        del state
        discovery = self._require_discovery(step_id=step.step_id)
        return self._result(
            test_case,
            step,
            status="passed",
            message="DCR discovery registration, token, and JWKS endpoints are valid HTTPS URLs",
            url=self.config.shared.discovery_url,
            details={
                "discovery": {
                    "issuer": discovery.issuer,
                    "registrationEndpoint": discovery.registration_endpoint,
                    "tokenEndpoint": discovery.token_endpoint,
                    "jwksUri": discovery.jwks_uri,
                }
            },
        )

    def _generate_registration_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Generate the exact compact JOSE variant for a catalogue step.

        Args:
            test_case: Owning catalogue case.
            step: JOSE generation step.
            state: Scenario state receiving the signed body.

        Returns:
            Passed generation result without secret material.
        """
        compact, claims = self.build_registration_jose(variant=step.variant or "valid")
        state.signed_registration_jose = compact
        state.registration_claims = claims
        return self._result(
            test_case,
            step,
            status="passed",
            message=f"Generated compact PS256 registration JOSE variant {step.variant or 'valid'}",
            details={"algorithm": "PS256", "keyIdConfigured": True, "variant": step.variant or "valid"},
        )

    def _post_registration_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """POST the current compact registration JOSE.

        Args:
            test_case: Owning catalogue case.
            step: Registration HTTP step.
            state: Scenario state containing the signed JOSE.

        Returns:
            Transport result; the following assertion step checks status.
        """
        discovery = self._require_discovery()
        response, evidence = self._send(
            step,
            "POST",
            discovery.registration_endpoint,
            headers={"Accept": "application/json", "Content-Type": "application/jose"},
            raw_body=_required_state(state.signed_registration_jose, "signedRegistrationJose").encode(),
        )
        state.last_response = response
        state.registration_response = dict(response.body)
        return self._result(
            test_case,
            step,
            status="passed",
            message=f"Registration endpoint returned HTTP {response.status_code}",
            url=response.url,
            status_code=response.status_code,
            details=evidence,
        )

    def _parse_registration_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Capture dynamic client credentials from a successful response.

        Args:
            test_case: Owning catalogue case.
            step: Registration parsing step.
            state: Scenario state receiving dynamic values.

        Returns:
            Passed result containing only non-secret state-presence evidence.

        Raises:
            DcrExecutionError: If the response is absent or malformed.
        """
        response = _required_response(state)
        self._validate_registration_response(response.body, state, require_consistency=True)
        state.client_id = _required_string(response.body, "client_id", location="registration response")
        state.client_secret = _optional_string(response.body, "client_secret", location="registration response")
        state.registration_access_token = _optional_string(
            response.body,
            "registration_access_token",
            location="registration response",
        )
        registration_client_uri = _optional_string(
            response.body,
            "registration_client_uri",
            location="registration response",
        )
        state.management_url = registration_client_uri or (
            f"{self._require_discovery().registration_endpoint.rstrip('/')}/{state.client_id}"
        )
        return self._result(
            test_case,
            step,
            status="passed",
            message="Captured scenario-local dynamic client state",
            details={
                "clientId": state.client_id,
                "clientSecretCaptured": state.client_secret is not None,
                "managementUrl": state.management_url,
            },
        )

    def _token_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Request a form-encoded client-credentials token.

        Args:
            test_case: Owning catalogue case.
            step: Token HTTP step.
            state: Scenario state containing dynamic credentials.

        Returns:
            Token result with fully masked request and response evidence.
        """
        discovery = self._require_discovery()
        form, headers = self._token_request(state, discovery)
        response, evidence = self._send(step, "POST", discovery.token_endpoint, headers=headers, form_body=form)
        state.last_response = response
        state.token_response = dict(response.body)
        if response.status_code != 200:
            return self._result(
                test_case,
                step,
                status="failed",
                message=f"Client-credentials token endpoint returned HTTP {response.status_code}, expected 200",
                url=response.url,
                status_code=response.status_code,
                details=evidence,
            )
        state.client_credentials_access_token = _required_string(
            response.body,
            "access_token",
            location="token response",
        )
        return self._result(
            test_case,
            step,
            status="passed",
            message="Client-credentials access token acquired",
            url=response.url,
            status_code=response.status_code,
            details=evidence,
        )

    def _delete_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """DELETE the current dynamic registration.

        Args:
            test_case: Owning catalogue case.
            step: DELETE HTTP step.
            state: Scenario state containing management URL and bearer.

        Returns:
            Status-checked DELETE result.
        """
        response, evidence = self._management_request(step, state, "DELETE")
        state.last_response = response
        if response.status_code == 204:
            state.deleted = True
        passed = response.status_code == 204
        return self._result(
            test_case,
            step,
            status="passed" if passed else "failed",
            message=(
                "Dynamic registration deleted"
                if passed
                else f"DELETE returned HTTP {response.status_code}, expected 204"
            ),
            url=response.url,
            status_code=response.status_code,
            details=evidence,
        )

    def _get_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """GET the current dynamic registration.

        Args:
            test_case: Owning catalogue case.
            step: GET HTTP step.
            state: Scenario state containing management URL and bearer.

        Returns:
            Transport result for the following status/schema assertions.
        """
        response, evidence = self._management_request(step, state, "GET")
        state.last_response = response
        state.registration_response = dict(response.body)
        return self._result(
            test_case,
            step,
            status="passed",
            message=f"Registration management GET returned HTTP {response.status_code}",
            url=response.url,
            status_code=response.status_code,
            details=evidence,
        )

    def _put_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """PUT the current compact JOSE to the dynamic management URL.

        Args:
            test_case: Owning catalogue case.
            step: PUT HTTP step.
            state: Scenario state containing JOSE, URL, and bearer.

        Returns:
            Transport result for the following status assertion.
        """
        response, evidence = self._management_request(
            step,
            state,
            "PUT",
            raw_body=_required_state(state.signed_registration_jose, "signedRegistrationJose").encode(),
        )
        state.last_response = response
        state.registration_response = dict(response.body)
        if response.status_code == 200:
            self._validate_registration_response(response.body, state, require_consistency=True)
        return self._result(
            test_case,
            step,
            status="passed",
            message=f"Registration management PUT returned HTTP {response.status_code}",
            url=response.url,
            status_code=response.status_code,
            details=evidence,
        )

    def _assert_status_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Assert the locked HTTP status against the latest response.

        Args:
            test_case: Owning catalogue case.
            step: Locked status assertion.
            state: Scenario state containing the latest response.

        Returns:
            Passed or failed assertion result.

        Raises:
            DcrExecutionError: If no response or expected status is available.
        """
        response = _required_response(state)
        if step.expected_status is None:
            raise DcrExecutionError(f"DCR status assertion {step.step_id} has no expected status")
        passed = response.status_code == step.expected_status
        assertion_detail: JsonObject = {
            "status": "passed" if passed else "failed",
            "message": f"Expected HTTP {step.expected_status}; received HTTP {response.status_code}",
        }
        details: JsonObject = {"assertions": [assertion_detail]}
        self.execution_logger.emit(
            "assertion-evaluated",
            step_id=step.step_id,
            payload=assertion_detail,
        )
        return self._result(
            test_case,
            step,
            status="passed" if passed else "failed",
            message=(
                f"HTTP status is {step.expected_status}"
                if passed
                else f"HTTP status {response.status_code} did not match {step.expected_status}"
            ),
            url=response.url,
            status_code=response.status_code,
            details=details,
        )

    def _validate_response_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Apply host-independent DCR 3.4 response validation.

        Args:
            test_case: Owning catalogue case.
            step: DCR response validation step.
            state: Scenario state containing the retrieved response.

        Returns:
            Passed schema/semantic validation result.

        Raises:
            DcrExecutionError: If any DCR 3.4 response rule fails.
        """
        response = _required_response(state)
        self._validate_registration_response(response.body, state, require_consistency=True)
        return self._result(
            test_case,
            step,
            status="passed",
            message="Registration response satisfies DCR 3.4 schema and cross-field rules",
            url=response.url,
            status_code=response.status_code,
        )

    def _parse_retrieved_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Check a retrieved client is consistent with scenario registration.

        Args:
            test_case: Owning catalogue case.
            step: Retrieved-client semantic assertion.
            state: Scenario state containing registered and retrieved values.

        Returns:
            Passed consistency result.

        Raises:
            DcrExecutionError: If client or token-auth metadata changed.
        """
        response = _required_response(state)
        if response.body.get("client_id") != state.client_id:
            raise DcrExecutionError("Retrieved DCR client_id does not match scenario-local registration state")
        if response.body.get("token_endpoint_auth_method") != self._auth_method():
            raise DcrExecutionError("Retrieved token_endpoint_auth_method does not match the registered method")
        return self._result(
            test_case,
            step,
            status="passed",
            message="Retrieved client is consistent with registration and token metadata",
            url=response.url,
            status_code=response.status_code,
        )

    def _empty_bearer_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Set an explicit empty bearer for the negative management request.

        Args:
            test_case: Owning catalogue case.
            step: State mutation step.
            state: Scenario state receiving the empty bearer.

        Returns:
            Passed non-secret state transition result.
        """
        state.client_credentials_access_token = ""
        return self._result(
            test_case,
            step,
            status="passed",
            message="Configured an empty bearer token for the negative request",
        )

    def _validate_error_step(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
    ) -> StepResult:
        """Validate an RFC 7591 error without mutating registration state.

        Args:
            test_case: Owning catalogue case.
            step: Registration-error assertion.
            state: Scenario state containing the error response.

        Returns:
            Passed structured-error result.

        Raises:
            DcrExecutionError: If the response is not an error object.
        """
        response = _required_response(state)
        _required_string(response.body, "error", location="registration error response")
        if "client_id" in response.body:
            raise DcrExecutionError("Registration error response must not be decoded as successful client state")
        return self._result(
            test_case,
            step,
            status="passed",
            message="Registration error response is a valid RFC 7591 error",
            url=response.url,
            status_code=response.status_code,
        )

    def _management_request(
        self,
        step: CatalogueExecutionStep,
        state: DcrScenarioState,
        method: str,
        *,
        raw_body: bytes | None = None,
    ) -> tuple[JsonHttpResponse, JsonObject]:
        """Send one authenticated dynamic registration management request.

        Args:
            step: Catalogue HTTP step used for evidence correlation.
            state: Scenario state containing the dynamic URL and bearer token.
            method: GET, PUT, or DELETE.
            raw_body: Optional compact JOSE bytes for PUT.

        Returns:
            Shared JSON HTTP response and masked evidence.

        Raises:
            DcrExecutionError: If dynamic state is missing.
            JsonHttpClientError: If transport or response parsing fails.
        """
        management_url = _required_state(state.management_url, "registration management URL")
        token = _required_state_allow_empty(
            state.client_credentials_access_token,
            "clientCredentialsAccessToken",
        )
        authorization = "Bearer" if not token else f"Bearer {token}"
        headers = {"Accept": "application/json", "Authorization": authorization}
        if raw_body is not None:
            headers["Content-Type"] = "application/jose"
        return self._send(step, method, management_url, headers=headers, raw_body=raw_body)

    def _send(
        self,
        step: CatalogueExecutionStep,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        form_body: Mapping[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[JsonHttpResponse, JsonObject]:
        """Send through the shared HTTP helper and build masked evidence.

        Args:
            step: Catalogue step used for execution-log correlation.
            method: HTTP method.
            url: Strictly validated HTTPS target.
            headers: Optional outbound headers.
            form_body: Optional OAuth form body.
            raw_body: Optional raw compact JOSE bytes.

        Returns:
            Parsed response and evidence safe for persistence/export.

        Raises:
            DcrExecutionError: If no client is active.
            HttpsUrlValidationError: If the target is not HTTPS.
            JsonHttpClientError: If transport or response parsing fails.
        """
        validate_https_url(url, label=f"DCR step {step.step_id} URL")
        if self.client is None:
            raise DcrExecutionError("DCR mTLS client is not active")
        request_evidence: JsonObject = {"method": method, "url": url}
        if headers:
            request_evidence["headers"] = cast(JsonObject, mask_headers(headers))
        if form_body is not None:
            request_evidence["form"] = cast(JsonObject, mask_form_fields(form_body))
        if raw_body is not None:
            request_evidence["signedRegistrationJose"] = MASKED_VALUE
        self.execution_logger.emit("request-sent", step_id=step.step_id, payload=request_evidence)
        response = send_json(
            self.client,
            method,
            url,
            headers=dict(headers) if headers is not None else None,
            form_body=form_body,
            raw_body=raw_body,
            allow_non_json_response=True,
        )
        self.execution_logger.emit(
            "response-received",
            step_id=step.step_id,
            payload={"statusCode": response.status_code, "url": response.url},
        )
        response_evidence: JsonObject = {
            "statusCode": response.status_code,
            "body": mask_json_value(dict(response.body)),
        }
        if response.headers:
            response_evidence["headers"] = cast(JsonObject, mask_headers(response.headers))
        return response, {"request": request_evidence, "response": response_evidence}

    def _token_request(
        self,
        state: DcrScenarioState,
        discovery: DcrDiscoveryMetadata,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Build one supported client-credentials authentication request.

        Args:
            state: Scenario-local dynamic client credentials.
            discovery: Validated token endpoint metadata.

        Returns:
            Form fields and HTTP headers.

        Raises:
            DcrExecutionError: If credentials are absent or the method is unsupported.
        """
        client_id = _required_state(state.client_id, "clientId")
        method = self._auth_method()
        if method in {"client_secret_jwt", "client_secret_basic"} and not state.client_secret:
            raise DcrExecutionError(
                f"Registration response omitted client_secret required for {method} token authentication"
            )
        form = {"grant_type": "client_credentials"}
        headers: dict[str, str] = {"Accept": "application/json"}
        if method == "tls_client_auth":
            form["client_id"] = client_id
        elif method == "client_secret_basic":
            client_secret = _required_state(state.client_secret, "clientSecret")
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif method == "private_key_jwt":
            form.update(self._client_assertion_form(client_id, discovery, method))
        elif method == "client_secret_jwt":
            form.update(self._client_assertion_form(client_id, discovery, method, secret=state.client_secret))
        else:
            raise DcrExecutionError(f"Unsupported DCR token auth method: {method}")
        return form, headers

    def _client_assertion_form(
        self,
        client_id: str,
        discovery: DcrDiscoveryMetadata,
        method: ClientAuthMethod,
        *,
        secret: str | None = None,
    ) -> dict[str, str]:
        """Sign a private-key or client-secret JWT assertion.

        Args:
            client_id: Dynamic OAuth client identifier.
            discovery: Validated token endpoint metadata.
            method: Assertion-based authentication method.
            secret: Dynamic secret required by ``client_secret_jwt``.

        Returns:
            OAuth client assertion form fields.

        Raises:
            DcrExecutionError: If signing material or JOSE processing fails.
        """
        now = self._utc_now()
        if method == "private_key_jwt":
            algorithm = "PS256"
            key = self._load_signing_key()
            key_id = self.config.shared.signing.key_id
        elif method == "client_secret_jwt":
            algorithm = "HS256"
            key = jwk.import_key(_required_state(secret, "clientSecret"), "oct")
            key_id = None
        else:
            raise DcrExecutionError(f"DCR auth method {method} does not use a client assertion")
        if algorithm not in discovery.token_auth_signing_algorithms:
            raise DcrExecutionError(f"Discovery does not advertise required {algorithm} client assertion signing")
        header: dict[str, str] = {"alg": algorithm, "typ": "JWT"}
        if key_id is not None:
            header["kid"] = key_id
        claims = {
            "iss": client_id,
            "sub": client_id,
            "aud": discovery.token_endpoint,
            "iat": int(now.timestamp()),
            "exp": int((now + _JWT_LIFETIME).timestamp()),
            "jti": self._jwt_id(),
        }
        try:
            assertion = jwt.encode(header, claims, key, algorithms=[algorithm])
        except (JoseError, TypeError, ValueError) as error:
            raise DcrExecutionError("Unable to sign DCR token client assertion") from error
        return {
            "client_id": client_id,
            "client_assertion_type": _ASSERTION_TYPE,
            "client_assertion": assertion,
        }

    def _require_discovery(self, *, step_id: str | None = None) -> DcrDiscoveryMetadata:
        """Return cached strict discovery metadata, fetching it once.

        Args:
            step_id: Optional catalogue step id used for discovery log evidence.

        Returns:
            Validated metadata and JWKS.

        Raises:
            DcrExecutionError: If discovery metadata or JWKS is invalid.
            JsonHttpClientError: If either HTTPS request fails.
        """
        if self._discovery is not None:
            return self._discovery
        discovery_url = self.config.shared.discovery_url
        if discovery_url is None:
            raise DcrExecutionError("DCR discovery URL is required")
        validate_https_url(discovery_url, label="DCR discovery URL")
        if self.client is None:
            raise DcrExecutionError("DCR mTLS client is not active")
        correlation_id = step_id or "dcr-discovery"
        self.execution_logger.emit(
            "request-sent",
            step_id=correlation_id,
            payload={"method": "GET", "url": discovery_url},
        )
        response = send_json(self.client, "GET", discovery_url)
        self.execution_logger.emit(
            "response-received",
            step_id=correlation_id,
            payload={"statusCode": response.status_code, "url": response.url},
        )
        if response.status_code != 200:
            raise DcrExecutionError(f"DCR discovery returned HTTP {response.status_code}, expected 200")
        issuer = _validated_endpoint(response.body, "issuer", location="discovery")
        registration_endpoint = _validated_endpoint(response.body, "registration_endpoint", location="discovery")
        token_endpoint = _validated_endpoint(response.body, "token_endpoint", location="discovery")
        jwks_uri = _validated_endpoint(response.body, "jwks_uri", location="discovery")
        methods = _required_string_array(
            response.body,
            "token_endpoint_auth_methods_supported",
            location="discovery",
        )
        auth_method = self._auth_method()
        if auth_method not in methods:
            raise DcrExecutionError(f"Discovery does not advertise configured DCR auth method {auth_method}")
        algorithms = _required_string_array(
            response.body,
            "token_endpoint_auth_signing_alg_values_supported",
            location="discovery",
        )
        required_algorithm = "PS256" if auth_method == "private_key_jwt" else "HS256"
        if auth_method in {"private_key_jwt", "client_secret_jwt"} and required_algorithm not in algorithms:
            raise DcrExecutionError(
                f"Discovery does not advertise required {required_algorithm} client assertion signing"
            )
        configured_algorithm = self.config.shared.signing.algorithm
        if (
            auth_method in {"private_key_jwt", "client_secret_jwt"}
            and configured_algorithm is not None
            and configured_algorithm != required_algorithm
        ):
            raise DcrExecutionError(
                f"Configured DCR client-auth signing algorithm must be {required_algorithm} for {auth_method}"
            )
        if configured_algorithm is not None and configured_algorithm not in algorithms:
            raise DcrExecutionError(
                f"Discovery does not advertise configured client-auth signing algorithm {configured_algorithm}"
            )

        self.execution_logger.emit(
            "request-sent",
            step_id=correlation_id,
            payload={"method": "GET", "url": jwks_uri},
        )
        jwks_response = send_json(self.client, "GET", jwks_uri)
        self.execution_logger.emit(
            "response-received",
            step_id=correlation_id,
            payload={"statusCode": jwks_response.status_code, "url": jwks_response.url},
        )
        if jwks_response.status_code != 200:
            raise DcrExecutionError(f"DCR JWKS returned HTTP {jwks_response.status_code}, expected 200")
        _validate_jwks(jwks_response.body)
        self._discovery = DcrDiscoveryMetadata(
            issuer=issuer,
            registration_endpoint=registration_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
            token_auth_methods=methods,
            token_auth_signing_algorithms=algorithms,
            jwks=dict(jwks_response.body),
        )
        return self._discovery

    def _validate_registration_response(
        self,
        body: Mapping[str, JsonValue],
        state: DcrScenarioState,
        *,
        require_consistency: bool,
    ) -> None:
        """Validate DCR 3.4 response schema and cross-field semantics.

        Args:
            body: Registration or management response body.
            state: Scenario state containing request and client values.
            require_consistency: Whether request/response values must agree.

        Raises:
            DcrExecutionError: If any required DCR 3.4 rule fails.
        """
        client_id = _required_string(body, "client_id", location="registration response")
        if len(client_id) > 36:
            raise DcrExecutionError("Registration response client_id must contain 1 to 36 characters")
        registration_uri: str | None = None
        if "registration_client_uri" in body:
            registration_uri = _validated_endpoint(body, "registration_client_uri", location="registration response")
        _optional_string(body, "registration_access_token", location="registration response")
        method = _required_string(body, "token_endpoint_auth_method", location="registration response")
        if method not in _DCR_AUTH_METHODS:
            raise DcrExecutionError("Registration response contains an unexecutable token auth method")
        if method != self._auth_method():
            raise DcrExecutionError("Registration response token auth method differs from the request")
        for timestamp_field in ("client_id_issued_at", "client_secret_expires_at"):
            if timestamp_field in body:
                timestamp = body[timestamp_field]
                if not isinstance(timestamp, int) or isinstance(timestamp, bool):
                    raise DcrExecutionError(f"Registration response {timestamp_field} must be an integer")
        client_secret = _optional_string(body, "client_secret", location="registration response")
        if client_secret is not None and len(client_secret) > 36:
            raise DcrExecutionError("Registration response client_secret must contain 1 to 36 characters")
        application_type = _required_string(body, "application_type", location="registration response")
        if application_type not in {"web", "native"}:
            raise DcrExecutionError("Registration response application_type must be web or native")
        redirects = _required_string_array(body, "redirect_uris", location="registration response")
        if len(set(redirects)) != len(redirects):
            raise DcrExecutionError("Registration response redirect_uris must not contain duplicates")
        for redirect in redirects:
            if len(redirect) > 256:
                raise DcrExecutionError("Registration response redirect_uris entries must not exceed 256 characters")
            try:
                validate_oauth_redirect_uri(redirect, label="registration response redirect_uri")
            except HttpsUrlValidationError as error:
                raise DcrExecutionError(str(error)) from error
        grant_types = _required_string_array(body, "grant_types", location="registration response")
        allowed_grants = {
            "authorization_code",
            "client_credentials",
            "refresh_token",
            "urn:openid:params:grant-type:ciba",
            "urn:ietf:params:oauth:grant-type:jwt-bearer",
        }
        if any(grant not in allowed_grants or len(grant) > 128 for grant in grant_types):
            raise DcrExecutionError("Registration response grant_types violates DCR 3.4 constraints")
        if len(set(grant_types)) != len(grant_types):
            raise DcrExecutionError("Registration response grant_types must not contain duplicates")
        response_types = _optional_string_array(body, "response_types", location="registration response")
        allowed_response_types = {"code", "code id_token"}
        if any(
            response_type not in allowed_response_types or len(response_type) > 32 for response_type in response_types
        ):
            raise DcrExecutionError("Registration response response_types violates DCR 3.4 constraints")
        if len(set(response_types)) != len(response_types):
            raise DcrExecutionError("Registration response response_types must not contain duplicates")
        scope = _required_string(body, "scope", location="registration response")
        if len(scope) > 256:
            raise DcrExecutionError("Registration response scope must contain 1 to 256 characters")
        _required_string(body, "software_statement", location="registration response")
        software_id = body.get("software_id")
        if software_id is not None and (
            not isinstance(software_id, str) or re.fullmatch(r"[0-9A-Za-z]{1,22}", software_id) is None
        ):
            raise DcrExecutionError("Registration response software_id must be 1 to 22 alphanumeric characters")
        id_token_algorithm = _required_jose_algorithm(body, "id_token_signed_response_alg")
        request_object_algorithm = _required_jose_algorithm(body, "request_object_signing_alg")
        registered_grants = frozenset(grant_types)
        registered_response_types = frozenset(response_types or ("code id_token",))
        if "authorization_code" in registered_grants and not any(
            "code" in response_type.split() for response_type in registered_response_types
        ):
            raise DcrExecutionError("Registration response grant_types and response_types are inconsistent")
        if method == "tls_client_auth":
            subject_dn = _required_string(body, "tls_client_auth_subject_dn", location="registration response")
            validate_dcr_subject_dn(subject_dn)
        if method in {"private_key_jwt", "client_secret_jwt"}:
            algorithm = _required_string(body, "token_endpoint_auth_signing_alg", location="registration response")
            expected_algorithm = "PS256" if method == "private_key_jwt" else "HS256"
            if algorithm != expected_algorithm:
                raise DcrExecutionError(
                    f"Registration response token auth signing algorithm must be {expected_algorithm}"
                )
        discovery = self._require_discovery()
        expected_management_uri = f"{discovery.registration_endpoint.rstrip('/')}/{client_id}"
        if registration_uri is not None and registration_uri != expected_management_uri:
            raise DcrExecutionError(
                "registration_client_uri must be the discovered registration endpoint plus client_id"
            )
        if require_consistency and state.registration_claims is not None:
            for claim_name in ("application_type", "token_endpoint_auth_method"):
                if body.get(claim_name) != state.registration_claims.get(claim_name):
                    raise DcrExecutionError(f"Registration response {claim_name} differs from the signed request")
            for claim_name, response_value in (
                ("scope", scope),
                ("id_token_signed_response_alg", id_token_algorithm),
                ("request_object_signing_alg", request_object_algorithm),
            ):
                if response_value != state.registration_claims.get(claim_name):
                    raise DcrExecutionError(f"Registration response {claim_name} differs from the signed request")
            if method == "tls_client_auth" and subject_dn != state.registration_claims.get(
                "tls_client_auth_subject_dn"
            ):
                raise DcrExecutionError(
                    "Registration response tls_client_auth_subject_dn differs from the signed request"
                )
            # RFC 7591 permits metadata replacement; OB DCR 3.4 narrows only
            # registered redirects to the master set asserted by the SSA.
            software_statement = _required_string(
                state.registration_claims,
                "software_statement",
                location="signed registration request",
            )
            statement_claims = decode_compact_jwt_claims(software_statement)
            statement_software_id = _required_string(statement_claims, "software_id", location="software statement")
            if software_id is not None and software_id != statement_software_id:
                raise DcrExecutionError("Registration response software_id differs from the software statement")
            statement_redirects = frozenset(
                _required_string_array(
                    statement_claims,
                    "software_redirect_uris",
                    location="software statement",
                )
            )
            if not frozenset(redirects) <= statement_redirects:
                raise DcrExecutionError(
                    "Registration response redirect_uris must match or be a subset of the software statement"
                )

    def _best_effort_cleanup(self, scenario_id: str, state: DcrScenarioState) -> None:
        """Attempt deterministic cleanup without changing scenario results.

        Args:
            scenario_id: Parent scenario id used for log correlation.
            state: Scenario-local client state to clean up.
        """
        selected_delete = any(
            endpoint.method == "DELETE" for endpoint in self.compiled_plan.traceability.selected_endpoints
        )
        if (
            not selected_delete
            or state.deleted
            or state.management_url is None
            or not state.client_credentials_access_token
            or self.client is None
        ):
            return
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {state.client_credentials_access_token}",
        }
        self.execution_logger.emit(
            "request-sent",
            step_id=f"{scenario_id}-cleanup",
            payload={
                "method": "DELETE",
                "url": state.management_url,
                "headers": cast(JsonObject, mask_headers(headers)),
                "bestEffort": True,
            },
        )
        try:
            response = send_json(
                self.client,
                "DELETE",
                state.management_url,
                headers=headers,
                allow_non_json_response=True,
            )
        except JsonHttpClientError as error:
            self.execution_logger.emit(
                "application-error",
                step_id=f"{scenario_id}-cleanup",
                payload={"message": f"Best-effort DCR cleanup failed: {error}"},
            )
            return
        self.execution_logger.emit(
            "response-received",
            step_id=f"{scenario_id}-cleanup",
            payload={"statusCode": response.status_code, "url": response.url, "bestEffort": True},
        )
        if response.status_code == 204:
            state.deleted = True

    def _result(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        *,
        status: CheckStatus,
        message: str,
        url: str | None = None,
        status_code: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> StepResult:
        """Build a shared step result with generic catalogue traceability.

        Args:
            test_case: Owning catalogue case.
            step: Executed catalogue operation.
            status: Shared result status.
            message: Human-readable non-sensitive outcome.
            url: Optional HTTPS request URL.
            status_code: Optional HTTP response status.
            details: Optional already-masked evidence.

        Returns:
            Immutable shared step result.
        """
        trace_group = test_case.trace_group
        catalogue: JsonObject = {
            "testCaseId": test_case.test_case_id,
            "executionStepId": step.step_id,
            "role": test_case.role,
            "complianceScope": list(test_case.compliance_scope),
        }
        if trace_group is not None:
            catalogue["traceGroupId"] = trace_group.group_id
        result_details: dict[str, JsonValue] = dict(details or {})
        result_details["catalogue"] = catalogue
        return StepResult(
            name=step.step_id,
            status=status,
            message=message,
            url=url,
            status_code=status_code,
            details=result_details,
            mandatory=test_case.mandatory,
        )

    def _skipped_result(
        self,
        test_case: CatalogueTestCase,
        step: CatalogueExecutionStep,
        reason: str,
    ) -> StepResult:
        """Build and log an explicit dependency-aware skipped result.

        Args:
            test_case: Owning catalogue case.
            step: Catalogue operation that cannot run.
            reason: Non-sensitive prerequisite failure explanation.

        Returns:
            Shared skipped step result.
        """
        self.execution_logger.emit("step-started", step_id=step.step_id)
        result = self._result(
            test_case,
            step,
            status="skipped",
            message=f"Skipped: {reason}",
            details={"skipReason": "failed-prerequisite"},
        )
        self.execution_logger.emit(
            "step-completed",
            step_id=step.step_id,
            payload={"status": "skipped", "message": result.message},
        )
        return result

    def _auth_method(self) -> ClientAuthMethod:
        """Return the configured executable token auth method.

        Returns:
            One of the four adapter-supported methods.

        Raises:
            DcrExecutionError: If configuration is missing or unsupported.
        """
        method = self.config.shared.client_auth_method
        if method is None or method not in _DCR_AUTH_METHODS:
            raise DcrExecutionError("DCR client auth method must be one of the four executable methods")
        return method

    def _load_signing_key(self) -> jwk.Key:
        """Load and cache the configured RSA private signing key.

        Returns:
            JOSE RSA private key.

        Raises:
            DcrExecutionError: If the key cannot be read or imported.
        """
        if self._signing_key is not None:
            return self._signing_key
        path = self.config.shared.signing.private_key_path
        if path is None:
            raise DcrExecutionError("DCR signing private key path is required")
        try:
            key_bytes = path.read_bytes()
            key = jwk.import_key(key_bytes, key_type="RSA")
            key.as_dict(private=True)
        except (OSError, InvalidKeyTypeError, TypeError, ValueError) as error:
            raise DcrExecutionError("Unable to load configured DCR RSA private signing key") from error
        self._signing_key = key
        return key

    def _read_ssa(self) -> str:
        """Read the configured software statement without exposing it in errors.

        Returns:
            Non-empty compact SSA string.

        Raises:
            DcrExecutionError: If the file cannot be read or is empty.
        """
        try:
            value = self.config.dynamic_client_registration.software_statement_assertion_path.read_text(
                encoding="utf-8"
            ).strip()
        except OSError as error:
            raise DcrExecutionError("Unable to read configured software statement assertion") from error
        if not value:
            raise DcrExecutionError("Configured software statement assertion must not be empty")
        return value

    def _utc_now(self) -> datetime:
        """Return a timezone-aware UTC JOSE timestamp.

        Returns:
            Current injected time converted to UTC.

        Raises:
            DcrExecutionError: If the injected clock returns a naive datetime.
        """
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise DcrExecutionError("DCR signing clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _jwt_id(self) -> str:
        """Return a non-empty unique JWT identifier.

        Returns:
            Generated identifier.

        Raises:
            DcrExecutionError: If the factory returns an empty value.
        """
        value = self.jwt_id_factory().strip()
        if not value:
            raise DcrExecutionError("DCR JWT id factory must return a non-empty string")
        return value

    def _validate_plan_boundary(self) -> None:
        """Reject non-DCR plans and catalogue/runtime parity drift.

        Raises:
            DcrExecutionError: If the compiled plan is not DCR 3.4, contains
                duplicate identifiers, violates endpoint gates, or a full
                selection differs from the pinned 10/34/79 inventory.
        """
        key = self.compiled_plan.catalogue_key
        if key.api not in {"dcr", "dynamic-client-registration"} or key.version not in {"v3.4", "3.4"}:
            raise DcrExecutionError("DCR execution adapter requires the Open Banking DCR 3.4 catalogue")
        selected_refs = {
            (endpoint.method, endpoint.path) for endpoint in self.compiled_plan.traceability.selected_endpoints
        }
        case_ids = [test_case.test_case_id for test_case in self.compiled_plan.test_cases]
        step_ids = [step.step_id for test_case in self.compiled_plan.test_cases for step in test_case.execution_steps]
        if len(case_ids) != len(set(case_ids)) or len(step_ids) != len(set(step_ids)):
            raise DcrExecutionError("Compiled DCR catalogue contains duplicate case or step identifiers")
        for test_case in self.compiled_plan.test_cases:
            required_refs = {(endpoint.method, endpoint.path) for endpoint in test_case.applicability.endpoint_refs}
            if not required_refs <= selected_refs:
                raise DcrExecutionError(
                    f"Compiled DCR case {test_case.test_case_id} violates participant endpoint selection"
                )
        selected_methods = {method for method, _path in selected_refs}
        if selected_methods == {"POST", "GET", "PUT", "DELETE"}:
            scenario_ids = {
                test_case.trace_group.group_id
                for test_case in self.compiled_plan.test_cases
                if test_case.trace_group is not None
            }
            if len(scenario_ids) != 10 or len(case_ids) != 34 or len(step_ids) != 79:
                raise DcrExecutionError(
                    "Full DCR endpoint selection must compile exactly 10 scenarios, 34 cases, 79 steps"
                )


def build_dcr_mtls_client(config: DcrPlanConfiguration, *, timeout_seconds: float = 10.0) -> httpx.Client:
    """Build the hardened mTLS transport used by every DCR request.

    Args:
        config: Validated DCR configuration containing certificate references.
        timeout_seconds: Per-request timeout.

    Returns:
        Caller-owned synchronous HTTP client with environment proxies disabled.

    Raises:
        DcrExecutionError: If TLS material cannot be loaded.
    """
    tls = config.shared.mtls
    certificate_path = tls.client_certificate_path
    private_key_path = tls.client_private_key_path
    if certificate_path is None or private_key_path is None:
        raise DcrExecutionError("DCR mTLS certificate and private key are required")
    try:
        context = ssl.create_default_context()
        if tls.ca_bundle_path is not None:
            context.load_verify_locations(cafile=str(tls.ca_bundle_path))
        context.load_cert_chain(certfile=str(certificate_path), keyfile=str(private_key_path))
    except (OSError, ssl.SSLError) as error:
        raise DcrExecutionError("Unable to load configured DCR mTLS credentials or CA bundle") from error
    disable_keep_alive = config.dynamic_client_registration.disable_keep_alive
    limits = httpx.Limits(max_keepalive_connections=0) if disable_keep_alive else httpx.Limits()
    headers = {"Connection": "close"} if disable_keep_alive else None
    return httpx.Client(
        verify=context,
        timeout=timeout_seconds,
        limits=limits,
        headers=headers,
        trust_env=False,
    )


def certificate_subject_dn(path: Path | None, *, override: str | None, numeric_oids: bool) -> str:
    """Derive a DCR RFC 2253 subject DN from an mTLS certificate.

    Args:
        path: Certificate path supplied by typed mTLS configuration.
        override: Exact configured subject-DN override, when present.
        numeric_oids: Whether derived attribute names use dotted numeric OIDs.

    Returns:
        Validated RFC 2253-style subject DN.

    Raises:
        DcrExecutionError: If the path, certificate, or DN is invalid.
    """
    if override is not None:
        validate_dcr_subject_dn(override)
        return override
    if path is None:
        raise DcrExecutionError("DCR mTLS certificate path is required for subject-DN derivation")
    try:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError) as error:
        raise DcrExecutionError("Unable to parse configured DCR mTLS certificate") from error
    overrides = (
        {attribute.oid: attribute.oid.dotted_string for attribute in certificate.subject} if numeric_oids else None
    )
    value = certificate.subject.rfc4514_string(attr_name_overrides=overrides)
    validate_dcr_subject_dn(value)
    return value


def validate_dcr_subject_dn(value: str) -> None:
    """Validate the DCR 3.4 512-character RFC 2253 DN boundary.

    Args:
        value: Subject DN to validate.

    Raises:
        DcrExecutionError: If length, controls, syntax, or attribute values fail.
    """
    if not value or len(value) > 512 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DcrExecutionError("DCR tls_client_auth_subject_dn must contain 1 to 512 printable characters")
    if _DCR_DN_PATTERN.fullmatch(value) is None:
        raise DcrExecutionError("DCR tls_client_auth_subject_dn does not match the required DN pattern")
    for component in re.split(r"(?<!\\),", value):
        attribute_name, separator, attribute_value = component.partition("=")
        if not separator or not attribute_name.strip() or not attribute_value or attribute_value.endswith("\\"):
            raise DcrExecutionError("DCR tls_client_auth_subject_dn is not valid RFC 2253 syntax")


def decode_compact_jwt_claims(token: str) -> JsonObject:
    """Decode unverified SSA claims needed to build the registration request.

    Signature verification belongs to the receiving ASPSP because the canonical
    plan intentionally contains no SSA trust-anchor key. This helper validates
    only compact shape and JSON types and never treats the decoded SSA as trusted.

    Args:
        token: Compact software statement assertion.

    Returns:
        Decoded JSON claims object.

    Raises:
        DcrExecutionError: If compact or JSON structure is malformed.
    """
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise DcrExecutionError("Software statement assertion must be a compact three-part JWT")
    padding = "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(parts[1] + padding)
        claims: object = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DcrExecutionError("Software statement assertion payload must be a JSON object") from error
    if not isinstance(claims, dict):
        raise DcrExecutionError("Software statement assertion payload must be a JSON object")
    return cast(JsonObject, claims)


def _apply_registration_variant(claims: JsonObject, *, variant: str, now: datetime) -> None:
    """Apply one exact malformed/negative catalogue registration variant.

    Args:
        claims: Mutable valid registration claims.
        variant: Catalogue variant identifier.
        now: Current UTC signing time.

    Raises:
        DcrExecutionError: If the catalogue names an unsupported variant.
    """
    if variant == "valid":
        return
    variants: Mapping[str, tuple[str, JsonValue]] = {
        "expired-one-hour": ("exp", int((now - timedelta(hours=1)).timestamp())),
        "issuer-foo.is/invalid": ("iss", "foo.is/invalid"),
        "issuer-empty": ("iss", ""),
        "issuer-30-characters": ("iss", "123456789012345678901234567890"),
        "response-types-id_token-token": ("response_types", ["id_token", "token"]),
    }
    replacement = variants.get(variant)
    if replacement is None:
        raise DcrExecutionError(f"Unsupported DCR registration JOSE variant: {variant}")
    claims[replacement[0]] = replacement[1]


def _validated_endpoint(body: Mapping[str, JsonValue], key: str, *, location: str) -> str:
    """Extract and strictly validate one HTTPS metadata endpoint.

    Args:
        body: Metadata object.
        key: Required endpoint field.
        location: Error location.

    Returns:
        Absolute HTTPS URL without a fragment.

    Raises:
        DcrExecutionError: If the field is missing or unsafe.
    """
    value = _required_string(body, key, location=location)
    try:
        validate_https_url(value, label=f"{location}.{key}")
    except HttpsUrlValidationError as error:
        raise DcrExecutionError(str(error)) from error
    if urlsplit(value).fragment:
        raise DcrExecutionError(f"{location}.{key} must not include a URL fragment")
    return value


def _validate_jwks(body: Mapping[str, JsonValue]) -> None:
    """Validate the discovery JWKS shape and every advertised public key.

    Args:
        body: JWKS response body.

    Raises:
        DcrExecutionError: If keys are absent, malformed, or include private data.
    """
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys:
        raise DcrExecutionError("DCR JWKS response must contain a non-empty keys array")
    private_members = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}
    for index, key_data in enumerate(keys):
        if not isinstance(key_data, dict) or private_members.intersection(key_data):
            raise DcrExecutionError(f"DCR JWKS key {index} must be a public JSON object")
        if key_data.get("is_private") not in {None, False}:
            raise DcrExecutionError(f"DCR JWKS key {index} must not contain private key material")
        normalized_key_data = {name: value for name, value in key_data.items() if name != "is_private"}
        if any(
            not isinstance(value, str)
            and not (isinstance(value, list) and all(isinstance(item, str) for item in value))
            for value in normalized_key_data.values()
        ):
            raise DcrExecutionError(f"DCR JWKS key {index} contains unsupported member types")
        validation_key_data = dict(normalized_key_data)
        if validation_key_data.get("use") not in {"sig", "enc"}:
            # RFC 7517 permits collision-resistant extension values such as the OB Directory's "tls".
            validation_key_data.pop("use", None)
        try:
            jwk.import_key(cast(dict[str, str | list[str]], validation_key_data))
        except (InvalidKeyTypeError, JoseError, TypeError, ValueError) as error:
            raise DcrExecutionError(f"DCR JWKS key {index} is invalid") from error


def _required_string(body: Mapping[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string.

    Args:
        body: JSON object.
        key: Required field.
        location: Error location.

    Returns:
        Non-empty string.

    Raises:
        DcrExecutionError: If the field is absent or not a string.
    """
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise DcrExecutionError(f"{location}.{key} must be a non-empty string")
    return value


def _optional_string(body: Mapping[str, JsonValue], key: str, *, location: str) -> str | None:
    """Extract an optional non-empty string while rejecting explicit null.

    Args:
        body: JSON object.
        key: Optional field.
        location: Error location.

    Returns:
        Non-empty string, or ``None`` when the field is omitted.

    Raises:
        DcrExecutionError: If a present field is not a non-empty string.
    """
    if key not in body:
        return None
    value = body[key]
    if not isinstance(value, str) or not value:
        raise DcrExecutionError(f"{location}.{key} must be a non-empty string")
    return value


def _required_string_array(
    body: Mapping[str, JsonValue],
    key: str,
    *,
    location: str,
) -> tuple[str, ...]:
    """Extract a required non-empty string array.

    Args:
        body: JSON object.
        key: Required field.
        location: Error location.

    Returns:
        Ordered non-empty strings.

    Raises:
        DcrExecutionError: If the field is absent or malformed.
    """
    value = body.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise DcrExecutionError(f"{location}.{key} must be a non-empty string array")
    return tuple(cast(str, item) for item in value)


def _required_jose_algorithm(body: Mapping[str, JsonValue], key: str) -> str:
    """Extract a required compact JOSE algorithm identifier.

    Args:
        body: Registration response object.
        key: Required algorithm field.

    Returns:
        Validated JOSE algorithm identifier.

    Raises:
        DcrExecutionError: If the field is absent or violates the DCR 3.4
            one-to-five-character algorithm boundary.
    """
    algorithm = _required_string(body, key, location="registration response")
    if len(algorithm) > 5 or re.fullmatch(r"[A-Za-z0-9-]+", algorithm) is None:
        raise DcrExecutionError(f"Registration response {key} must be a valid 1 to 5 character JOSE algorithm")
    return algorithm


def _optional_string_array(
    body: Mapping[str, JsonValue],
    key: str,
    *,
    location: str,
) -> tuple[str, ...]:
    """Extract an optional string array while distinguishing omission from null.

    Args:
        body: JSON object.
        key: Optional field.
        location: Error location.

    Returns:
        Ordered strings, or an empty tuple when the field is omitted.

    Raises:
        DcrExecutionError: If a present field is not a string array.
    """
    if key not in body:
        return ()
    value = body[key]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise DcrExecutionError(f"{location}.{key} must be a string array")
    return tuple(cast(str, item) for item in value)


def _required_state(value: str | None, name: str) -> str:
    """Extract a required non-empty scenario-state string.

    Args:
        value: Candidate state value.
        name: State identifier used in errors.

    Returns:
        Non-empty state value.

    Raises:
        DcrExecutionError: If state is missing.
    """
    if value is None or not value:
        raise DcrExecutionError(f"DCR scenario state {name} is unavailable")
    return value


def _required_state_allow_empty(value: str | None, name: str) -> str:
    """Extract scenario state while preserving an intentional empty string.

    Args:
        value: Candidate state value.
        name: State identifier used in errors.

    Returns:
        State value, including an intentional empty string.

    Raises:
        DcrExecutionError: If state was never initialized.
    """
    if value is None:
        raise DcrExecutionError(f"DCR scenario state {name} is unavailable")
    return value


def _required_response(state: DcrScenarioState) -> JsonHttpResponse:
    """Return the most recent scenario HTTP response.

    Args:
        state: Scenario state.

    Returns:
        Most recent response.

    Raises:
        DcrExecutionError: If no response is available.
    """
    if state.last_response is None:
        raise DcrExecutionError("DCR scenario has no response for this assertion")
    return state.last_response
