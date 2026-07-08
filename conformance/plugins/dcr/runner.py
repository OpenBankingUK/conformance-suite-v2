"""DCR scenario runner: orchestrates all DCR conformance scenarios.

:class:`DcrRunner` is the top-level executor for Open Banking UK Dynamic
Client Registration conformance testing.  It:

1. Builds an mTLS HTTP client from the validated credential paths.
2. Fetches and validates the OIDC discovery document.
3. Parses the SSA to extract ``software_id`` and ``redirect_uris``.
4. Executes each applicable DCR scenario in order.
5. Collects :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`
   for every scenario including skipped ones.
6. Attempts a DELETE cleanup after all scenarios (when DELETE is advertised);
   cleanup failures are reported separately and do not affect scenario outcomes.

Execution order:

- DCR-001 (positive registration) — must pass before CRUD scenarios run.
- DCR-002 / DCR-003 (GET/PUT, if advertised) — after DCR-001.
- DCR-005 / DCR-007 / DCR-008 / DCR-009 (negative registration).
- DCR-011 (wrong client ID token).
- DCR-004 (DELETE, if advertised) — near end to keep client alive.
- DCR-010 (deleted client access) — only after DCR-004 succeeds.

All sensitive values (access tokens, ``client_secret``,
``registration_access_token``) are masked via :mod:`conformance.masking`
before any evidence is stored or returned.
"""

from __future__ import annotations

import logging
import ssl
import warnings
from dataclasses import dataclass
from uuid import uuid4

import httpx

from conformance.dcr.credentials import DcrCredentialPaths, DcrCredentials
from conformance.dcr.transport import DcrTransportConfig
from conformance.http import JsonHttpClientError, send_json
from conformance.json_types import JsonObject
from conformance.plugins.dcr.client_state import (
    DcrClientState,
    DcrScenarioResult,
    build_step_evidence,
    evidence_from_http_response,
    failed_result,
    parse_client_state,
    passed_result,
    skipped_result,
)
from conformance.plugins.dcr.discovery import DcrDiscoveryError, DcrDiscoveryResult, fetch_discovery
from conformance.plugins.dcr.registration import (
    DcrRegistrationJwtInput,
    build_negative_registration_jwt_expired_ssa,
    build_negative_registration_jwt_invalid_auth_method,
    build_negative_registration_jwt_wrong_issuer,
    build_negative_registration_jwt_wrong_response_type,
    build_registration_jwt,
    parse_ssa_claims,
)
from conformance.plugins.dcr.scenarios import ALL_SCENARIOS
from conformance.plugins.dcr.schema_validation import DcrSchemaValidationError, validate_dcr_registration_response
from conformance.plugins.dcr.token import DcrTokenError, request_token_wrong_client_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class DcrRunResult:
    """Overall result of a DCR conformance run.

    Attributes:
        discovery: The validated OIDC discovery result, or ``None`` when
            discovery failed before any scenario ran.
        scenario_results: Ordered list of results for all attempted scenarios.
        cleanup_attempted: ``True`` when a DELETE cleanup was attempted.
        cleanup_succeeded: ``True`` when the DELETE cleanup succeeded.
        cleanup_detail: Human-readable cleanup outcome description.
    """

    discovery: DcrDiscoveryResult | None
    scenario_results: list[DcrScenarioResult]
    cleanup_attempted: bool
    cleanup_succeeded: bool
    cleanup_detail: str


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class DcrRunner:
    """Orchestrates all DCR conformance scenarios for a single ASPSP target.

    Construct with validated credential paths, loaded credentials, transport
    config, and the ASPSP issuer URL, then call :meth:`run` to execute all
    applicable scenarios.

    The runner does not modify any state between runs; calling :meth:`run`
    multiple times on the same instance will attempt independent registration
    sequences each time.
    """

    def __init__(
        self,
        *,
        credential_paths: DcrCredentialPaths,
        credentials: DcrCredentials,
        transport_config: DcrTransportConfig,
        issuer_url: str,
        dcr_version: str,
        advertise_get: bool = True,
        advertise_put: bool = True,
        advertise_delete: bool = True,
    ) -> None:
        """Initialise the DCR runner.

        Args:
            credential_paths: Validated file paths for mTLS and signing material.
            credentials: In-memory credential bytes loaded at run time.
            transport_config: TLS and timeout options for the HTTP client.
            issuer_url: ASPSP issuer URL (used to construct discovery URL).
            dcr_version: DCR specification version string (e.g. ``"3.3"``).
            advertise_get: Whether to attempt GET /register/{clientId} scenarios.
            advertise_put: Whether to attempt PUT /register/{clientId} scenarios.
            advertise_delete: Whether to attempt DELETE /register/{clientId} scenarios.
        """
        self._credential_paths = credential_paths
        self._credentials = credentials
        self._transport_config = transport_config
        self._issuer_url = issuer_url
        self._dcr_version = dcr_version
        self._advertise_get = advertise_get
        self._advertise_put = advertise_put
        self._advertise_delete = advertise_delete

    def run(self) -> DcrRunResult:
        """Execute all applicable DCR conformance scenarios and return results.

        Builds the HTTP client, fetches OIDC discovery, and runs each scenario
        in the prescribed order.  Every scenario produces a
        :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`
        regardless of whether it passed, failed, or was skipped.

        Returns:
            A :class:`DcrRunResult` containing scenario results and cleanup
            outcome.
        """
        results: list[DcrScenarioResult] = []

        with self._build_http_client() as client:
            # Step 1: OIDC discovery.
            try:
                discovery = fetch_discovery(client, self._issuer_url)
            except DcrDiscoveryError as exc:
                logger.error("DCR discovery failed: %s", exc)
                # All scenarios are skipped when discovery fails.
                for scenario in ALL_SCENARIOS:
                    results.append(skipped_result(scenario.scenario_id, f"Discovery failed: {exc}"))
                return DcrRunResult(
                    discovery=None,
                    scenario_results=results,
                    cleanup_attempted=False,
                    cleanup_succeeded=False,
                    cleanup_detail="Discovery failed; cleanup not attempted",
                )

            # Step 2: parse SSA claims.
            ssa_claims = self._parse_ssa()
            software_id = str(ssa_claims.get("software_id", "unknown-software-id"))
            redirect_uris_raw = ssa_claims.get("software_redirect_uris", [])
            redirect_uris: list[str] = (
                [str(u) for u in redirect_uris_raw if isinstance(u, str)] if isinstance(redirect_uris_raw, list) else []
            )
            if not redirect_uris:
                redirect_uris = ["https://tpp.example.com/callback"]
                logger.warning("SSA contains no software_redirect_uris; using placeholder")

            # Build the base registration JWT input (reused across scenarios).
            base_jwt_input = DcrRegistrationJwtInput(
                issuer=software_id,
                audience=discovery.issuer,
                redirect_uris=redirect_uris,
                token_endpoint_auth_method=discovery.selected_auth_method,
                grant_types=["client_credentials", "authorization_code"],
                response_types=self._derive_response_types(discovery),
                software_statement=self._credentials.ssa_jwt.decode("ascii").strip(),
            )

            # Step 3: DCR-001 — positive registration.
            dcr001_result, client_state = self._run_dcr001(client, discovery, base_jwt_input)
            results.append(dcr001_result)

            if client_state is None:
                # All CRUD and token scenarios depend on DCR-001.
                remaining = [s for s in ALL_SCENARIOS if s.scenario_id != "DCR-001"]
                for scenario in remaining:
                    results.append(skipped_result(scenario.scenario_id, "DCR-001 registration failed; cannot proceed"))
                return DcrRunResult(
                    discovery=discovery,
                    scenario_results=results,
                    cleanup_attempted=False,
                    cleanup_succeeded=False,
                    cleanup_detail="DCR-001 failed; cleanup not attempted",
                )

            # Step 4: DCR-002 (GET, if advertised).
            if self._advertise_get:
                results.append(self._run_dcr002(client, discovery, client_state))
            else:
                results.append(skipped_result("DCR-002", "GET /register/{clientId} not advertised"))

            # Step 5: DCR-003 (PUT, if advertised).
            if self._advertise_put:
                results.append(self._run_dcr003(client, discovery, client_state, base_jwt_input))
            else:
                results.append(skipped_result("DCR-003", "PUT /register/{clientId} not advertised"))

            # Step 6: Negative registration scenarios (independent of client state).
            results.append(self._run_dcr005(client, discovery, base_jwt_input, software_id=software_id))
            results.append(self._run_dcr007(client, discovery, base_jwt_input))
            results.append(self._run_dcr008(client, discovery, base_jwt_input))
            results.append(self._run_dcr009(client, discovery, base_jwt_input))

            # Step 7: DCR-011 (wrong client ID token — runs before DELETE).
            results.append(self._run_dcr011(client, discovery))

            # Step 8: DCR-004 (DELETE, if advertised).
            delete_succeeded = False
            if self._advertise_delete:
                dcr004_result, delete_succeeded = self._run_dcr004(client, discovery, client_state)
                results.append(dcr004_result)
            else:
                results.append(skipped_result("DCR-004", "DELETE /register/{clientId} not advertised"))

            # Step 9: DCR-010 (deleted client access).
            if delete_succeeded:
                results.append(self._run_dcr010(client, discovery, client_state))
            else:
                results.append(skipped_result("DCR-010", "DCR-004 did not succeed; cannot test deleted-client access"))

            # Cleanup: if DELETE was not run as part of testing, attempt it now.
            cleanup_attempted, cleanup_succeeded, cleanup_detail = self._attempt_cleanup(
                client,
                discovery=discovery,
                client_state=client_state,
                delete_already_ran=delete_succeeded or self._advertise_delete,
            )

        return DcrRunResult(
            discovery=discovery,
            scenario_results=results,
            cleanup_attempted=cleanup_attempted,
            cleanup_succeeded=cleanup_succeeded,
            cleanup_detail=cleanup_detail,
        )

    # -----------------------------------------------------------------------
    # Individual scenario methods
    # -----------------------------------------------------------------------

    def _run_dcr001(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        jwt_input: DcrRegistrationJwtInput,
    ) -> tuple[DcrScenarioResult, DcrClientState | None]:
        """Execute DCR-001: POST /register with valid SSA and claims.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            jwt_input: Base registration JWT input values.

        Returns:
            Tuple of ``(DcrScenarioResult, DcrClientState | None)`` where
            the client state is ``None`` when registration failed.
        """
        reg_jwt = build_registration_jwt(jwt_input, self._credentials)
        status_code, body, resp_headers = self._post_register(client, discovery.registration_endpoint, reg_jwt)
        evidence = evidence_from_http_response(
            request_url=discovery.registration_endpoint,
            request_method="POST",
            request_content_type="application/jose",
            response_status=status_code,
            response_headers=resp_headers,
            response_body=body,
        )

        if status_code != 201:  # noqa: PLR2004
            return (
                failed_result(
                    "DCR-001",
                    detail=f"Expected HTTP 201 from POST /register; got {status_code}",
                    evidence=evidence,
                ),
                None,
            )

        try:
            validate_dcr_registration_response(body, self._dcr_version)
        except DcrSchemaValidationError as exc:
            return (
                failed_result(
                    "DCR-001",
                    detail=f"Registration response failed schema validation: {exc}",
                    evidence=evidence,
                ),
                None,
            )

        try:
            client_state = parse_client_state(body)
        except ValueError as exc:
            return (
                failed_result(
                    "DCR-001",
                    detail=f"Failed to parse registration response: {exc}",
                    evidence=evidence,
                ),
                None,
            )

        return (
            passed_result(
                "DCR-001",
                detail=(
                    f"POST /register returned 201 with client_id={client_state.client_id!r}; "
                    f"DCR {self._dcr_version} required fields present"
                ),
                evidence=evidence,
            ),
            client_state,
        )

    def _run_dcr002(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        client_state: DcrClientState | None,
    ) -> DcrScenarioResult:
        """Execute DCR-002: GET /register/{clientId}, expect 200.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            client_state: Registered client state from DCR-001, or None.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        if not isinstance(client_state, DcrClientState):
            return skipped_result("DCR-002", "client_state not available")

        url = f"{discovery.registration_endpoint.rstrip('/')}/{client_state.client_id}"
        headers: dict[str, str] = {}
        rat = client_state.registration_access_token()
        if rat:
            headers["Authorization"] = f"Bearer {rat}"

        status_code, body, resp_headers = self._send_crud(client, "GET", url, headers=headers)
        evidence = evidence_from_http_response(
            request_url=url,
            request_method="GET",
            request_content_type="",
            response_status=status_code,
            response_headers=resp_headers,
            response_body=body,
        )
        if status_code == 200:  # noqa: PLR2004
            return passed_result("DCR-002", detail="GET /register/{clientId} returned 200", evidence=evidence)
        return failed_result("DCR-002", detail=f"Expected 200; got {status_code}", evidence=evidence)

    def _run_dcr003(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        client_state: DcrClientState | None,
        jwt_input: DcrRegistrationJwtInput,
    ) -> DcrScenarioResult:
        """Execute DCR-003: PUT /register/{clientId}, expect 200.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            client_state: Registered client state from DCR-001.
            jwt_input: Base JWT input used to build the update payload.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        if not isinstance(client_state, DcrClientState):
            return skipped_result("DCR-003", "client_state not available")

        url = f"{discovery.registration_endpoint.rstrip('/')}/{client_state.client_id}"
        reg_jwt = build_registration_jwt(jwt_input, self._credentials)
        headers: dict[str, str] = {"Content-Type": "application/jose"}
        rat = client_state.registration_access_token()
        if rat:
            headers["Authorization"] = f"Bearer {rat}"

        status_code, body, resp_headers = self._send_crud(
            client, "PUT", url, headers=headers, body_bytes=reg_jwt.encode("ascii")
        )
        evidence = evidence_from_http_response(
            request_url=url,
            request_method="PUT",
            request_content_type="application/jose",
            response_status=status_code,
            response_headers=resp_headers,
            response_body=body,
        )
        if status_code == 200:  # noqa: PLR2004
            return passed_result("DCR-003", detail="PUT /register/{clientId} returned 200", evidence=evidence)
        return failed_result("DCR-003", detail=f"Expected 200; got {status_code}", evidence=evidence)

    def _run_dcr004(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        client_state: DcrClientState | None,
    ) -> tuple[DcrScenarioResult, bool]:
        """Execute DCR-004: DELETE /register/{clientId}, expect 204.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            client_state: Registered client state from DCR-001.

        Returns:
            Tuple of ``(DcrScenarioResult, delete_succeeded)``.
        """
        if not isinstance(client_state, DcrClientState):
            return skipped_result("DCR-004", "client_state not available"), False

        url = f"{discovery.registration_endpoint.rstrip('/')}/{client_state.client_id}"
        headers: dict[str, str] = {}
        rat = client_state.registration_access_token()
        if rat:
            headers["Authorization"] = f"Bearer {rat}"

        status_code, body, resp_headers = self._send_crud(client, "DELETE", url, headers=headers)
        evidence = evidence_from_http_response(
            request_url=url,
            request_method="DELETE",
            request_content_type="",
            response_status=status_code,
            response_headers=resp_headers,
            response_body=body,
        )
        if status_code == 204:  # noqa: PLR2004
            return (
                passed_result("DCR-004", detail="DELETE /register/{clientId} returned 204", evidence=evidence),
                True,
            )
        return (
            failed_result("DCR-004", detail=f"Expected 204; got {status_code}", evidence=evidence),
            False,
        )

    def _run_dcr005(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        jwt_input: DcrRegistrationJwtInput,
        *,
        software_id: str,
    ) -> DcrScenarioResult:
        """Execute DCR-005: Registration with expired SSA, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            jwt_input: Base registration JWT input.
            software_id: software_id extracted from the real SSA.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        try:
            reg_jwt = build_negative_registration_jwt_expired_ssa(jwt_input, self._credentials, software_id=software_id)
        except Exception as exc:  # noqa: BLE001
            return failed_result("DCR-005", detail=f"Failed to build expired-SSA JWT: {exc}", evidence=None)

        return self._run_negative_register("DCR-005", client, discovery.registration_endpoint, reg_jwt)

    def _run_dcr007(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        jwt_input: DcrRegistrationJwtInput,
    ) -> DcrScenarioResult:
        """Execute DCR-007: Registration with invalid issuer, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            jwt_input: Base registration JWT input; ``iss`` will be replaced.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        try:
            reg_jwt = build_negative_registration_jwt_wrong_issuer(jwt_input, self._credentials)
        except Exception as exc:  # noqa: BLE001
            return failed_result("DCR-007", detail=f"Failed to build invalid-issuer JWT: {exc}", evidence=None)
        return self._run_negative_register("DCR-007", client, discovery.registration_endpoint, reg_jwt)

    def _run_dcr008(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        jwt_input: DcrRegistrationJwtInput,
    ) -> DcrScenarioResult:
        """Execute DCR-008: Registration with invalid token endpoint auth method, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            jwt_input: Base registration JWT input; auth method will be replaced.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        try:
            reg_jwt = build_negative_registration_jwt_invalid_auth_method(jwt_input, self._credentials)
        except Exception as exc:  # noqa: BLE001
            return failed_result("DCR-008", detail=f"Failed to build invalid-auth-method JWT: {exc}", evidence=None)
        return self._run_negative_register("DCR-008", client, discovery.registration_endpoint, reg_jwt)

    def _run_dcr009(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        jwt_input: DcrRegistrationJwtInput,
    ) -> DcrScenarioResult:
        """Execute DCR-009: Registration with wrong response type, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            jwt_input: Base registration JWT input; ``response_types`` will be replaced.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        try:
            reg_jwt = build_negative_registration_jwt_wrong_response_type(jwt_input, self._credentials)
        except Exception as exc:  # noqa: BLE001
            return failed_result("DCR-009", detail=f"Failed to build wrong-response-type JWT: {exc}", evidence=None)
        return self._run_negative_register("DCR-009", client, discovery.registration_endpoint, reg_jwt)

    def _run_dcr010(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
        client_state: DcrClientState | None,
    ) -> DcrScenarioResult:
        """Execute DCR-010: Access using deleted client ID, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            client_state: Client state from DCR-001 (client was deleted in DCR-004).

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        if not isinstance(client_state, DcrClientState):
            return skipped_result("DCR-010", "client_state not available")

        try:
            status_code, body = request_token_wrong_client_id(
                client, discovery.token_endpoint, fake_client_id=client_state.client_id
            )
        except DcrTokenError as exc:
            return failed_result("DCR-010", detail=f"Token request raised an error: {exc}", evidence=None)

        evidence = build_step_evidence(
            request_url=discovery.token_endpoint,
            request_method="POST",
            request_content_type="application/x-www-form-urlencoded",
            request_headers={},
            response_status=status_code,
            response_headers={},
            response_body=body,
        )
        if status_code >= 400:  # noqa: PLR2004
            return passed_result(
                "DCR-010",
                detail=f"Token endpoint correctly rejected deleted client_id with {status_code}",
                evidence=evidence,
            )
        return failed_result(
            "DCR-010",
            detail=f"Expected 4xx from token endpoint for deleted client; got {status_code}",
            evidence=evidence,
        )

    def _run_dcr011(
        self,
        client: httpx.Client,
        discovery: DcrDiscoveryResult,
    ) -> DcrScenarioResult:
        """Execute DCR-011: Access using wrong client ID, expect 4xx.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        fake_client_id = f"dcr-test-invalid-{uuid4().hex[:8]}"
        try:
            status_code, body = request_token_wrong_client_id(
                client, discovery.token_endpoint, fake_client_id=fake_client_id
            )
        except DcrTokenError as exc:
            return failed_result("DCR-011", detail=f"Token request raised an error: {exc}", evidence=None)

        evidence = build_step_evidence(
            request_url=discovery.token_endpoint,
            request_method="POST",
            request_content_type="application/x-www-form-urlencoded",
            request_headers={},
            response_status=status_code,
            response_headers={},
            response_body=body,
        )
        if status_code >= 400:  # noqa: PLR2004
            return passed_result(
                "DCR-011",
                detail=f"Token endpoint correctly rejected wrong client_id with {status_code}",
                evidence=evidence,
            )
        return failed_result(
            "DCR-011",
            detail=f"Expected 4xx from token endpoint for wrong client_id; got {status_code}",
            evidence=evidence,
        )

    # -----------------------------------------------------------------------
    # Negative-registration helper
    # -----------------------------------------------------------------------

    def _run_negative_register(
        self,
        scenario_id: str,
        client: httpx.Client,
        registration_endpoint: str,
        reg_jwt: str,
    ) -> DcrScenarioResult:
        """Execute a negative registration scenario, asserting 4xx response.

        Args:
            scenario_id: Scenario identifier for the result.
            client: Preconfigured mTLS HTTP client.
            registration_endpoint: ASPSP registration endpoint URL.
            reg_jwt: Pre-built registration JWT (with invalid claims).

        Returns:
            A :class:`~conformance.plugins.dcr.client_state.DcrScenarioResult`.
        """
        status_code, body, resp_headers = self._post_register(client, registration_endpoint, reg_jwt)
        evidence = evidence_from_http_response(
            request_url=registration_endpoint,
            request_method="POST",
            request_content_type="application/jose",
            response_status=status_code,
            response_headers=resp_headers,
            response_body=body,
        )
        if status_code >= 400:  # noqa: PLR2004
            return passed_result(
                scenario_id,
                detail=f"ASPSP correctly rejected invalid registration request with {status_code}",
                evidence=evidence,
            )
        return failed_result(
            scenario_id,
            detail=f"Expected 4xx from POST /register for invalid input; got {status_code}",
            evidence=evidence,
        )

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def _attempt_cleanup(
        self,
        client: httpx.Client,
        *,
        discovery: DcrDiscoveryResult,
        client_state: DcrClientState | None,
        delete_already_ran: bool,
    ) -> tuple[bool, bool, str]:
        """Attempt to DELETE the registered client as post-test cleanup.

        Cleanup is skipped when DELETE was already exercised as part of the
        test scenarios (``delete_already_ran=True``) or when DELETE is not
        advertised.  Cleanup failures are logged but do not affect any
        conformance assertion.

        Args:
            client: Preconfigured mTLS HTTP client.
            discovery: Validated OIDC discovery result.
            client_state: Registered client state from DCR-001.
            delete_already_ran: ``True`` when DELETE was already attempted.

        Returns:
            Tuple of ``(cleanup_attempted, cleanup_succeeded, cleanup_detail)``.
        """
        if delete_already_ran or not self._advertise_delete:
            return False, False, "Cleanup not required (DELETE already ran or not advertised)"
        if not isinstance(client_state, DcrClientState):
            return False, False, "Cleanup skipped: no client_state available"

        url = f"{discovery.registration_endpoint.rstrip('/')}/{client_state.client_id}"
        headers: dict[str, str] = {}
        rat = client_state.registration_access_token()
        if rat:
            headers["Authorization"] = f"Bearer {rat}"

        logger.info("DCR cleanup: attempting DELETE %s", url)
        try:
            status_code, _body, _headers = self._send_crud(client, "DELETE", url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            detail = f"Cleanup DELETE raised an exception: {exc}"
            logger.warning("DCR cleanup failed: %s", detail)
            return True, False, detail

        if status_code == 204:  # noqa: PLR2004
            detail = f"Cleanup DELETE succeeded (204) for client_id={client_state.client_id!r}"
            logger.info("DCR cleanup: %s", detail)
            return True, True, detail

        detail = f"Cleanup DELETE returned {status_code} for client_id={client_state.client_id!r}"
        logger.warning("DCR cleanup: %s", detail)
        return True, False, detail

    # -----------------------------------------------------------------------
    # HTTP helpers
    # -----------------------------------------------------------------------

    def _post_register(
        self,
        client: httpx.Client,
        registration_endpoint: str,
        reg_jwt: str,
    ) -> tuple[int, JsonObject, dict[str, str]]:
        """POST a registration JWT to the registration endpoint.

        Args:
            client: Preconfigured mTLS HTTP client.
            registration_endpoint: ASPSP registration endpoint URL.
            reg_jwt: Compact JWT to send as the ``application/jose`` body.

        Returns:
            Tuple of ``(status_code, response_body, response_headers)``.
        """
        try:
            response = send_json(
                client,
                "POST",
                registration_endpoint,
                headers={"Content-Type": "application/jose"},
                json_body_bytes=reg_jwt.encode("ascii"),
            )
            return response.status_code, dict(response.body), dict(response.headers)
        except JsonHttpClientError as exc:
            if exc.status_code is not None:
                return exc.status_code, {}, {}
            logger.error("POST /register failed with no HTTP response: %s", exc)
            return 0, {}, {}

    def _send_crud(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body_bytes: bytes | None = None,
    ) -> tuple[int, JsonObject, dict[str, str]]:
        """Send a GET, PUT, or DELETE to the registration management endpoint.

        Args:
            client: Preconfigured mTLS HTTP client.
            method: HTTP method (``"GET"``, ``"PUT"``, or ``"DELETE"``).
            url: Full URL of the registration management resource.
            headers: Optional additional headers.
            body_bytes: Optional body bytes for PUT requests.

        Returns:
            Tuple of ``(status_code, response_body, response_headers)``.
        """
        try:
            response = send_json(
                client,
                method,
                url,
                headers=headers or {},
                json_body_bytes=body_bytes,
            )
            return response.status_code, dict(response.body), dict(response.headers)
        except JsonHttpClientError as exc:
            if exc.status_code is not None:
                return exc.status_code, {}, {}
            logger.error("%s %s failed with no HTTP response: %s", method, url, exc)
            return 0, {}, {}

    # -----------------------------------------------------------------------
    # HTTP client factory
    # -----------------------------------------------------------------------

    def _build_http_client(self) -> httpx.Client:
        """Build an mTLS httpx client from the configured credential paths.

        Applies transport config options: ``tls_skip_verify``, CA bundle,
        ``disable_keep_alives``, and timeouts.

        Returns:
            A configured :class:`httpx.Client` ready for DCR requests.
        """
        transport_config = self._transport_config
        cred_paths = self._credential_paths

        if transport_config.tls_skip_verify:
            from conformance.dcr.transport import tls_skip_verify_warning

            warnings.warn(tls_skip_verify_warning(), stacklevel=2)
            verify: bool | ssl.SSLContext = False
        elif cred_paths.ca_bundle_path is not None:
            ctx = ssl.create_default_context()
            ctx.load_verify_locations(cafile=str(cred_paths.ca_bundle_path))
            verify = ctx
        else:
            verify = True

        cert = (
            str(cred_paths.transport_certificate_path),
            str(cred_paths.transport_private_key_path),
        )
        timeout = httpx.Timeout(
            connect=transport_config.connection_timeout_seconds,
            read=transport_config.read_timeout_seconds,
            write=transport_config.connection_timeout_seconds,
            pool=transport_config.connection_timeout_seconds,
        )
        limits = (
            httpx.Limits(max_keepalive_connections=0, keepalive_expiry=0.0)
            if transport_config.disable_keep_alives
            else httpx.Limits()
        )
        return httpx.Client(timeout=timeout, verify=verify, cert=cert, limits=limits)

    # -----------------------------------------------------------------------
    # SSA parsing
    # -----------------------------------------------------------------------

    def _parse_ssa(self) -> dict[str, object]:
        """Decode SSA claims from the loaded credential bytes.

        Returns:
            Dict of JWT payload claims from the SSA.
        """
        raw = parse_ssa_claims(self._credentials.ssa_jwt)
        return dict(raw)

    # -----------------------------------------------------------------------
    # Claim helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _derive_response_types(discovery: DcrDiscoveryResult) -> list[str]:
        """Derive registration response_types from discovery metadata.

        Prefers ``"code"`` when it appears in the advertised response types,
        otherwise falls back to the first advertised type, or ``["code"]``
        when none are advertised.

        Args:
            discovery: Validated OIDC discovery result.

        Returns:
            List of response type strings for the registration JWT.
        """
        supported = discovery.response_types_supported
        if "code" in supported:
            return ["code"]
        return supported[:1] if supported else ["code"]
