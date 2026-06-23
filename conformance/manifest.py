"""Load and validate v0/v1 conformance manifest files."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, cast

from conformance.auth_metadata import (
    AuthBundleDeclaration,
    AuthBundleError,
    AuthBundleInventory,
    AuthStepRequirement,
    validate_inventory,
)
from conformance.json_types import JsonValue
from conformance.model_bank_config import TokenEndpointClientAuthMode
from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri


class ManifestError(ValueError):
    """Raised when a conformance manifest cannot be loaded or validated."""


ManifestSchemaVersion = Literal["v0", "v1"]
"""Manifest schema versions accepted by the parser."""

CertificationCoverage = Literal["partial", "complete"]
"""Manifest-level certification coverage declaration.

Declares whether a suite provides full certification coverage (``complete``) or
is intentionally partial / non-certifying (``partial``). Omitting the field from
a v1 manifest is treated as ``partial`` for safety, so smoke suites and starter
slices cannot inadvertently pass certification eligibility. V0 manifests always
receive ``partial`` because v0 is a legacy smoke-check format that predates the
certification eligibility model.

The value feeds directly into ``certificationEligibility.eligible`` in the result
JSON and into OBL-side ``validate_report``; a ``partial`` manifest blocks both
even when all mandatory steps pass and the tool version is approved.
"""

StepPhase = Literal["setup", "execution"]
"""Scheduling phase accepted by v1 steps."""

RequestMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
"""HTTP methods supported by manifest-driven smoke-check requests."""

GeneratedRequestObjectSource = Literal["fapi-signing"]
"""Source selectors for generated PSU request-object directives."""

TokenEndpointAuthSource = Literal["fapi-signing"]
"""Source selectors for token-endpoint auth directives on HTTP steps."""

DetachedJwsSource = Literal["fapi-signing"]
"""Source selectors for detached JWS directives on HTTP requests."""

SigningNegativeCase = Literal["omit-detached-jws-header", "omit-request-object-signature-claim", "omit-jwt-claim"]
"""Enumerated negative signing mutations accepted by v1 step directives.

``omit-detached-jws-header`` omits the entire ``x-jws-signature`` header from
write requests that would otherwise carry a detached JWS.
``omit-request-object-signature-claim`` removes the OpenBanking intent ID
claim from PSU authorisation request objects.
``omit-jwt-claim`` produces a detached JWS with the JOSE protected header
``b64`` critical claim intentionally omitted, so the JWT is structurally
malformed relative to the Open Banking detached-JWS specification.
"""

PsuExpectedAuthorizationResponseType = Literal["http_status"]
"""Expected-response discriminators supported on PSU authorisation steps."""

AssertionType = Literal["http_status", "json_field", "header", "response_schema", "ob_error_code", "response_signature"]
"""Assertion discriminators supported by manifest assertions."""

ResponseSchemaSource = Literal["bundled_openapi"]
"""Source selectors for schema-backed response assertions."""

JsonFieldRule = Literal[
    "required",
    "https_url",
    "array",
    "absent",
    "string",
    "number",
    "boolean",
    "object",
    "non_empty_array",
    "min_items",
    "equals",
    "one_of",
    "all_items_have_field",
    "all_items_absent_field",
]
"""JSON field validation rules supported by manifest assertions."""

HeaderRule = Literal["present", "absent", "equals", "contains", "matches_request_header"]
"""HTTP header validation rules supported by manifest assertions."""

FollowUpType = Literal["jwks"]
"""Follow-up request kinds supported by v0 manifest tests."""

FollowUpUrlSource = Literal["response.body.jwks_uri"]
"""Locations from which follow-up request URLs may be extracted."""

GeneratedValueKind = Literal["per-run-uuid", "per-run-compact-uuid"]
"""Accepted generated value kinds for test-value profile entries.

``"per-run-uuid"`` instructs the runtime to generate a fresh UUID4 string
(36 characters with hyphens) each time the suite is executed.

``"per-run-compact-uuid"`` generates a UUID4 hex string without hyphens
(32 characters), suitable for Open Banking fields with a 35-character maximum
such as ``InstructionIdentification`` and ``EndToEndIdentification``.

Both kinds are unique per run and safe to use as payment identifiers.
"""

_TEST_VALUES_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
"""Pattern for valid test-value key names.

Keys must begin with a letter and may contain only letters, digits, hyphens,
and underscores. Leading-digit keys are reserved for step-placeholder IDs and
would create ambiguity in the dot-path resolver.
"""

_TEST_VALUES_PLACEHOLDER_PATTERN = re.compile(r"\$\{testValues\.([A-Za-z][A-Za-z0-9_-]*)\}")
"""Regex matching valid ``${testValues.<key>}`` placeholders in v1 manifests."""


@dataclass(frozen=True)
class TestValueReference:
    """Field-level manifest reference to one ``${testValues.<key>}`` placeholder.

    Attributes:
        key: Referenced test-value key name.
        request_area: High-level request area where the placeholder appears
            (for example ``"request-url"``, ``"request-header"``,
            ``"request-json-body"``, ``"request-form-body"``,
            ``"psu-step-field"``, or ``"psu-request-object"``).
        field_path: Dot-path-like field path within the manifest step that
            contains the placeholder (for example
            ``"request.body.Data.Initiation.CreditorAccount.Name"``).
    """

    key: str
    __test__: ClassVar[bool] = False
    request_area: str
    field_path: str


def _sorted_test_value_references(references: set[TestValueReference]) -> tuple[TestValueReference, ...]:
    """Sort test-value references deterministically for stable evidence output.

    Args:
        references: Unordered set of parsed test-value references.

    Returns:
        Tuple sorted by field path, request area, then key.
    """
    return tuple(
        sorted(
            references,
            key=lambda reference: (
                reference.field_path,
                reference.request_area,
                reference.key,
            ),
        )
    )


def _extract_test_value_references(*, text: str, request_area: str, field_path: str) -> tuple[TestValueReference, ...]:
    """Extract field-level test-value references from one placeholder-capable string.

    Args:
        text: Candidate string that may contain ``${testValues.<key>}``
            placeholders.
        request_area: High-level request area label for emitted references.
        field_path: Dot-path-like field location for emitted references.

    Returns:
        Tuple of distinct references for keys present in ``text``.
    """
    return _sorted_test_value_references(
        {
            TestValueReference(key=key, request_area=request_area, field_path=field_path)
            for key in _TEST_VALUES_PLACEHOLDER_PATTERN.findall(text)
        }
    )


def _extract_test_value_keys(text: str) -> frozenset[str]:
    """Extract consumed ``testValues`` placeholder keys from a string.

    Args:
        text: Candidate string that may contain ``${testValues.<key>}``
            placeholders.

    Returns:
        Frozen set of matched key names. Empty when no test-value placeholders
        are present.
    """
    return frozenset(_TEST_VALUES_PLACEHOLDER_PATTERN.findall(text))


def _collect_keys_from_json_value(value: JsonValue) -> frozenset[str]:
    """Recursively collect consumed test-value keys from a JSON value.

    Args:
        value: JSON value to scan. String leaves are searched for
            ``${testValues.<key>}`` placeholders.

    Returns:
        Frozen set of all discovered key names.
    """
    references = _collect_references_from_json_value(
        value,
        request_area="request-json-body",
        field_path="request.body",
    )
    return frozenset(reference.key for reference in references)


def _collect_references_from_json_value(
    value: JsonValue,
    *,
    request_area: str,
    field_path: str,
) -> tuple[TestValueReference, ...]:
    """Recursively collect field-level test-value references from a JSON value.

    Args:
        value: JSON value to scan. String leaves are searched for
            ``${testValues.<key>}`` placeholders.
        request_area: High-level request area label for emitted references.
        field_path: Dot-path-like location of ``value`` within its enclosing
            request body.

    Returns:
        Tuple of unique field-level references discovered under ``value``.
    """
    if isinstance(value, str):
        return _extract_test_value_references(text=value, request_area=request_area, field_path=field_path)
    if isinstance(value, list):
        refs: set[TestValueReference] = set()
        for index, item in enumerate(value):
            refs.update(
                _collect_references_from_json_value(
                    item,
                    request_area=request_area,
                    field_path=f"{field_path}[{index}]",
                )
            )
        return _sorted_test_value_references(refs)
    if isinstance(value, dict):
        refs = set()
        for key, item in value.items():
            refs.update(
                _collect_references_from_json_value(
                    item,
                    request_area=request_area,
                    field_path=f"{field_path}.{key}",
                )
            )
        return _sorted_test_value_references(refs)
    return ()


def _collect_keys_from_generated_request_object(request_object: GeneratedRequestObject | None) -> frozenset[str]:
    """Collect consumed test-value keys from a generated PSU request-object directive.

    Args:
        request_object: Generated request-object directive to scan, or ``None``.

    Returns:
        Frozen set of consumed key names from ``audience`` and
        ``openbanking_intent_id`` fields.
    """
    references = _collect_references_from_generated_request_object(request_object)
    return frozenset(reference.key for reference in references)


def _collect_references_from_generated_request_object(
    request_object: GeneratedRequestObject | None,
) -> tuple[TestValueReference, ...]:
    """Collect field-level references from a generated PSU request-object directive.

    Args:
        request_object: Generated request-object directive to scan, or ``None``.

    Returns:
        Tuple of field-level references from ``audience`` and
        ``openbanking_intent_id`` fields.
    """
    if request_object is None:
        return ()
    references: set[TestValueReference] = set()
    if request_object.audience is not None:
        references.update(
            _extract_test_value_references(
                text=request_object.audience,
                request_area="psu-request-object",
                field_path="requestObject.audience",
            )
        )
    if request_object.openbanking_intent_id is not None:
        references.update(
            _extract_test_value_references(
                text=request_object.openbanking_intent_id,
                request_area="psu-request-object",
                field_path="requestObject.openbankingIntentId",
            )
        )
    return _sorted_test_value_references(references)


def _collect_keys_from_manifest_request(request: ManifestRequest) -> frozenset[str]:
    """Collect consumed test-value keys from a parsed manifest HTTP request.

    Args:
        request: Parsed manifest request whose placeholder-capable fields should
            be scanned.

    Returns:
        Frozen set of consumed key names from URL, headers, body, and generated
        request-object directives embedded in request bodies.
    """
    references = _collect_references_from_manifest_request(request)
    return frozenset(reference.key for reference in references)


def _collect_references_from_manifest_request(request: ManifestRequest) -> tuple[TestValueReference, ...]:
    """Collect field-level test-value references from a parsed HTTP request.

    Args:
        request: Parsed manifest request whose placeholder-capable fields should
            be scanned.

    Returns:
        Tuple of unique references from URL, headers, JSON body leaves, and
        form-urlencoded field values.
    """
    references: set[TestValueReference] = set(
        _extract_test_value_references(
            text=request.url,
            request_area="request-url",
            field_path="request.url",
        )
    )
    if request.headers is not None:
        for name, value in request.headers.items():
            references.update(
                _extract_test_value_references(
                    text=value,
                    request_area="request-header",
                    field_path=f"request.headers.{name}",
                )
            )
    if isinstance(request.body, JsonBody):
        references.update(
            _collect_references_from_json_value(
                request.body.value,
                request_area="request-json-body",
                field_path="request.body",
            )
        )
    elif isinstance(request.body, FormBody):
        for field_name, field_value in request.body.fields.items():
            references.update(
                _extract_test_value_references(
                    text=field_value,
                    request_area="request-form-body",
                    field_path=f"request.body.fields.{field_name}",
                )
            )
    return _sorted_test_value_references(references)


@dataclass(frozen=True)
class JsonBody:
    """Manifest request body sent as ``application/json``.

    The default body shape for v1 manifests. A bare body value
    (no ``encoding`` tag) is also parsed into ``JsonBody`` for
    backwards compatibility with the original DL-0013 contract.

    Attributes:
        value: JSON value sent verbatim as the request body. String leaves
            may contain ``${...}`` placeholders that the executor resolves
            against the execution context before dispatch.
    """

    value: JsonValue


@dataclass(frozen=True)
class FormBody:
    """Manifest request body sent as ``application/x-www-form-urlencoded``.

    Used by OAuth 2.0 token-exchange and similar flows where the wire format
    is form-urlencoded rather than JSON. The executor sets ``Content-Type:
    application/x-www-form-urlencoded`` automatically only when the manifest
    has not supplied a ``Content-Type`` header (case-insensitive per RFC
    7230). Encoding is delegated to ``httpx`` (never hand-rolled).

    Attributes:
        fields: Mapping of form field name to value. Both names and values
            are strings; placeholder substitution applies to each value
            before dispatch. Stored as a read-only ``MappingProxyType`` so
            the parsed body cannot be mutated after parse time.
    """

    fields: Mapping[str, str]


type ManifestBody = JsonBody | FormBody
"""Discriminated request body shape carried by ``ManifestRequest.body``."""


@dataclass(frozen=True)
class ManifestRequest:
    """HTTP request declared by a manifest test.

    Attributes:
        method: HTTP method used for the manifest request.
        url: HTTPS URL fetched for the manifest test.
        headers: Optional string-valued headers to send with the request.
        body: Optional typed body (JSON or form-urlencoded). Allowed on
            POST/PUT/PATCH/DELETE; rejected on GET at parse time.
        detached_jws: Optional directive requesting runtime detached JWS
            signing for this exact request.
    """

    method: RequestMethod
    url: str
    headers: dict[str, str] | None = None
    body: ManifestBody | None = None
    detached_jws: DetachedJwsPolicy | None = None


@dataclass(frozen=True)
class GeneratedRequestObject:
    """Directive instructing the executor to sign a JAR request object.

    Attributes:
        source: Runtime signing source used to build the PS256 request object.
        audience: Optional request-object JWT ``aud`` value. Placeholders are
            permitted so bundled manifests can use the OpenID discovery
            issuer when an ASPSP requires that value instead of the concrete
            authorisation endpoint URL.
        openbanking_intent_id: Optional Open Banking consent identifier that
            should be embedded into the signed request object. Placeholders are
            permitted so bundled AIS manifests can bind PSU authorisation to a
            consent created by an earlier step.
    """

    source: GeneratedRequestObjectSource
    audience: str | None = None
    openbanking_intent_id: str | None = None


RequestObjectValue = str | GeneratedRequestObject
"""Accepted PSU ``requestObject`` manifest values.

Legacy manifests may continue to supply an opaque string JWT directly.
Newer manifests may instead declare a typed runtime-generated directive.
"""


@dataclass(frozen=True)
class TokenEndpointAuthPolicy:
    """Directive instructing an HTTP token exchange to use runtime auth config.

    Attributes:
        source: Runtime signing/auth configuration source to apply.
    """

    source: TokenEndpointAuthSource


@dataclass(frozen=True)
class DetachedJwsPolicy:
    """Directive instructing an HTTP request to add a detached JWS header.

    Attributes:
        source: Runtime signing configuration source used to build the
            detached JWS.
    """

    source: DetachedJwsSource


@dataclass(frozen=True)
class PsuExpectedAuthorizationResponse:
    """Expected non-redirect response accepted for a headless PSU step.

    Attributes:
        type: Expected-response discriminator. Currently only ``"http_status"``
            is supported.
        expected: HTTP status code that the ASPSP authorisation endpoint is
            expected to return directly (without redirecting to ``redirectUri``).
    """

    type: PsuExpectedAuthorizationResponseType
    expected: int


@dataclass(frozen=True)
class TestValueProfileEntry:
    """Single named profile in the manifest test-value profile catalogue.

    Each profile declares a complete set of test values that the suite can use
    at runtime. A manifest may declare one or more profiles; the executor
    selects the default profile unless the participant config overrides it.

    Attributes:
        id: Stable identifier for this profile (e.g. ``"ozone-sandbox"``).
        label: Human-readable display label for plan preview and evidence.
        values: Immutable mapping of declared key names to their literal string
            values. Participants may override individual keys if the key is
            listed in :attr:`TestValueProfileSpec.allowed_override_keys`.
        generated_keys: Immutable mapping of key names to their generated-value
            kind. Generated keys are not stored in :attr:`values`; the executor
            produces their value at run time and caches it for the duration of
            the run so all steps receive the same value.
    """

    id: str
    label: str
    values: Mapping[str, str]
    generated_keys: Mapping[str, GeneratedValueKind]


@dataclass(frozen=True)
class TestValueProfileSpec:
    """Manifest-level test-value profile metadata.

    Declared at the manifest root under ``testValueProfiles``. Describes the
    available named profiles, which keys participants may override, and which
    keys are safe to include in masked evidence.

    Attributes:
        default_profile_id: Identifier of the profile used when the
            participant config omits ``testValues.profile``.
        profiles: Tuple of declared profile entries in the order they appear
            in the manifest. Must be non-empty; must contain a profile whose
            id matches :attr:`default_profile_id`.
        allowed_override_keys: Frozen set of key names that the participant
            config is permitted to override via ``testValues.overrides``.
            Override keys that are absent from this set are rejected at
            config validation time.
        non_secret_keys: Frozen set of key names whose effective values may
            be included in masked execution-log evidence without redaction.
            Keys absent from this set are treated as sensitive and must pass
            through the standard masking pipeline before persistence.
    """

    default_profile_id: str
    profiles: tuple[TestValueProfileEntry, ...]
    allowed_override_keys: frozenset[str]
    non_secret_keys: frozenset[str]


@dataclass(frozen=True)
class ManifestTestValues:
    """Parsed test-value declarations from a suite manifest.

    Extracted from the manifest's top-level ``testValues`` block.

    Attributes:
        baseline: Mapping of key names to generic certifiable default values.
        generated_keys: Mapping of key names to generation strategy identifiers
            (e.g. ``"per-run-uuid"``, ``"per-run-compact-uuid"``).
        allowed_custom_keys: Set of key names that participants may override.
    """

    baseline: Mapping[str, str]
    generated_keys: Mapping[str, str]
    allowed_custom_keys: frozenset[str]


@dataclass(frozen=True)
class StepSelectionMetadata:
    """Step-level conditional selection metadata for plan preview and evidence.

    Carried by v1 manifest steps that participate in the conditional-row
    contract. Old manifests without ``selectionMetadata`` leave this field
    as ``None`` on their steps; all existing selection semantics are
    preserved unchanged.

    Attributes:
        condition_id: Optional stable machine-readable identifier for the
            condition that governs whether this step is selected (e.g.
            ``"domestic-scheduled-payments-supported"``). Used by
            plan-preview and evidence tooling to explain selection outcomes.
        condition_label: Optional human-readable label for the condition,
            suitable for display in plan preview rows and result summaries.
        conditional: Whether the step is a conditional row. When ``True``
            the step is deselected in the default plan unless the required
            test values are available from the resolved profile.
        required_test_value_keys: Tuple of test-value key names that must
            resolve to non-empty values for this step to be automatically
            selected. Only meaningful when :attr:`conditional` is ``True``.
    """

    condition_id: str | None
    condition_label: str | None
    conditional: bool
    required_test_value_keys: tuple[str, ...]


@dataclass(frozen=True)
class FollowUpRequest:
    """HTTP request shape for a manifest follow-up step.

    v0 follow-ups are always GET requests. The narrow type enforces the
    contract at the type level; ``_required_get_method`` enforces it at
    parse time.

    Attributes:
        method: HTTP method used for the follow-up request (always GET).
    """

    method: Literal["GET"]


@dataclass(frozen=True)
class HttpStatusAssertion:
    """Assertion requiring a specific HTTP response status.

    Attributes:
        type: Assertion discriminator for HTTP status checks.
        expected: Expected HTTP status code.
    """

    type: Literal["http_status"]
    expected: int


@dataclass(frozen=True)
class JsonFieldAssertion:
    """Assertion requiring a JSON field to satisfy a rule.

    Attributes:
        type: Assertion discriminator for JSON field checks.
        path: Dot-path to the response JSON field under test.
        rule: Validation rule applied to the JSON field.
        value: Exact JSON value expected for ``equals`` rules.
        values: Candidate JSON values accepted by ``one_of`` rules.
        min_items: Minimum array length required by ``min_items`` rules.
        field: Field evaluated by ``all_items_have_field`` and
            ``all_items_absent_field`` rules.
    """

    type: Literal["json_field"]
    path: str
    rule: JsonFieldRule
    value: JsonValue | None = None
    values: tuple[JsonValue, ...] | None = None
    min_items: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class HeaderAssertion:
    """Assertion requiring a response header to satisfy a rule.

    Attributes:
        type: Assertion discriminator for header checks.
        name: Header field name under test.
        rule: Validation rule applied to the response header value.
        value: Expected or required substring for ``equals`` and ``contains``
            rules.
        request_header: Name of the outbound request header whose value the
            response header must echo back for the ``matches_request_header``
            rule. Populated from the manifest ``requestHeader`` field when
            present, otherwise defaults to ``name``. Always ``None`` for all
            other rules.
    """

    type: Literal["header"]
    name: str
    rule: HeaderRule
    value: str | None = None
    request_header: str | None = None


@dataclass(frozen=True)
class ResponseSchemaAssertion:
    """Assertion requiring a response payload to satisfy a JSON/OpenAPI schema.

    Attributes:
        type: Assertion discriminator for schema-backed checks.
        source: Source selector for schema resolution.
        document: Allowlisted bundled standards document identifier.
        schema_ref: JSON Pointer reference to a schema within ``document``.
        schema: Optional inline schema object embedded directly in the
            assertion.
        body_path: Optional dot-path selecting a nested response body value
            before schema validation.
    """

    type: Literal["response_schema"]
    source: ResponseSchemaSource
    document: str
    schema_ref: str | None = None
    schema: Mapping[str, JsonValue] | None = None
    body_path: str | None = None


@dataclass(frozen=True)
class ObErrorCodeAssertion:
    """Assertion that checks at least one OB error-code in the response matches an acceptable set.

    Implements the legacy ``asserts_one_of`` OB error-code semantics: at least one
    ``Errors[*].ErrorCode`` in the response body must match one of the ``codes`` values.
    This is used for negative cases where multiple OB error codes are acceptable (for example,
    ``UK.OBIE.Signature.Invalid``, ``UK.OBIE.Signature.Missing``, and
    ``UK.OBIE.Signature.Malformed`` are all acceptable for a missing-JWT-claim negative test).

    Attributes:
        type: Assertion type discriminator.
        codes: Acceptable OB error-code values. At least one ``Errors[*].ErrorCode`` must match.
    """

    type: Literal["ob_error_code"]
    codes: tuple[str, ...]


@dataclass(frozen=True)
class ResponseSignatureAssertion:
    """Assertion requiring an ASPSP response body to verify against ``x-jws-signature``.

    Attributes:
        type: Assertion type discriminator.
        jwks_step_id: Step id whose response body contains the ASPSP JWKS.
        header_name: Response header containing the detached compact JWS.
    """

    type: Literal["response_signature"]
    jwks_step_id: str
    header_name: str = "x-jws-signature"


ManifestAssertion = (
    HttpStatusAssertion
    | JsonFieldAssertion
    | HeaderAssertion
    | ResponseSchemaAssertion
    | ObErrorCodeAssertion
    | ResponseSignatureAssertion
)
"""Assertion variants accepted by manifest tests and sequential steps (v0 and v1)."""


@dataclass(frozen=True)
class ManifestFollowUp:
    """Manifest follow-up step derived from a prior response.

    Attributes:
        type: Follow-up step discriminator.
        url_source: Response location used to discover the follow-up URL.
        request: HTTP request shape for the follow-up fetch.
        assertions: Assertions evaluated against the follow-up response.
    """

    type: FollowUpType
    url_source: FollowUpUrlSource
    request: FollowUpRequest
    assertions: tuple[ManifestAssertion, ...]


@dataclass(frozen=True)
class ManifestTest:
    """Single conformance check declared by a v0 manifest.

    Attributes:
        id: Stable identifier for the manifest test.
        name: Human-readable test name.
        request: Primary HTTP request to execute.
        assertions: Assertions evaluated against the primary response.
        follow_up: Optional follow-up step, such as JWKS validation.
    """

    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]
    follow_up: ManifestFollowUp | None = None


@dataclass(frozen=True)
class ManifestStep:
    """Single sequential step declared by a v1 manifest.

    Attributes:
        id: Stable identifier for the step, referenced by later placeholders.
        name: Human-readable step name.
        request: HTTP request to execute (may contain ``${...}`` placeholders).
        assertions: Assertions evaluated against the step response.
        warning: Optional deprecation or risk message. When present and the step
            would otherwise pass, the executor emits a ``warn`` outcome instead
            of ``passed`` and surfaces this message in the step result. Per the
            PRD, ``warn`` does not block certification.
        mandatory: Whether this step is required for certification eligibility.
            Defaults to ``False``. Per the PRD's Certification Eligibility
            Assessment, a run is eligible for certification submission only
            when every mandatory step passed (``warn`` is non-blocking, but
            ``failed`` and ``skipped`` are blocking). Mandatory status is
            defined per spec version and standard in manifest configuration —
            never hardcoded — so OBL Standards can adjust mandatory coverage
            without an engine release.
        optional: Whether this step is opt-in for the default test plan.
            Defaults to ``False`` (the step is part of default coverage).
            When ``True`` the step is present in the
            :class:`conformance.test_plan.TestPlan` but starts deselected;
            participants opt into running it deliberately. Mutually
            exclusive with ``mandatory`` (enforced at parse time so the
            plan precedence rule stays unambiguous).
        group: Execution group identifier. Defaults to ``"default"``.
            Steps in different groups may run independently once setup
            has completed.
        phase: Scheduling phase for this step. Defaults to
            ``"execution"``. Setup steps run before grouped execution.
        token_endpoint_auth_policy: Optional directive indicating that the
            executor should apply token-endpoint client authentication using
            runtime FAPI signing configuration rather than literal manifest
            form fields.
        signing_negative_case: Optional narrow negative-signing selector used
            by legacy payment error tests. Supported values are
            ``"omit-detached-jws-header"`` (HTTP request executes without the
            detached JWS header even when ``detachedJws`` is present) and
            ``"omit-request-object-signature-claim"`` (PSU runtime request
            object omits the Open Banking intent signature claim).
        required_token_id: Optional semantic auth requirement id required by
            this step when it consumes a protected-resource bearer token.
            This binds the step to ``${tokens.<id>.access_token}`` header
            placeholders without coupling consumers to token step ids.
        produces_token_id: Optional semantic auth requirement id minted by
            this step when its response carries an ``access_token``.
        selection_metadata: Optional step-level conditional selection metadata
            declared via ``selectionMetadata``. ``None`` for steps in old
            manifests that predate this field and for unconditional steps
            that omit the section.
        consumed_test_value_keys: Frozen set of key names referenced by
            ``${testValues.<key>}`` placeholders in this step's
            placeholder-capable request fields.
        test_value_references: Field-level references for each
            ``${testValues.<key>}`` placeholder consumed by this step.
    """

    id: str
    name: str
    request: ManifestRequest
    assertions: tuple[ManifestAssertion, ...]
    warning: str | None = None
    mandatory: bool = False
    optional: bool = False
    group: str = "default"
    phase: StepPhase = "execution"
    token_endpoint_auth_policy: TokenEndpointAuthPolicy | None = None
    signing_negative_case: SigningNegativeCase | None = None
    required_token_id: str | None = None
    produces_token_id: str | None = None
    selection_metadata: StepSelectionMetadata | None = None
    consumed_test_value_keys: frozenset[str] = field(default_factory=frozenset)
    test_value_references: tuple[TestValueReference, ...] = ()


PsuAuthorizationMode = Literal["manual", "headless"]
"""Mode controlling how the engine completes a PSU authorisation step.

``manual`` surfaces the authorisation URL to the participant and polls the
:class:`conformance.api.auth_session_store.AuthSessionStore` until the
ASPSP browser redirect resolves the session. ``headless`` issues the
authorisation request engine-side, parses the ``Location`` header of the
expected 3xx response, and feeds the result into the store programmatically
(see PRD open-investigation item: feasibility against the Ozone sandbox
is not yet confirmed — the code path is unit-tested via
``httpx.MockTransport`` and gated by a separate env var in the tier-2
integration suite).
"""


PsuAuthorizationStepKind = Literal["psu-authorization"]
"""Discriminator value identifying a PSU authorisation step in v1 manifests."""


HttpStepKind = Literal["http"]
"""Discriminator value identifying a plain HTTP step in v1 manifests."""


V1StepKind = HttpStepKind | PsuAuthorizationStepKind
"""All v1 step ``kind`` discriminator values accepted by the parser."""


_PSU_AUTH_TIMEOUT_MIN_SECONDS = 1
"""Minimum permitted value for ``timeoutSeconds`` on a PSU authorisation step."""

_PSU_AUTH_TIMEOUT_MAX_SECONDS = 600
"""Maximum permitted value for ``timeoutSeconds`` on a PSU authorisation step.

Bounded at ten minutes so a misauthored manifest cannot stall a CI run
indefinitely. Parsed manifests outside this range fail before execution, so
the executor deadline calculation receives only validated values.
"""

_PSU_AUTH_DEFAULT_TIMEOUT_SECONDS = 120
"""Default ``timeoutSeconds`` applied when a PSU step omits the field."""

_PSU_AUTH_DEFAULT_RESPONSE_TYPE = "code id_token"
"""Default ``responseType`` for a PSU authorisation step (FAPI 1 Advanced hybrid flow)."""

_PSU_AUTH_DEFAULT_SCOPE = "openid"
"""Default ``scope`` for a PSU authorisation step.

The minimum scope required for an OIDC flow. Manifest authors override
this for AIS/PIS/CBPII/VRP consent flows that require additional scopes
(e.g. ``"openid accounts"``).
"""

_PSU_AUTH_MIN_STATE_LENGTH = 32
"""Minimum length for a manifest-supplied ``state`` value (after stripping).

Mirrors :data:`conformance.api.auth_session_store.MIN_CALLER_SUPPLIED_STATE_LENGTH`
so a misauthored manifest fails fast at parse time rather than producing
a runtime FAIL inside the executor when the store rejects the value.
A literal ``state`` value that contains a placeholder is exempt from
this parse-time check — the value will be resolved at runtime and the
store will enforce the same minimum then.
"""


@dataclass(frozen=True)
class PsuAuthorizationStep:
    """Single PSU authorisation step declared by a v1 manifest.

    Drives the existing ``AuthSessionStore`` + ``/callback/`` plumbing to
    capture an ASPSP-issued authorisation ``code``. The captured ``code``
    is exposed to downstream steps via the synthetic
    ``${steps.<id>.response.body.code}`` placeholder so a subsequent
    standard HTTP step can perform the token exchange.

    Per the PRD's PSU Authorisation section, two modes are supported:

    * ``manual``: the executor surfaces the authorisation URL via an
      execution-log event and polls the store until the participant
      completes the consent flow in a browser.
    * ``headless``: the executor issues the authorisation request itself
      with ``follow_redirects=False`` and parses the 3xx ``Location``
      header to extract ``state`` and ``code`` (or ``error``).

    Attributes:
        id: Stable identifier for the step, referenced by later placeholders.
        name: Human-readable step name.
        mode: Whether the step waits for a browser callback (``manual``) or
            drives the redirect itself (``headless``).
        authorization_endpoint: Authorisation endpoint URL. Placeholders are
            permitted so the URL can be sourced from an earlier discovery
            step. Validated as HTTPS at parse time when no placeholder is
            present, and again at runtime after placeholder resolution.
        client_id: OAuth 2.0 client identifier sent as the ``client_id``
            query parameter. Placeholders permitted.
        redirect_uri: Registered redirect URI sent as the ``redirect_uri``
            query parameter and used to match the ASPSP redirect in
            headless mode. Literal values are validated as HTTPS at parse
            time. The only permitted placeholder is the narrow participant
            config value ``${config.oauth.redirectUri}``, which is resolved
            and HTTPS-validated again at runtime.
        response_type: OAuth 2.0 ``response_type`` value. Defaults to
            ``"code id_token"`` (FAPI 1 Advanced hybrid flow). Placeholders
            are **not** permitted — this is a static FAPI-defined value.
        scope: OAuth 2.0 ``scope`` value. Defaults to ``"openid"``.
            Placeholders are **not** permitted — scope is a static,
            manifest-author-time consent declaration.
        state: Optional caller-supplied ``state`` token. Placeholders are
            permitted so the value can come from an earlier step. When
            omitted (``None``) the executor asks the store to generate
            a cryptographically secure value. When supplied as a literal
            (no placeholders), the value must be at least 32 characters
            to mirror the store's minimum entropy requirement.
        nonce: Optional caller-supplied OIDC ``nonce`` token. Placeholders are
            permitted so the value can come from an earlier step. When omitted
            (``None``), the executor generates a cryptographically secure
            value for the authorisation request.
        request_object: Optional signed JWT carried as the ``request``
            query parameter (RFC 9101 JAR). Legacy manifests may supply an
            opaque string JWT; newer manifests may instead declare a typed
            runtime-generated directive. String values permit placeholders so
            the JWT can be produced by an upstream signing step.
        signing_negative_case: Optional narrow negative-signing selector used
            by legacy payment error tests. PSU steps only support
            ``"omit-request-object-signature-claim"``, which removes the
            Open Banking intent signature claim from a runtime-generated
            request object on this step only.
        timeout_seconds: Per-step deadline in seconds. Defaults to 120;
            must be between 1 and 600 inclusive.
        expected_authorization_response: Optional expected direct HTTP response
            for headless negative tests where the ASPSP rejects authorisation
            before any redirect to ``redirectUri``. Currently supports only
            an exact ``http_status`` match in the 4xx/5xx range.
        mandatory: Whether the step is required for certification
            eligibility. Same semantics as :class:`ManifestStep`.
        optional: Whether the step is opt-in for the default test plan.
            Same semantics as :class:`ManifestStep`. Mutually exclusive
            with ``mandatory`` (enforced at parse time).
        group: Execution group identifier. Defaults to ``"default"``.
            PSU steps in the same group retain sequential ordering.
        phase: Scheduling phase for this step. Defaults to
            ``"execution"``. Setup-phase PSU steps execute before grouped
            execution starts.
        selection_metadata: Optional step-level conditional selection metadata.
            Same semantics as :class:`ManifestStep`.
        consumed_test_value_keys: Frozen set of key names referenced by
            ``${testValues.<key>}`` placeholders in this step's
            placeholder-capable fields.
        test_value_references: Field-level references for each
            ``${testValues.<key>}`` placeholder consumed by this step.
    """

    id: str
    name: str
    mode: PsuAuthorizationMode
    authorization_endpoint: str
    client_id: str
    redirect_uri: str
    response_type: str = _PSU_AUTH_DEFAULT_RESPONSE_TYPE
    scope: str = _PSU_AUTH_DEFAULT_SCOPE
    state: str | None = None
    nonce: str | None = None
    request_object: RequestObjectValue | None = None
    signing_negative_case: SigningNegativeCase | None = None
    timeout_seconds: int = _PSU_AUTH_DEFAULT_TIMEOUT_SECONDS
    expected_authorization_response: PsuExpectedAuthorizationResponse | None = None
    mandatory: bool = False
    optional: bool = False
    group: str = "default"
    phase: StepPhase = "execution"
    selection_metadata: StepSelectionMetadata | None = None
    consumed_test_value_keys: frozenset[str] = field(default_factory=frozenset)
    test_value_references: tuple[TestValueReference, ...] = ()


type V1Step = ManifestStep | PsuAuthorizationStep
"""Discriminated v1 step variant carried by :attr:`Manifest.steps`.

``ManifestStep`` represents a plain HTTP step (``"kind": "http"`` —
the default and the only kind accepted before this addition).
``PsuAuthorizationStep`` represents an OAuth 2.0 / OIDC PSU authorisation
step (``"kind": "psu-authorization"``).
"""


@dataclass(frozen=True)
class Manifest:
    """Validated conformance manifest (v0 or v1).

    Attributes:
        schema_version: Manifest schema version accepted by this parser.
        name: Human-readable manifest name.
        certification_coverage: Whether this manifest declares full
            certification coverage (``complete``) or is intentionally
            partial / non-certifying (``partial``). V0 manifests are always
            ``partial``. V1 manifests default to ``partial`` when the
            ``certificationCoverage`` root key is omitted. A ``partial``
            value blocks ``certificationEligibility.eligible`` in the result
            JSON and OBL-side ``validate_report``, even when all mandatory
            steps pass and the tool version is approved.
        tests: Ordered manifest tests to execute (v0 only, empty for v1).
        steps: Ordered sequential steps to execute (v1 only, empty for v0).
            Each entry is either a plain HTTP step or a PSU authorisation
            step — see :data:`V1Step` for the discriminated union.
        auth_inventory: Optional durable, non-secret auth bundle inventory
            declared by a v1 manifest's ``authMetadata`` root key.  When
            present, every bundle's step references must resolve against
            :attr:`steps` and the whole inventory is validated via
            :func:`conformance.auth_metadata.validate_inventory`.  V0
            manifests and v1 manifests without ``authMetadata`` leave this
            as ``None``; all existing parsing and execution paths treat
            ``None`` as "no explicit auth metadata declared".
        test_value_profiles: Optional manifest-level test-value profile
            metadata declared via the ``testValueProfiles`` root key. When
            present, ``${testValues.<key>}`` placeholders in steps are resolved
            against the effective profile (default profile merged with any
            participant overrides). V0 manifests and v1 manifests without
            ``testValueProfiles`` leave this as ``None``.
        test_values: Optional manifest-level baseline/generation metadata
            declared via the ``testValues`` root key. When present, it
            provides suite-baseline values, runtime-generated keys, and the
            allow-list for participant-supplied custom test data.
    """

    schema_version: ManifestSchemaVersion
    name: str
    certification_coverage: CertificationCoverage = "partial"
    tests: tuple[ManifestTest, ...] = ()
    steps: tuple[V1Step, ...] = ()
    auth_inventory: AuthBundleInventory | None = None
    test_value_profiles: TestValueProfileSpec | None = None
    test_values: ManifestTestValues | None = None


def load_manifest(manifest_path: Path) -> Manifest:
    """Load a v0 conformance manifest JSON file from disk.

    Args:
        manifest_path: Path to the manifest JSON file.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If the file cannot be read, parsed, or validated.
    """
    resolved_manifest_path = manifest_path.resolve()
    try:
        raw_manifest = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"Invalid JSON manifest: {error.msg}") from error
    except OSError as error:
        raise ManifestError(f"Unable to read manifest file: {error}") from error

    if not isinstance(raw_manifest, dict):
        raise ManifestError("Manifest root must be a JSON object")

    return parse_manifest(raw_manifest)


def load_manifest_from_object(raw_manifest: object) -> Manifest:
    """Validate and parse a manifest from an already-decoded JSON object.

    Intended for API callers that provide the manifest inline in a request
    body rather than as a file path.

    Args:
        raw_manifest: Decoded JSON value expected to be a JSON object.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If the value is not a JSON object or validation fails.
    """
    if not isinstance(raw_manifest, dict):
        raise ManifestError("Manifest root must be a JSON object")
    return parse_manifest(cast(dict[str, JsonValue], raw_manifest))


def parse_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated manifest (v0 or v1).

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated conformance manifest.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    schema_version = _required_string(raw_manifest, "schemaVersion", location="manifest")
    if schema_version == "v0":
        return _parse_v0_manifest(raw_manifest)
    if schema_version == "v1":
        return _parse_v1_manifest(raw_manifest)
    raise ManifestError("schemaVersion must be v0 or v1")


def _parse_v0_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated v0 manifest.

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated v0 conformance manifest.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    _reject_unknown_keys(raw_manifest, allowed_keys={"schemaVersion", "name", "tests"}, location="manifest")

    name = _required_string(raw_manifest, "name", location="manifest")
    tests = _required_object_array(raw_manifest, "tests", location="manifest")

    return Manifest(
        schema_version="v0",
        name=name,
        tests=tuple(_parse_test(raw_test, index=index) for index, raw_test in enumerate(tests)),
    )


def _parse_certification_coverage(raw_manifest: dict[str, JsonValue], *, location: str) -> CertificationCoverage:
    """Extract and validate the optional ``certificationCoverage`` root key.

    Defaults to ``"partial"`` when the key is absent so that manifests that
    pre-date the field (and all v0 manifests) cannot inadvertently satisfy
    certification eligibility. Only the two literal values ``"partial"`` and
    ``"complete"`` are accepted; any other string is rejected at parse time.

    Args:
        raw_manifest: The manifest JSON object to extract from.
        location: Dot-path location string used in error messages.

    Returns:
        ``"partial"`` when absent or explicitly set to ``"partial"``;
        ``"complete"`` only when explicitly set to ``"complete"``.

    Raises:
        ManifestError: If ``certificationCoverage`` is present but is not one
            of the two accepted values.
    """
    if "certificationCoverage" not in raw_manifest:
        return "partial"
    value = raw_manifest["certificationCoverage"]
    if value == "partial":
        return "partial"
    if value == "complete":
        return "complete"
    raise ManifestError(f"{location}.certificationCoverage must be one of: partial, complete (got: {value!r})")


_STEP_ID_CHAR_CLASS = r"[A-Za-z0-9][A-Za-z0-9_-]*"
"""Character class for valid step/test IDs (excludes dot to avoid resolver ambiguity)."""

_TOKEN_ID_PATTERN = re.compile(r"^" + _STEP_ID_CHAR_CLASS + r"$")
"""Pattern for semantic runtime token identifiers used by ``tokens`` placeholders."""

_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")
"""RFC 7230 token pattern for valid HTTP header field names."""

_HEADER_VALUE_INVALID_PATTERN = re.compile(r"[^\x09\x20-\x7e]")
"""Pattern matching characters not transportable as HTTP header field values.

httpx encodes string header values as ASCII, so only the ASCII-safe subset
of RFC 7230 §3.2.6 field content is transportable: HTAB (0x09), SP (0x20),
and VCHAR (0x21-0x7E). The RFC's obs-text range (0x80-0xFF) is rejected
because it cannot be transmitted without a UnicodeEncodeError at the
transport layer.
"""

_STEP_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{steps\.(" + _STEP_ID_CHAR_CLASS + r")"
    r"\.(?:"
    r"request\.(?:method|url)"
    r"|"
    r"response\.(?:status_code|body(?:\.[A-Za-z0-9_-]+)+)"
    r")\}"
)
"""Regex matching valid ``${steps.<id>...}`` placeholders with direction-specific rules.

Request direction accepts: ``method``, ``url`` (no sub-segments).
Response direction accepts: ``status_code`` (no sub-segments), ``body.<path>`` (at least one segment).
"""

_CONFIG_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{config\.(?:discoveryUrl|environment|oauth\.(?:clientId|redirectUri|authorizationEndpoint|openBankingIntentId|resourceBaseUrl))\}"
)
"""Regex matching safe runtime config placeholders accepted in v1 manifests."""

_TOKEN_PLACEHOLDER_PATTERN = re.compile(r"\$\{tokens\.([A-Za-z0-9][A-Za-z0-9_-]*)\.access_token\}")
"""Regex matching semantic token placeholders accepted in v1 manifests."""

_PLACEHOLDER_FIND_PATTERN = re.compile(r"\$\{[^}]*\}")
"""Regex matching any ``${...}`` token for syntax validation."""

_DEFAULT_STEP_GROUP = "default"
"""Default execution group assigned when a v1 step omits ``group``."""

_DEFAULT_STEP_PHASE: StepPhase = "execution"
"""Default scheduling phase assigned when a v1 step omits ``phase``."""


def validate_header_value(value: str, *, location: str) -> None:
    """Validate an HTTP header field value for transport safety.

    Rejects empty/whitespace-only values and values containing characters
    that cannot be transmitted by httpx (which encodes headers as ASCII).
    Permitted characters are HTAB (0x09), SP (0x20), and VCHAR (0x21-0x7E).

    The RFC 7230 §3.2.6 obs-text range (0x80-0xFF) is intentionally excluded
    because httpx raises ``UnicodeEncodeError`` for non-ASCII str header
    values. This restriction ensures all validated values are transportable.

    This function is used both at manifest parse time (static values) and
    after placeholder resolution (dynamic values) to ensure no invalid
    characters reach the HTTP transport layer.

    Args:
        value: The header field value to validate.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If the value is empty or contains non-transportable
            characters.
    """
    if not value.strip():
        raise ManifestError(f"{location} must not be empty")
    match = _HEADER_VALUE_INVALID_PATTERN.search(value)
    if match:
        bad_char = match.group()
        code_point = ord(bad_char)
        raise ManifestError(
            f"{location} contains non-transportable character U+{code_point:04X} "
            "(only HTAB, SP, and VCHAR 0x21-0x7E are permitted)"
        )


def _parse_v1_manifest(raw_manifest: dict[str, JsonValue]) -> Manifest:
    """Parse a raw JSON object into a validated v1 manifest.

    Args:
        raw_manifest: JSON object loaded from a conformance manifest file.

    Returns:
        Parsed and validated v1 conformance manifest with sequential steps.

    Raises:
        ManifestError: If required fields are missing or validation fails.
    """
    _reject_unknown_keys(
        raw_manifest,
        allowed_keys={
            "schemaVersion",
            "name",
            "certificationCoverage",
            "steps",
            "authMetadata",
            "testValueProfiles",
            "testValues",
        },
        location="manifest",
    )

    name = _required_string(raw_manifest, "name", location="manifest")
    certification_coverage = _parse_certification_coverage(raw_manifest, location="manifest")
    test_value_profiles, known_test_value_keys = _parse_v1_test_value_profiles(raw_manifest)
    test_values, known_manifest_test_value_keys = _parse_v1_test_values(raw_manifest)
    raw_steps = _required_object_array(raw_manifest, "steps", location="manifest")
    allowed_test_value_keys = frozenset(known_test_value_keys | known_manifest_test_value_keys)

    seen_ids: set[str] = set()
    steps: list[V1Step] = []
    for index, raw_step in enumerate(raw_steps):
        step = _parse_v1_step(
            raw_step,
            index=index,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
        seen_ids.add(step.id)
        steps.append(step)

    known_step_ids = frozenset(seen_ids)
    auth_inventory = _parse_v1_auth_metadata(raw_manifest, known_step_ids=known_step_ids)

    return Manifest(
        schema_version="v1",
        name=name,
        certification_coverage=certification_coverage,
        steps=tuple(steps),
        auth_inventory=auth_inventory,
        test_value_profiles=test_value_profiles,
        test_values=test_values,
    )


# ---------------------------------------------------------------------------
# Auth metadata parsing helpers
# ---------------------------------------------------------------------------

_AUTH_TOKEN_ENDPOINT_AUTH_METHODS: frozenset[str] = frozenset({"private_key_jwt", "tls_client_auth"})
"""Token-endpoint client auth method identifiers accepted in auth bundle declarations.

Must stay aligned with :data:`conformance.model_bank_config.TokenEndpointClientAuthMode`
so the manifest parser produces only values the executor auth-routing logic
can act on.
"""


def _parse_v1_auth_metadata(
    raw_manifest: dict[str, JsonValue],
    *,
    known_step_ids: frozenset[str],
) -> AuthBundleInventory | None:
    """Parse the optional ``authMetadata`` section from a v1 manifest root.

    ``authMetadata`` carries durable, non-secret auth bundle declarations and
    per-step bundle mappings that downstream plan-preview, execution routing,
    and certification-coverage tooling can consume without re-parsing the full
    manifest.  Manifests that omit the key remain fully compatible — the field
    defaults to ``None`` on the returned :class:`Manifest`.

    Validation steps performed:

    1. ``authMetadata`` must be a JSON object when present.
    2. Unknown keys inside the object are rejected.
    3. ``bundles`` must be a non-empty array of bundle declaration objects.
    4. ``stepRequirements`` is optional; when present it must be an array.
    5. Every bundle and step-requirement is parsed and validated via
       :func:`conformance.auth_metadata.validate_inventory` with the full
       manifest step-id set so that stale or typo'd step references fail at
       parse time rather than silently producing wrong execution routing.

    Args:
        raw_manifest: Full v1 manifest JSON object (after top-level key check).
        known_step_ids: Frozen set of all step ids declared by the v1 manifest
            steps array.  Passed to :func:`validate_inventory` to detect
            unknown step references in bundle and step-requirement fields.

    Returns:
        Parsed and validated :class:`AuthBundleInventory`, or ``None`` when
        the ``authMetadata`` key is absent.

    Raises:
        ManifestError: If the section is malformed, contains unknown keys,
            references step ids not declared in the manifest's steps array,
            contains credential material, or fails any structural check in
            :func:`conformance.auth_metadata.validate_inventory`.
    """
    if "authMetadata" not in raw_manifest:
        return None
    raw_auth = raw_manifest["authMetadata"]
    location = "manifest.authMetadata"
    if not isinstance(raw_auth, dict):
        raise ManifestError(f"{location} must be a JSON object when present")
    _reject_unknown_keys(raw_auth, allowed_keys={"bundles", "stepRequirements"}, location=location)

    raw_bundles = _required_object_array(raw_auth, "bundles", location=location)
    bundles = tuple(
        _parse_auth_bundle_declaration(raw_bundle, index=i, location=f"{location}.bundles")
        for i, raw_bundle in enumerate(raw_bundles)
    )
    step_requirements = _parse_auth_step_requirements(raw_auth, location=location)

    inventory = AuthBundleInventory(bundles=bundles, step_requirements=step_requirements)
    try:
        validate_inventory(inventory, known_step_ids=known_step_ids)
    except AuthBundleError as error:
        raise ManifestError(f"{location}: {error}") from error
    return inventory


def _parse_v1_test_value_profiles(
    raw_manifest: dict[str, JsonValue],
) -> tuple[TestValueProfileSpec | None, frozenset[str]]:
    """Parse the optional ``testValueProfiles`` root key from a v1 manifest.

    Args:
        raw_manifest: Full v1 manifest JSON object.

    Returns:
        Two-tuple of ``(TestValueProfileSpec | None, frozenset[str])`` where the
        second item is the union of every declared profile key plus every
        ``allowedOverrideKeys`` entry.

    Raises:
        ManifestError: If the root object, profile entries, or declared key
            lists are malformed.
    """
    if "testValueProfiles" not in raw_manifest:
        return None, frozenset()

    location = "manifest.testValueProfiles"
    raw_profiles = raw_manifest["testValueProfiles"]
    if not isinstance(raw_profiles, dict):
        raise ManifestError(f"{location} must be a JSON object when present")
    _reject_unknown_keys(
        raw_profiles,
        allowed_keys={"defaultProfileId", "profiles", "allowedOverrideKeys", "nonSecretKeys"},
        location=location,
    )

    default_profile_id = _required_string(raw_profiles, "defaultProfileId", location=location)
    raw_entries = _required_object_array(raw_profiles, "profiles", location=location)
    profiles = tuple(
        _parse_test_value_profile_entry(raw_entry, index=index, location=f"{location}.profiles")
        for index, raw_entry in enumerate(raw_entries)
    )

    known_profile_ids: set[str] = set()
    declared_keys: set[str] = set()
    for index, profile in enumerate(profiles):
        if profile.id in known_profile_ids:
            raise ManifestError(f"{location}.profiles[{index}].id '{profile.id}' is a duplicate")
        known_profile_ids.add(profile.id)
        declared_keys.update(profile.values)
        declared_keys.update(profile.generated_keys)

    if default_profile_id not in known_profile_ids:
        raise ManifestError(
            f"{location}.defaultProfileId must match one of the declared profiles (got: {default_profile_id!r})"
        )

    allowed_override_keys = frozenset(
        _parse_optional_string_array(raw_profiles, "allowedOverrideKeys", location=location)
    )
    for key in allowed_override_keys:
        if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ManifestError(
                f"{location}.allowedOverrideKeys contains invalid key {key!r} (must match [A-Za-z][A-Za-z0-9_-]*)"
            )
    non_secret_keys = frozenset(_parse_optional_string_array(raw_profiles, "nonSecretKeys", location=location))
    for key in non_secret_keys:
        if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ManifestError(
                f"{location}.nonSecretKeys contains invalid key {key!r} (must match [A-Za-z][A-Za-z0-9_-]*)"
            )

    declared_keys.update(allowed_override_keys)
    return (
        TestValueProfileSpec(
            default_profile_id=default_profile_id,
            profiles=profiles,
            allowed_override_keys=allowed_override_keys,
            non_secret_keys=non_secret_keys,
        ),
        frozenset(declared_keys),
    )


def _parse_v1_test_values(
    raw_manifest: dict[str, JsonValue],
) -> tuple[ManifestTestValues | None, frozenset[str]]:
    """Parse the optional ``testValues`` root key from a v1 manifest.

    Args:
        raw_manifest: Full v1 manifest JSON object.

    Returns:
        Two-tuple of ``(ManifestTestValues | None, frozenset[str])`` where the
        second item is the union of all baseline keys, generated key names, and
        allowed custom key names.

    Raises:
        ManifestError: If the root object or declared key lists are malformed.
    """
    if "testValues" not in raw_manifest:
        return None, frozenset()

    location = "manifest.testValues"
    raw_test_values = raw_manifest["testValues"]
    if not isinstance(raw_test_values, dict):
        raise ManifestError(f"{location} must be a JSON object when present")
    _reject_unknown_keys(
        raw_test_values,
        allowed_keys={"baseline", "generatedKeys", "allowedCustomKeys"},
        location=location,
    )

    raw_baseline = _required_object(raw_test_values, "baseline", location=location)
    baseline: dict[str, str] = {}
    for key, value in raw_baseline.items():
        if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ManifestError(f"{location}.baseline key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)")
        if not isinstance(value, str):
            raise ManifestError(f"{location}.baseline.{key} must be a string value")
        baseline[key] = value

    generated_keys: dict[str, str] = {}
    raw_generated_keys = raw_test_values.get("generatedKeys")
    if raw_generated_keys is not None:
        if not isinstance(raw_generated_keys, dict):
            raise ManifestError(f"{location}.generatedKeys must be a JSON object when present")
        for key, value in raw_generated_keys.items():
            if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
                raise ManifestError(
                    f"{location}.generatedKeys key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)"
                )
            if value not in ("per-run-uuid", "per-run-compact-uuid"):
                raise ManifestError(f"{location}.generatedKeys.{key} must be 'per-run-uuid' or 'per-run-compact-uuid'")
            generated_keys[key] = value

    allowed_custom_keys = frozenset(
        _parse_optional_string_array(raw_test_values, "allowedCustomKeys", location=location)
    )
    for key in allowed_custom_keys:
        if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ManifestError(
                f"{location}.allowedCustomKeys contains invalid key {key!r} (must match [A-Za-z][A-Za-z0-9_-]*)"
            )

    known_keys = frozenset(set(baseline) | set(generated_keys) | set(allowed_custom_keys))
    return (
        ManifestTestValues(
            baseline=MappingProxyType(baseline),
            generated_keys=MappingProxyType(generated_keys),
            allowed_custom_keys=allowed_custom_keys,
        ),
        known_keys,
    )


def _parse_test_value_profile_entry(
    raw_entry: dict[str, JsonValue],
    *,
    index: int,
    location: str,
) -> TestValueProfileEntry:
    """Parse one profile entry from ``testValueProfiles.profiles``.

    Args:
        raw_entry: Raw JSON object describing one named profile.
        index: Zero-based position in the profile array.
        location: Dot-path location prefix for the profiles array.

    Returns:
        Parsed immutable profile entry.

    Raises:
        ManifestError: If required fields are missing, a key name is invalid,
            a declared value is not a string, or a generated key duplicates a
            literal value key in the same profile.
    """
    entry_location = f"{location}[{index}]"
    _reject_unknown_keys(
        raw_entry,
        allowed_keys={"id", "label", "values", "generatedKeys"},
        location=entry_location,
    )
    profile_id = _required_string(raw_entry, "id", location=entry_location)
    label = _required_string(raw_entry, "label", location=entry_location)
    raw_values = _required_object(raw_entry, "values", location=entry_location)

    values: dict[str, str] = {}
    for key, value in raw_values.items():
        if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ManifestError(f"{entry_location}.values key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)")
        if not isinstance(value, str):
            raise ManifestError(f"{entry_location}.values.{key} must be a string value")
        values[key] = value

    generated_keys: dict[str, GeneratedValueKind] = {}
    raw_generated_keys = raw_entry.get("generatedKeys")
    if raw_generated_keys is not None:
        if not isinstance(raw_generated_keys, dict):
            raise ManifestError(f"{entry_location}.generatedKeys must be a JSON object when present")
        for key, value in raw_generated_keys.items():
            if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
                raise ManifestError(
                    f"{entry_location}.generatedKeys key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)"
                )
            if key in values:
                raise ManifestError(f"{entry_location}.generatedKeys.{key} duplicates {entry_location}.values.{key}")
            if value not in ("per-run-uuid", "per-run-compact-uuid"):
                raise ManifestError(
                    f"{entry_location}.generatedKeys.{key} must be 'per-run-uuid' or 'per-run-compact-uuid'"
                )
            generated_keys[key] = cast(GeneratedValueKind, value)

    return TestValueProfileEntry(
        id=profile_id,
        label=label,
        values=MappingProxyType(values),
        generated_keys=MappingProxyType(generated_keys),
    )


def _parse_step_selection_metadata(
    raw_step: dict[str, JsonValue],
    *,
    location: str,
    allowed_test_value_keys: frozenset[str],
) -> StepSelectionMetadata | None:
    """Parse optional per-step ``selectionMetadata`` from a v1 manifest step.

    Args:
        raw_step: Raw JSON object for one manifest step.
        location: Dot-path location string for the parent step.
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Parsed step selection metadata, or ``None`` when the step omits the
        ``selectionMetadata`` section.

    Raises:
        ManifestError: If the section is not a JSON object, contains unknown
            keys, or references undeclared test-value keys.
    """
    if "selectionMetadata" not in raw_step:
        return None

    selection_location = f"{location}.selectionMetadata"
    raw_metadata = raw_step["selectionMetadata"]
    if not isinstance(raw_metadata, dict):
        raise ManifestError(f"{selection_location} must be a JSON object when present")
    _reject_unknown_keys(
        raw_metadata,
        allowed_keys={"conditionId", "conditionLabel", "conditional", "requiredTestValueKeys"},
        location=selection_location,
    )

    condition_id = _parse_optional_id_field(raw_metadata, key="conditionId", location=selection_location)
    condition_label = _parse_optional_id_field(raw_metadata, key="conditionLabel", location=selection_location)

    raw_conditional = raw_metadata.get("conditional", False)
    if not isinstance(raw_conditional, bool):
        raise ManifestError(f"{selection_location}.conditional must be a boolean when present")

    required_test_value_keys: list[str] = []
    if "requiredTestValueKeys" in raw_metadata:
        raw_keys = raw_metadata["requiredTestValueKeys"]
        if not isinstance(raw_keys, list):
            raise ManifestError(f"{selection_location}.requiredTestValueKeys must be an array when present")
        for index, raw_key in enumerate(raw_keys):
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ManifestError(f"{selection_location}.requiredTestValueKeys[{index}] must be a non-empty string")
            key = raw_key.strip()
            if _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
                raise ManifestError(
                    f"{selection_location}.requiredTestValueKeys[{index}] must match [A-Za-z][A-Za-z0-9_-]*"
                )
            if key not in allowed_test_value_keys:
                raise ManifestError(
                    f"{selection_location}.requiredTestValueKeys[{index}] references undeclared test-value key {key!r}"
                )
            required_test_value_keys.append(key)

    return StepSelectionMetadata(
        condition_id=condition_id,
        condition_label=condition_label,
        conditional=raw_conditional,
        required_test_value_keys=tuple(required_test_value_keys),
    )


def _parse_auth_bundle_declaration(
    raw_bundle: dict[str, JsonValue],
    *,
    index: int,
    location: str,
) -> AuthBundleDeclaration:
    """Parse one auth bundle declaration from the ``authMetadata.bundles`` array.

    All string fields are validated as non-credential values so that a
    misauthored manifest cannot accidentally embed token material in the
    non-secret auth contract.  Step-id cross-references are validated later
    by :func:`conformance.auth_metadata.validate_inventory`.

    Args:
        raw_bundle: Raw JSON object representing one bundle declaration.
        index: Zero-based position in the ``bundles`` array, used in error
            messages.
        location: Dot-path location prefix for the ``bundles`` array
            (e.g. ``"manifest.authMetadata.bundles"``).

    Returns:
        Parsed :class:`conformance.auth_metadata.AuthBundleDeclaration`.

    Raises:
        ManifestError: If required fields are missing, optional fields are
            malformed, or any field value contains credential material.
    """
    loc = f"{location}[{index}]"
    _reject_unknown_keys(
        raw_bundle,
        allowed_keys={
            "id",
            "tokenStepId",
            "consentStepId",
            "psuStepId",
            "tokenEndpointAuthMethod",
            "requiredScopes",
            "requiredObPermissions",
            "excludedObPermissions",
            "consumingStepIds",
            "capabilityRefs",
        },
        location=loc,
    )
    bundle_id = _required_string(raw_bundle, "id", location=loc)
    token_step_id = _required_string(raw_bundle, "tokenStepId", location=loc)
    consent_step_id = _parse_optional_id_field(raw_bundle, key="consentStepId", location=loc)
    psu_step_id = _parse_optional_id_field(raw_bundle, key="psuStepId", location=loc)
    token_endpoint_auth_method = _parse_optional_bundle_auth_method(raw_bundle, location=loc)
    required_scopes = tuple(_parse_optional_string_array(raw_bundle, "requiredScopes", location=loc))
    required_ob_permissions = tuple(_parse_optional_string_array(raw_bundle, "requiredObPermissions", location=loc))
    excluded_ob_permissions = tuple(_parse_optional_string_array(raw_bundle, "excludedObPermissions", location=loc))
    consuming_step_ids = tuple(_parse_optional_string_array(raw_bundle, "consumingStepIds", location=loc))
    capability_refs = tuple(_parse_optional_string_array(raw_bundle, "capabilityRefs", location=loc))
    return AuthBundleDeclaration(
        id=bundle_id,
        token_step_id=token_step_id,
        consent_step_id=consent_step_id,
        psu_step_id=psu_step_id,
        token_endpoint_auth_method=token_endpoint_auth_method,
        required_scopes=required_scopes,
        required_ob_permissions=required_ob_permissions,
        excluded_ob_permissions=excluded_ob_permissions,
        consuming_step_ids=consuming_step_ids,
        capability_refs=capability_refs,
    )


def _parse_auth_step_requirements(
    raw_auth: dict[str, JsonValue],
    *,
    location: str,
) -> tuple[AuthStepRequirement, ...]:
    """Parse the optional ``stepRequirements`` array from an ``authMetadata`` object.

    When absent the field defaults to an empty tuple.  When present each
    element must be a JSON object containing ``stepId`` and ``bundleId``.
    Cross-reference validation (bundle id must exist in declared bundles,
    step id must exist in the manifest) is performed later by
    :func:`conformance.auth_metadata.validate_inventory`.

    Args:
        raw_auth: ``authMetadata`` JSON object.
        location: Dot-path location of the ``authMetadata`` object, used in
            error messages.

    Returns:
        Parsed tuple of :class:`conformance.auth_metadata.AuthStepRequirement`
        objects.  Empty when the key is absent or the array is empty.

    Raises:
        ManifestError: If ``stepRequirements`` is present but is not an array,
            or any element is not a JSON object with the required string fields.
    """
    if "stepRequirements" not in raw_auth:
        return ()
    raw_reqs = raw_auth["stepRequirements"]
    if not isinstance(raw_reqs, list):
        raise ManifestError(f"{location}.stepRequirements must be an array when present")
    requirements: list[AuthStepRequirement] = []
    for i, raw_req in enumerate(raw_reqs):
        if not isinstance(raw_req, dict):
            raise ManifestError(f"{location}.stepRequirements[{i}] must be a JSON object")
        req_loc = f"{location}.stepRequirements[{i}]"
        _reject_unknown_keys(raw_req, allowed_keys={"stepId", "bundleId"}, location=req_loc)
        step_id = _required_string(raw_req, "stepId", location=req_loc)
        bundle_id = _required_string(raw_req, "bundleId", location=req_loc)
        requirements.append(AuthStepRequirement(step_id=step_id, bundle_id=bundle_id))
    return tuple(requirements)


def _parse_optional_id_field(
    raw_obj: dict[str, JsonValue],
    *,
    key: str,
    location: str,
) -> str | None:
    """Extract an optional non-empty string identifier field from a JSON object.

    Args:
        raw_obj: The JSON object to extract from.
        key: The field name to look up.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped string value, or ``None`` when the key is absent.

    Raises:
        ManifestError: If the key is present but the value is not a
            non-empty string.
    """
    if key not in raw_obj:
        return None
    value = raw_obj[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string when present")
    return value.strip()


def _parse_optional_bundle_auth_method(
    raw_bundle: dict[str, JsonValue],
    *,
    location: str,
) -> TokenEndpointClientAuthMode | None:
    """Extract the optional token-endpoint auth method from a bundle declaration.

    Validates the value against the known set of token-endpoint client-auth
    method identifiers accepted by the executor (``private_key_jwt`` and
    ``tls_client_auth``).  Corresponds to the FAPI 1.0 Advanced permitted
    token-endpoint client-authentication methods.

    Args:
        raw_bundle: Raw JSON object for the bundle declaration.
        location: Dot-path location string used in error messages.

    Returns:
        The validated :data:`conformance.model_bank_config.TokenEndpointClientAuthMode`
        literal, or ``None`` when the field is absent.

    Raises:
        ManifestError: If the field is present but is not a string or is not
            one of the accepted method identifiers.
    """
    if "tokenEndpointAuthMethod" not in raw_bundle:
        return None
    value = raw_bundle["tokenEndpointAuthMethod"]
    if not isinstance(value, str):
        raise ManifestError(f"{location}.tokenEndpointAuthMethod must be a string when present")
    if value not in _AUTH_TOKEN_ENDPOINT_AUTH_METHODS:
        allowed = ", ".join(sorted(_AUTH_TOKEN_ENDPOINT_AUTH_METHODS))
        raise ManifestError(f"{location}.tokenEndpointAuthMethod must be one of: {allowed} (got: {value!r})")
    return cast(TokenEndpointClientAuthMode, value)


def _parse_optional_string_array(
    raw_obj: dict[str, JsonValue],
    key: str,
    *,
    location: str,
) -> list[str]:
    """Extract an optional array of non-empty strings from a JSON object.

    Used to parse auth bundle fields such as ``requiredScopes``,
    ``requiredObPermissions``, ``excludedObPermissions``, ``consumingStepIds``,
    and ``capabilityRefs``.  Returns an empty list when the key is absent so
    optional bundle metadata fields default to empty collections.

    Args:
        raw_obj: The JSON object to extract from.
        key: The field name to look up.
        location: Dot-path location string used in error messages.

    Returns:
        List of stripped non-empty strings.  Empty when the key is absent or
        the value is an empty array.

    Raises:
        ManifestError: If the key is present but the value is not an array,
            or any element is not a non-empty string.
    """
    if key not in raw_obj:
        return []
    raw_value = raw_obj[key]
    if not isinstance(raw_value, list):
        raise ManifestError(f"{location}.{key} must be an array when present")
    result: list[str] = []
    for i, item in enumerate(raw_value):
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"{location}.{key}[{i}] must be a non-empty string")
        result.append(item.strip())
    return result


def _parse_v1_step(
    raw_step: dict[str, JsonValue],
    *,
    index: int,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> V1Step:
    """Parse a single step entry from the v1 manifest steps array.

    Dispatches on the optional ``kind`` discriminator (default ``"http"``)
    to the matching per-kind parser. Unknown ``kind`` values are rejected.

    Args:
        raw_step: Raw JSON object representing one manifest step.
        index: Zero-based position in the steps array, used for error locations.
        seen_ids: Set of step ids already parsed (for duplicate/forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Validated manifest step — either a :class:`ManifestStep` (kind
        ``"http"``) or a :class:`PsuAuthorizationStep` (kind
        ``"psu-authorization"``).

    Raises:
        ManifestError: If required fields are missing, ids are duplicated,
            placeholders reference forward/unknown steps, or ``kind`` is
            unknown.
    """
    location = f"steps[{index}]"
    if not isinstance(raw_step, dict):
        raise ManifestError(f"{location} must be a JSON object")
    kind_raw = raw_step.get("kind", "http")
    if not isinstance(kind_raw, str):
        raise ManifestError(f"{location}.kind must be a string when present")
    if kind_raw == "http":
        return _parse_v1_http_step(
            raw_step,
            index=index,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
    if kind_raw == "psu-authorization":
        return _parse_v1_psu_authorization_step(
            raw_step,
            index=index,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
    raise ManifestError(f"{location}.kind must be one of: http, psu-authorization (got: {kind_raw!r})")


def _parse_v1_http_step(
    raw_step: dict[str, JsonValue],
    *,
    index: int,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> ManifestStep:
    """Parse a plain HTTP v1 manifest step (``"kind": "http"`` or default).

    Args:
        raw_step: Raw JSON object representing one manifest step.
        index: Zero-based position in the steps array, used for error locations.
        seen_ids: Set of step ids already parsed (for duplicate/forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Validated :class:`ManifestStep` with request and assertions.

    Raises:
        ManifestError: If required fields are missing, ids are duplicated, or
            placeholders reference forward/unknown steps.
    """
    location = f"steps[{index}]"
    _reject_unknown_keys(
        raw_step,
        allowed_keys={
            "kind",
            "id",
            "name",
            "request",
            "assertions",
            "warning",
            "mandatory",
            "optional",
            "group",
            "phase",
            "tokenEndpointAuthPolicy",
            "signingNegativeCase",
            "requiredTokenId",
            "producesTokenId",
            "selectionMetadata",
        },
        location=location,
    )

    step_id = _required_string(raw_step, "id", location=location)
    _validate_step_id(step_id, location=location)
    if step_id in seen_ids:
        raise ManifestError(f"{location}.id '{step_id}' is a duplicate")

    step_name = _required_string(raw_step, "name", location=location)
    request = _parse_v1_request(
        _required_object(raw_step, "request", location=location),
        location=f"{location}.request",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    assertions = _required_object_array(raw_step, "assertions", location=location)
    warning = _parse_optional_warning(raw_step, location=location)
    mandatory = _parse_optional_mandatory(raw_step, location=location)
    optional = _parse_optional_optional(raw_step, location=location)
    group = _parse_optional_group(raw_step, location=location)
    phase = _parse_optional_phase(raw_step, location=location)
    selection_metadata = _parse_step_selection_metadata(
        raw_step,
        location=location,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    token_endpoint_auth_policy = _parse_optional_token_endpoint_auth_policy(
        raw_step,
        request=request,
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    signing_negative_case = _parse_optional_signing_negative_case(
        raw_step,
        location=location,
        step_kind="http",
        request=request,
    )
    required_token_id = _parse_optional_token_id(raw_step, key="requiredTokenId", location=location, seen_ids=seen_ids)
    produces_token_id = _parse_optional_token_id(raw_step, key="producesTokenId", location=location, seen_ids=seen_ids)
    inferred_required_token_id = _required_token_id_from_authorization_header(request=request)
    if required_token_id is None:
        required_token_id = inferred_required_token_id
    elif inferred_required_token_id is not None and inferred_required_token_id != required_token_id:
        raise ManifestError(
            f"{location}.requiredTokenId must match Authorization token placeholder id '{inferred_required_token_id}'"
        )
    if required_token_id is not None and inferred_required_token_id is None:
        raise ManifestError(
            f"{location}.requiredTokenId requires Authorization header value "
            f"'Bearer ${{tokens.{required_token_id}.access_token}}'"
        )
    if mandatory and optional:
        raise ManifestError(f"{location}: 'mandatory' and 'optional' must not both be true")
    test_value_references = _collect_references_from_manifest_request(request)
    consumed_test_value_keys = frozenset(reference.key for reference in test_value_references)

    return ManifestStep(
        id=step_id,
        name=step_name,
        request=request,
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
        warning=warning,
        mandatory=mandatory,
        optional=optional,
        group=group,
        phase=phase,
        token_endpoint_auth_policy=token_endpoint_auth_policy,
        signing_negative_case=signing_negative_case,
        required_token_id=required_token_id,
        produces_token_id=produces_token_id,
        selection_metadata=selection_metadata,
        consumed_test_value_keys=consumed_test_value_keys,
        test_value_references=test_value_references,
    )


def _parse_optional_signing_negative_case(
    raw_step: dict[str, JsonValue],
    *,
    location: str,
    step_kind: V1StepKind,
    request: ManifestRequest | None = None,
    request_object: RequestObjectValue | None = None,
) -> SigningNegativeCase | None:
    """Parse the optional signing-negative selector on a v1 step.

    Args:
        raw_step: Raw JSON object for the current step.
        location: Dot-path location string used in error messages.
        step_kind: Declared v1 step kind currently being parsed.
        request: Parsed HTTP request for ``http`` steps, if available.
        request_object: Parsed PSU request object for ``psu-authorization``
            steps, if available.

    Returns:
        Parsed negative-case selector, or ``None`` when the field is absent.

    Raises:
        ManifestError: If the field is not a non-empty string, is not one of
            the accepted selectors, or is incompatible with the current step
            kind or signing inputs.
    """
    if "signingNegativeCase" not in raw_step:
        return None
    raw_value = raw_step["signingNegativeCase"]
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ManifestError(f"{location}.signingNegativeCase must be a non-empty string when present")
    value = raw_value.strip()
    allowed_values = ("omit-detached-jws-header", "omit-request-object-signature-claim", "omit-jwt-claim")
    if value not in allowed_values:
        raise ManifestError(f"{location}.signingNegativeCase must be one of: {', '.join(allowed_values)}")
    if value == "omit-detached-jws-header":
        if step_kind != "http":
            raise ManifestError(f"{location}.signingNegativeCase '{value}' is only valid on http steps")
        if request is None or request.detached_jws is None:
            raise ManifestError(f"{location}.signingNegativeCase '{value}' requires request.detachedJws")
        return "omit-detached-jws-header"

    if value == "omit-jwt-claim":
        if step_kind != "http":
            raise ManifestError(f"{location}.signingNegativeCase '{value}' is only valid on http steps")
        if request is None or request.detached_jws is None:
            raise ManifestError(f"{location}.signingNegativeCase '{value}' requires request.detachedJws")
        return "omit-jwt-claim"

    if step_kind != "psu-authorization":
        raise ManifestError(f"{location}.signingNegativeCase '{value}' is only valid on psu-authorization steps")
    if not isinstance(request_object, GeneratedRequestObject) or request_object.openbanking_intent_id is None:
        raise ManifestError(f"{location}.signingNegativeCase '{value}' requires requestObject.openbankingIntentId")
    return "omit-request-object-signature-claim"


def _parse_optional_token_id(
    raw_step: dict[str, JsonValue],
    *,
    key: str,
    location: str,
    seen_ids: set[str],
) -> str | None:
    """Parse an optional semantic token identifier from a v1 HTTP step.

    Args:
        raw_step: Raw JSON object for the HTTP step.
        key: Field name to parse (``requiredTokenId`` or ``producesTokenId``).
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for placeholder validation).

    Returns:
        Parsed semantic token id, or ``None`` when absent.

    Raises:
        ManifestError: If present but not a non-empty string, contains
            placeholders, or violates the token-id character policy.
    """
    if key not in raw_step:
        return None
    raw_value = raw_step[key]
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string when present")
    token_id = raw_value.strip()
    _validate_constant_manifest_string(token_id, location=f"{location}.{key}", seen_ids=seen_ids)
    if _TOKEN_ID_PATTERN.fullmatch(token_id) is None:
        raise ManifestError(
            f"{location}.{key} must match pattern {_STEP_ID_CHAR_CLASS!r} "
            "(letters/digits, optional internal '_' or '-')"
        )
    return token_id


def _required_token_id_from_authorization_header(*, request: ManifestRequest) -> str | None:
    """Extract a semantic token id from a bearer Authorization header.

    Args:
        request: Parsed HTTP request to inspect.

    Returns:
        Token id from ``Bearer ${tokens.<id>.access_token}``, or ``None``
        when no such placeholder is present.
    """
    if request.headers is None:
        return None
    authorization_value: str | None = None
    for header_name, header_value in request.headers.items():
        if header_name.lower() == "authorization":
            authorization_value = header_value
            break
    if authorization_value is None:
        return None
    match = re.fullmatch(r"\s*Bearer\s+(\$\{tokens\.[A-Za-z0-9][A-Za-z0-9_-]*\.access_token\})\s*", authorization_value)
    if match is None:
        return None
    token_match = _TOKEN_PLACEHOLDER_PATTERN.fullmatch(match.group(1))
    if token_match is None:
        return None
    return token_match.group(1)


def _collect_references_from_psu_authorization_fields(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str | None,
    nonce: str | None,
    request_object: RequestObjectValue | None,
) -> tuple[TestValueReference, ...]:
    """Collect field-level test-value references from PSU step input fields.

    Args:
        authorization_endpoint: Parsed ``authorizationEndpoint`` field value.
        client_id: Parsed ``clientId`` field value.
        redirect_uri: Parsed ``redirectUri`` field value.
        scope: Parsed ``scope`` field value.
        state: Parsed ``state`` field value, or ``None``.
        nonce: Parsed ``nonce`` field value, or ``None``.
        request_object: Parsed ``requestObject`` field value, which may be a
            literal string or a generated request-object directive.

    Returns:
        Tuple of unique field-level test-value references across supported PSU
        placeholder-capable fields.
    """
    references: set[TestValueReference] = set()
    references.update(
        _extract_test_value_references(
            text=authorization_endpoint,
            request_area="psu-step-field",
            field_path="authorizationEndpoint",
        )
    )
    references.update(
        _extract_test_value_references(
            text=client_id,
            request_area="psu-step-field",
            field_path="clientId",
        )
    )
    references.update(
        _extract_test_value_references(
            text=redirect_uri,
            request_area="psu-step-field",
            field_path="redirectUri",
        )
    )
    references.update(
        _extract_test_value_references(
            text=scope,
            request_area="psu-step-field",
            field_path="scope",
        )
    )
    if state is not None:
        references.update(
            _extract_test_value_references(
                text=state,
                request_area="psu-step-field",
                field_path="state",
            )
        )
    if nonce is not None:
        references.update(
            _extract_test_value_references(
                text=nonce,
                request_area="psu-step-field",
                field_path="nonce",
            )
        )
    if isinstance(request_object, str):
        references.update(
            _extract_test_value_references(
                text=request_object,
                request_area="psu-step-field",
                field_path="requestObject",
            )
        )
    elif isinstance(request_object, GeneratedRequestObject):
        references.update(_collect_references_from_generated_request_object(request_object))
    return _sorted_test_value_references(references)


_PSU_AUTH_ALLOWED_KEYS: set[str] = {
    "kind",
    "id",
    "name",
    "mode",
    "authorizationEndpoint",
    "clientId",
    "redirectUri",
    "responseType",
    "scope",
    "state",
    "nonce",
    "requestObject",
    "signingNegativeCase",
    "timeoutSeconds",
    "expectedAuthorizationResponse",
    "mandatory",
    "optional",
    "group",
    "phase",
    "selectionMetadata",
}
"""Permitted top-level keys on a ``psu-authorization`` v1 step.

Closed set — unknown keys are rejected at parse time so a typo in (e.g.)
``redirect_uri`` vs ``redirectUri`` fails fast instead of silently falling
back to the default endpoint. The HTTP-step fields ``request``,
``assertions``, and ``warning`` are intentionally excluded — they do not
apply to PSU steps and the executor would have nothing to do with them.
"""


def _parse_v1_psu_authorization_step(
    raw_step: dict[str, JsonValue],
    *,
    index: int,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> PsuAuthorizationStep:
    """Parse a v1 manifest step of ``"kind": "psu-authorization"``.

    Args:
        raw_step: Raw JSON object representing one PSU authorisation step.
        index: Zero-based position in the steps array, used for error locations.
        seen_ids: Set of step ids already parsed (for duplicate/forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Validated :class:`PsuAuthorizationStep` ready for the executor's
        PSU branch.

    Raises:
        ManifestError: If required fields are missing, ids are duplicated,
            placeholders reference forward/unknown steps, fields fail type
            or value validation, or ``mandatory`` and ``optional`` are both
            set.
    """
    location = f"steps[{index}]"
    _reject_unknown_keys(raw_step, allowed_keys=_PSU_AUTH_ALLOWED_KEYS, location=location)

    step_id = _required_string(raw_step, "id", location=location)
    _validate_step_id(step_id, location=location)
    if step_id in seen_ids:
        raise ManifestError(f"{location}.id '{step_id}' is a duplicate")
    step_name = _required_string(raw_step, "name", location=location)

    mode = _parse_psu_mode(raw_step, location=location)
    selection_metadata = _parse_step_selection_metadata(
        raw_step,
        location=location,
        allowed_test_value_keys=allowed_test_value_keys,
    )

    authorization_endpoint = _required_string(raw_step, "authorizationEndpoint", location=location)
    _validate_placeholder_syntax(
        authorization_endpoint,
        location=f"{location}.authorizationEndpoint",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    if not _PLACEHOLDER_FIND_PATTERN.search(authorization_endpoint):
        try:
            validate_https_url(authorization_endpoint, label=f"{location}.authorizationEndpoint")
        except HttpsUrlValidationError as error:
            raise ManifestError(str(error)) from error

    client_id = _required_string(raw_step, "clientId", location=location)
    _validate_placeholder_syntax(
        client_id,
        location=f"{location}.clientId",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )

    redirect_uri = _required_string(raw_step, "redirectUri", location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(redirect_uri):
        _validate_placeholder_syntax(
            redirect_uri,
            location=f"{location}.redirectUri",
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
        if redirect_uri != "${config.oauth.redirectUri}":
            raise ManifestError(
                f"{location}.redirectUri may only use the config placeholder "
                "${config.oauth.redirectUri} or a literal HTTPS URL"
            )
    else:
        try:
            validate_oauth_redirect_uri(redirect_uri, label=f"{location}.redirectUri")
        except HttpsUrlValidationError as error:
            raise ManifestError(str(error)) from error

    response_type = _parse_psu_optional_string(
        raw_step, key="responseType", default=_PSU_AUTH_DEFAULT_RESPONSE_TYPE, location=location
    )
    if _PLACEHOLDER_FIND_PATTERN.search(response_type):
        raise ManifestError(f"{location}.responseType must not contain placeholders")
    scope = _parse_psu_optional_string(raw_step, key="scope", default=_PSU_AUTH_DEFAULT_SCOPE, location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(scope):
        raise ManifestError(f"{location}.scope must not contain placeholders")

    state = _parse_psu_optional_token(
        raw_step,
        key="state",
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    nonce = _parse_psu_optional_token(
        raw_step,
        key="nonce",
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    request_object = _parse_psu_optional_request_object(
        raw_step,
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    signing_negative_case = _parse_optional_signing_negative_case(
        raw_step,
        location=location,
        step_kind="psu-authorization",
        request_object=request_object,
    )
    timeout_seconds = _parse_psu_timeout_seconds(raw_step, location=location)
    expected_authorization_response = _parse_psu_expected_authorization_response(
        raw_step,
        location=location,
        mode=mode,
        seen_ids=seen_ids,
    )

    mandatory = _parse_optional_mandatory(raw_step, location=location)
    optional = _parse_optional_optional(raw_step, location=location)
    group = _parse_optional_group(raw_step, location=location)
    phase = _parse_optional_phase(raw_step, location=location)
    if mandatory and optional:
        raise ManifestError(f"{location}: 'mandatory' and 'optional' must not both be true")
    test_value_references = _collect_references_from_psu_authorization_fields(
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        nonce=nonce,
        request_object=request_object,
    )
    consumed_test_value_keys = frozenset(reference.key for reference in test_value_references)

    return PsuAuthorizationStep(
        id=step_id,
        name=step_name,
        mode=mode,
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        scope=scope,
        state=state,
        nonce=nonce,
        request_object=request_object,
        signing_negative_case=signing_negative_case,
        timeout_seconds=timeout_seconds,
        expected_authorization_response=expected_authorization_response,
        mandatory=mandatory,
        optional=optional,
        group=group,
        phase=phase,
        selection_metadata=selection_metadata,
        consumed_test_value_keys=consumed_test_value_keys,
        test_value_references=test_value_references,
    )


def _parse_psu_mode(raw_step: dict[str, JsonValue], *, location: str) -> PsuAuthorizationMode:
    """Extract and validate the PSU step ``mode`` discriminator.

    Args:
        raw_step: Raw JSON object for the PSU step.
        location: Dot-path location string used in error messages.

    Returns:
        The validated mode literal (``"manual"`` or ``"headless"``).

    Raises:
        ManifestError: If ``mode`` is missing or not one of the supported values.
    """
    mode = _required_string(raw_step, "mode", location=location)
    if mode == "manual":
        return "manual"
    if mode == "headless":
        return "headless"
    raise ManifestError(f"{location}.mode must be one of: manual, headless (got: {mode!r})")


def _parse_psu_optional_string(raw_step: dict[str, JsonValue], *, key: str, default: str, location: str) -> str:
    """Extract an optional non-empty string field on a PSU step.

    Args:
        raw_step: Raw JSON object for the PSU step.
        key: The key to read from the step (e.g. ``"responseType"``).
        default: Value returned when the key is absent.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped string when present, otherwise ``default``.

    Raises:
        ManifestError: If the key is present but is not a non-empty string.
    """
    if key not in raw_step:
        return default
    value = raw_step[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string when present")
    return value.strip()


def _parse_psu_optional_token(
    raw_step: dict[str, JsonValue],
    *,
    key: str,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> str | None:
    """Parse an optional ``state`` or ``nonce`` field on a PSU authorisation step.

    Placeholders are permitted (and the runtime check inside the
    :class:`AuthSessionStore` enforces the same 32-character minimum for
    ``state`` after resolution). When the literal value contains no
    placeholders, the 32-character minimum is enforced at parse time as well,
    mirroring the store's :data:`MIN_CALLER_SUPPLIED_STATE_LENGTH` so a
    misauthored manifest fails fast.

    Args:
        raw_step: Raw JSON object for the PSU step.
        key: Field name to parse.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        The stripped token string, or ``None`` if the key was absent.

    Raises:
        ManifestError: If the field is present but is not a non-empty
            string, contains a malformed placeholder, references a
            forward step, or — when given as a literal — is shorter than
            the store's minimum length.
    """
    if key not in raw_step:
        return None
    value = raw_step[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string when present")
    token_value = value.strip()
    _validate_placeholder_syntax(
        token_value,
        location=f"{location}.{key}",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    if not _PLACEHOLDER_FIND_PATTERN.search(token_value) and len(token_value) < _PSU_AUTH_MIN_STATE_LENGTH:
        raise ManifestError(
            f"{location}.{key} must be at least {_PSU_AUTH_MIN_STATE_LENGTH} characters "
            "(matches AuthSessionStore minimum caller-supplied entropy)"
        )
    return token_value


def _parse_psu_optional_request_object(
    raw_step: dict[str, JsonValue],
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> RequestObjectValue | None:
    """Parse the optional ``requestObject`` field on a PSU authorisation step.

    Two backward-compatible shapes are accepted:

    * a legacy opaque JWT string, optionally containing placeholders, and
    * a typed ``{"source": "fapi-signing"}`` directive that tells the
      executor to generate a PS256 JAR request object at runtime.

    Args:
        raw_step: Raw JSON object for the PSU step.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        The stripped JWT string, a typed generated-request-object directive,
        or ``None`` if the key was absent.

    Raises:
        ManifestError: If ``requestObject`` is present but is not a
            supported string/object shape, contains malformed placeholders,
            references a forward step, or declares an unknown directive.
    """
    if "requestObject" not in raw_step:
        return None
    value = raw_step["requestObject"]
    if isinstance(value, str):
        if not value.strip():
            raise ManifestError(f"{location}.requestObject must be a non-empty string when present")
        request_object = value.strip()
        _validate_placeholder_syntax(
            request_object,
            location=f"{location}.requestObject",
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
        return request_object
    if not isinstance(value, dict):
        raise ManifestError(
            f"{location}.requestObject must be either a non-empty string or a JSON object directive when present"
        )
    return _parse_generated_request_object(
        value,
        location=f"{location}.requestObject",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )


def _parse_generated_request_object(
    raw_request_object: dict[str, JsonValue],
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> GeneratedRequestObject:
    """Parse a typed runtime-generated PSU ``requestObject`` directive.

    Args:
        raw_request_object: Raw JSON object representing the directive.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for placeholder validation).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Parsed generated-request-object directive.

    Raises:
        ManifestError: If the directive contains unknown keys, malformed
            placeholders, or an unsupported source selector.
    """
    _reject_unknown_keys(
        raw_request_object,
        allowed_keys={"source", "audience", "openbankingIntentId"},
        location=location,
    )
    source = _required_string(raw_request_object, "source", location=location)
    _validate_constant_manifest_string(source, location=f"{location}.source", seen_ids=seen_ids)
    if source != "fapi-signing":
        raise ManifestError(f"{location}.source must be 'fapi-signing'")
    raw_audience = raw_request_object.get("audience")
    if raw_audience is not None and (not isinstance(raw_audience, str) or not raw_audience.strip()):
        raise ManifestError(f"{location}.audience must be a non-empty string when present")
    audience = raw_audience.strip() if isinstance(raw_audience, str) else None
    if audience is not None:
        _validate_placeholder_syntax(
            audience,
            location=f"{location}.audience",
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
    raw_openbanking_intent_id = raw_request_object.get("openbankingIntentId")
    if raw_openbanking_intent_id is not None and (
        not isinstance(raw_openbanking_intent_id, str) or not raw_openbanking_intent_id.strip()
    ):
        raise ManifestError(f"{location}.openbankingIntentId must be a non-empty string when present")
    openbanking_intent_id = raw_openbanking_intent_id.strip() if isinstance(raw_openbanking_intent_id, str) else None
    if openbanking_intent_id is not None:
        _validate_placeholder_syntax(
            openbanking_intent_id,
            location=f"{location}.openbankingIntentId",
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
    return GeneratedRequestObject(
        source="fapi-signing",
        audience=audience,
        openbanking_intent_id=openbanking_intent_id,
    )


def _parse_optional_token_endpoint_auth_policy(
    raw_step: dict[str, JsonValue],
    *,
    request: ManifestRequest,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> TokenEndpointAuthPolicy | None:
    """Parse the optional token-endpoint auth directive on an HTTP step.

    Args:
        raw_step: Raw JSON object for the HTTP step.
        request: Parsed request for the HTTP step.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for placeholder validation).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Parsed token-endpoint auth policy, or ``None`` if absent.

    Raises:
        ManifestError: If the directive is not a JSON object, contains
            unknown keys, malformed placeholders, an unsupported source, or
            is used outside a POST form request.
    """
    del allowed_test_value_keys
    if "tokenEndpointAuthPolicy" not in raw_step:
        return None
    raw_policy = raw_step["tokenEndpointAuthPolicy"]
    if not isinstance(raw_policy, dict):
        raise ManifestError(f"{location}.tokenEndpointAuthPolicy must be a JSON object when present")
    _reject_unknown_keys(raw_policy, allowed_keys={"source"}, location=f"{location}.tokenEndpointAuthPolicy")
    source = _required_string(raw_policy, "source", location=f"{location}.tokenEndpointAuthPolicy")
    _validate_constant_manifest_string(
        source,
        location=f"{location}.tokenEndpointAuthPolicy.source",
        seen_ids=seen_ids,
    )
    if source != "fapi-signing":
        raise ManifestError(f"{location}.tokenEndpointAuthPolicy.source must be 'fapi-signing'")
    if request.method != "POST" or not isinstance(request.body, FormBody):
        raise ManifestError(f"{location}.tokenEndpointAuthPolicy is only valid on POST requests with a form body")
    return TokenEndpointAuthPolicy(source="fapi-signing")


def _validate_constant_manifest_string(value: str, *, location: str, seen_ids: set[str]) -> None:
    """Reject placeholders inside constant manifest discriminator fields.

    Args:
        value: Constant manifest string field under validation.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for placeholder validation).

    Raises:
        ManifestError: If the string contains any placeholder token.
    """
    _validate_placeholder_syntax(value, location=location, seen_ids=seen_ids)
    if _PLACEHOLDER_FIND_PATTERN.search(value):
        raise ManifestError(f"{location} must not contain placeholders")


def _parse_psu_timeout_seconds(raw_step: dict[str, JsonValue], *, location: str) -> int:
    """Parse the optional ``timeoutSeconds`` field on a PSU authorisation step.

    Args:
        raw_step: Raw JSON object for the PSU step.
        location: Dot-path location string used in error messages.

    Returns:
        The validated integer timeout. Returns the module-level default
        when the key is absent.

    Raises:
        ManifestError: If the value is present but is not a JSON integer
            in the inclusive range
            ``[_PSU_AUTH_TIMEOUT_MIN_SECONDS, _PSU_AUTH_TIMEOUT_MAX_SECONDS]``.
    """
    if "timeoutSeconds" not in raw_step:
        return _PSU_AUTH_DEFAULT_TIMEOUT_SECONDS
    value = raw_step["timeoutSeconds"]
    # Reject ``bool`` (subclass of ``int``) so ``true``/``false`` cannot
    # silently become 1/0 second timeouts on a misauthored manifest.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{location}.timeoutSeconds must be a JSON integer when present")
    if value < _PSU_AUTH_TIMEOUT_MIN_SECONDS or value > _PSU_AUTH_TIMEOUT_MAX_SECONDS:
        raise ManifestError(
            f"{location}.timeoutSeconds must be between {_PSU_AUTH_TIMEOUT_MIN_SECONDS} "
            f"and {_PSU_AUTH_TIMEOUT_MAX_SECONDS} inclusive (got: {value})"
        )
    return value


def _parse_psu_expected_authorization_response(
    raw_step: dict[str, JsonValue],
    *,
    location: str,
    mode: PsuAuthorizationMode,
    seen_ids: set[str],
) -> PsuExpectedAuthorizationResponse | None:
    """Parse optional expected direct authorisation rejection metadata.

    Args:
        raw_step: Raw JSON object for the PSU step.
        location: Dot-path location string used in error messages.
        mode: Parsed PSU execution mode for this step.
        seen_ids: Set of step ids already parsed (for placeholder validation).

    Returns:
        Parsed expected-response directive, or ``None`` when absent.

    Raises:
        ManifestError: If the field is present on non-headless PSU steps,
            is not a JSON object, contains unknown keys, uses unsupported
            response type, or declares a non-integer/non-rejection status.
    """
    if "expectedAuthorizationResponse" not in raw_step:
        return None
    if mode != "headless":
        raise ManifestError(f"{location}.expectedAuthorizationResponse is only valid when mode is 'headless'")

    field_location = f"{location}.expectedAuthorizationResponse"
    raw_expected_response = raw_step["expectedAuthorizationResponse"]
    if not isinstance(raw_expected_response, dict):
        raise ManifestError(f"{field_location} must be a JSON object when present")
    _reject_unknown_keys(raw_expected_response, allowed_keys={"type", "expected"}, location=field_location)
    response_type = _required_string(raw_expected_response, "type", location=field_location)
    _validate_constant_manifest_string(response_type, location=f"{field_location}.type", seen_ids=seen_ids)
    if response_type != "http_status":
        raise ManifestError(f"{field_location}.type must be 'http_status'")

    expected = raw_expected_response.get("expected")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ManifestError(f"{field_location}.expected must be a JSON integer")
    if expected < 400 or expected > 599:
        raise ManifestError(f"{field_location}.expected must be between 400 and 599 inclusive (got: {expected})")
    return PsuExpectedAuthorizationResponse(type="http_status", expected=expected)


def _parse_v1_request(
    raw_request: dict[str, JsonValue],
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> ManifestRequest:
    """Parse and validate a v1 manifest step request object.

    Unlike the v0 parser, this allows ``${...}`` placeholders in the URL field,
    header values, and body string leaves. Supports GET, POST, PUT, PATCH, and
    DELETE methods. Body is rejected on GET requests.

    Args:
        raw_request: Raw JSON object expected to contain ``method``, ``url``,
            and optionally ``headers``, ``body``, and ``detachedJws``.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        Validated request with method, URL, optional headers, optional body,
        and optional detached JWS policy (body may contain placeholders in
        string leaves).

    Raises:
        ManifestError: If required fields are missing, invalid, or placeholders
            reference forward steps.
    """
    _reject_unknown_keys(
        raw_request,
        allowed_keys={"method", "url", "headers", "body", "detachedJws"},
        location=location,
    )
    method = _required_v1_method(raw_request, location=location)
    url = _required_string(raw_request, "url", location=location)

    _validate_placeholder_syntax(
        url,
        location=f"{location}.url",
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )

    if not _PLACEHOLDER_FIND_PATTERN.search(url):
        try:
            validate_https_url(url, label=f"{location}.url")
        except HttpsUrlValidationError as error:
            raise ManifestError(str(error)) from error

    headers = _parse_v1_headers(
        raw_request,
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    body = _parse_v1_body(
        raw_request,
        method=method,
        location=location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    detached_jws = _parse_optional_detached_jws(raw_request, method=method, location=location, seen_ids=seen_ids)

    return ManifestRequest(method=method, url=url, headers=headers, body=body, detached_jws=detached_jws)


def _parse_optional_detached_jws(
    raw_request: dict[str, JsonValue], *, method: RequestMethod, location: str, seen_ids: set[str]
) -> DetachedJwsPolicy | None:
    """Parse the optional detached-JWS policy for a request.

    Args:
        raw_request: Raw request JSON object potentially containing
            ``detachedJws``.
        method: Parsed HTTP method for the request.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for placeholder validation).

    Returns:
        Parsed detached-JWS policy, or ``None`` when the request does not opt
        into detached signing.

    Raises:
        ManifestError: If the directive shape is invalid, uses an unsupported
            source, applies to an unsupported HTTP method, or contains invalid
            placeholders.
    """
    if "detachedJws" not in raw_request:
        return None
    if method not in {"POST", "PUT", "PATCH"}:
        raise ManifestError(f"{location}.detachedJws is only valid on POST, PUT, or PATCH requests")

    raw_policy = raw_request["detachedJws"]
    policy_location = f"{location}.detachedJws"
    if not isinstance(raw_policy, dict):
        raise ManifestError(f"{policy_location} must be a JSON object when present")
    _reject_unknown_keys(raw_policy, allowed_keys={"source"}, location=policy_location)

    source = _required_string(raw_policy, "source", location=policy_location)
    _validate_placeholder_syntax(source, location=f"{policy_location}.source", seen_ids=seen_ids)
    if source != "fapi-signing":
        raise ManifestError(f"{policy_location}.source must be 'fapi-signing'")
    return DetachedJwsPolicy(source="fapi-signing")


def _validate_placeholder_syntax(
    value: str,
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str] = frozenset(),
) -> None:
    """Validate that all ``${...}`` tokens in a string are syntactically correct.

    Checks that each placeholder matches the canonical grammar and that any
    referenced step id exists in ``seen_ids`` (i.e. no forward references).

    Args:
        value: String potentially containing ``${...}`` placeholders.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Raises:
        ManifestError: If a placeholder is malformed or references a forward step.
    """
    matched_tokens = list(_PLACEHOLDER_FIND_PATTERN.finditer(value))
    if value.count("${") > len(matched_tokens):
        raise ManifestError(f"{location} contains an unterminated placeholder (missing closing '}}')")
    for match in matched_tokens:
        token = match.group(0)
        if _CONFIG_PLACEHOLDER_PATTERN.fullmatch(token) is not None:
            continue
        if _TOKEN_PLACEHOLDER_PATTERN.fullmatch(token) is not None:
            continue
        test_values_match = _TEST_VALUES_PLACEHOLDER_PATTERN.fullmatch(token)
        if test_values_match is not None:
            key = test_values_match.group(1)
            if key not in allowed_test_value_keys:
                if allowed_test_value_keys:
                    raise ManifestError(
                        f"{location} contains undeclared testValues key: {token} "
                        f"(declared keys: {', '.join(sorted(allowed_test_value_keys))})"
                    )
                raise ManifestError(
                    f"{location} contains unsupported placeholder: {token} "
                    "(no testValueProfiles declared in this manifest)"
                )
            continue
        valid_match = _STEP_PLACEHOLDER_PATTERN.fullmatch(token)
        if valid_match is None:
            if token.startswith("${config."):
                raise ManifestError(
                    f"{location} contains unsupported config placeholder: {token} "
                    "(allowed: ${config.discoveryUrl}, ${config.environment}, "
                    "${config.oauth.clientId}, ${config.oauth.redirectUri}, "
                    "${config.oauth.authorizationEndpoint}, ${config.oauth.openBankingIntentId}, "
                    "${config.oauth.resourceBaseUrl})"
                )
            if token.startswith("${tokens."):
                raise ManifestError(
                    f"{location} contains unsupported token placeholder: {token} "
                    "(allowed: ${tokens.<tokenId>.access_token})"
                )
            if token.startswith("${testValues."):
                raise ManifestError(
                    f"{location} contains unsupported testValues placeholder: {token} "
                    "(no testValueProfiles declared in this manifest)"
                )
            raise ManifestError(f"{location} contains malformed placeholder: {token}")
        referenced_id = valid_match.group(1)
        if referenced_id not in seen_ids:
            raise ManifestError(f"{location} references undefined step '{referenced_id}'")


def _required_v1_method(raw_config: dict[str, JsonValue], *, location: str) -> RequestMethod:
    """Extract and validate the request method for a v1 step.

    Accepts GET, POST, PUT, PATCH, and DELETE.

    Args:
        raw_config: The parent JSON object containing a ``method`` field.
        location: Dot-path location string used in error messages.

    Returns:
        A validated HTTP method literal.

    Raises:
        ManifestError: If the method is missing or not one of the supported values.
    """
    method = _required_string(raw_config, "method", location=location)
    allowed: set[str] = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method not in allowed:
        raise ManifestError(f"{location}.method must be one of: GET, POST, PUT, PATCH, DELETE")
    return cast(RequestMethod, method)


def _parse_v1_headers(
    raw_request: dict[str, JsonValue],
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> dict[str, str] | None:
    """Parse and validate optional headers from a v1 step request.

    Header names must be RFC 7230 tokens. Header values must be non-empty
    strings (may contain ``${...}`` placeholders).

    Args:
        raw_request: Raw request JSON object potentially containing ``headers``.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        A dict mapping header names to string values, or ``None`` if no
        headers key is present.

    Raises:
        ManifestError: If header names or values are invalid, or if
            placeholders reference forward steps.
    """
    if "headers" not in raw_request:
        return None
    raw_headers = raw_request["headers"]
    if not isinstance(raw_headers, dict):
        raise ManifestError(f"{location}.headers must be a JSON object")

    headers: dict[str, str] = {}
    for name, value in raw_headers.items():
        header_location = f"{location}.headers.{name}"
        if not _HEADER_NAME_PATTERN.match(name):
            raise ManifestError(f"{header_location} is not a valid HTTP header name (RFC 7230 token)")
        if not isinstance(value, str):
            raise ManifestError(f"{header_location} must be a string value")
        validate_header_value(value, location=header_location)
        _validate_placeholder_syntax(
            value,
            location=header_location,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
        headers[name] = value
    return headers


def _parse_v1_body(
    raw_request: dict[str, JsonValue],
    *,
    method: RequestMethod,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> ManifestBody | None:
    """Parse and validate the optional body from a v1 step request.

    Body is rejected on GET requests. Two body shapes are accepted:

    1. **Bare JSON value** (no ``encoding`` tag): parsed as ``JsonBody``.
       Preserves DL-0013 back-compat — any v1 manifest written before
       DL-0014 keeps working without change.
    2. **Tagged dict** ``{"encoding": "json" | "form", ...}``: parsed as
       ``JsonBody`` (with required ``value``) or ``FormBody`` (with
       required non-empty ``fields`` mapping of string→string).

    The tagged-vs-bare discrimination is conservative: only a dict that
    contains an ``encoding`` key is treated as tagged. A bare dict without
    ``encoding`` is still a JSON body — manifests that happen to send a
    JSON object with no ``encoding`` field continue to work.

    Placeholder syntax is validated in:
    - every string leaf of a JSON body (recursively, via
      ``_validate_placeholders_in_structure``), and
    - every value of a form body's ``fields`` mapping.

    Args:
        raw_request: Raw request JSON object potentially containing ``body``.
        method: The parsed HTTP method (used to reject body on GET).
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        A ``JsonBody`` or ``FormBody``, or ``None`` if no body key is present.

    Raises:
        ManifestError: If body is present on a GET request, the tagged shape
            is malformed, ``encoding`` is unknown, ``fields`` are empty or
            contain non-string values, or any placeholder is invalid.
    """
    if "body" not in raw_request:
        return None
    if method == "GET":
        raise ManifestError(f"{location}: GET requests must not declare a body")
    body = raw_request["body"]
    if body is None:
        raise ManifestError(f"{location}.body must not be null (omit the key to send no body)")

    body_location = f"{location}.body"
    if isinstance(body, dict) and "encoding" in body:
        return _parse_tagged_body(
            body,
            location=body_location,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )

    _validate_placeholders_in_structure(
        body,
        location=body_location,
        seen_ids=seen_ids,
        allowed_test_value_keys=allowed_test_value_keys,
    )
    return JsonBody(value=copy.deepcopy(body))


def _parse_tagged_body(
    body: dict[str, JsonValue],
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> ManifestBody:
    """Parse a ``{"encoding": ..., ...}`` tagged body dict into a typed body.

    Args:
        body: The raw body dict, known to contain an ``encoding`` key.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Returns:
        A typed ``JsonBody`` or ``FormBody``.

    Raises:
        ManifestError: If ``encoding`` is unknown, required tagged-shape keys
            are missing, ``fields`` are empty or non-string-valued, or any
            placeholder inside the body is invalid.
    """
    encoding = body.get("encoding")
    if encoding == "json":
        _reject_unknown_keys(body, allowed_keys={"encoding", "value"}, location=location)
        if "value" not in body:
            raise ManifestError(f"{location}: tagged JSON body must include a 'value' key")
        value = body["value"]
        if value is None:
            raise ManifestError(f"{location}.value must not be null (omit the body key to send no body)")
        _validate_placeholders_in_structure(
            value,
            location=f"{location}.value",
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
        return JsonBody(value=copy.deepcopy(value))
    if encoding == "form":
        _reject_unknown_keys(body, allowed_keys={"encoding", "fields"}, location=location)
        if "fields" not in body:
            raise ManifestError(f"{location}: form body must include a 'fields' object")
        raw_fields = body["fields"]
        if not isinstance(raw_fields, dict):
            raise ManifestError(f"{location}.fields must be a JSON object")
        if not raw_fields:
            raise ManifestError(f"{location}.fields must not be empty")
        validated_fields: dict[str, str] = {}
        for field_name, field_value in raw_fields.items():
            field_location = f"{location}.fields.{field_name}"
            if not field_name:
                raise ManifestError(f"{location}.fields contains an empty field name")
            if not isinstance(field_value, str):
                raise ManifestError(f"{field_location} must be a string value")
            _validate_placeholder_syntax(
                field_value,
                location=field_location,
                seen_ids=seen_ids,
                allowed_test_value_keys=allowed_test_value_keys,
            )
            validated_fields[field_name] = field_value
        return FormBody(fields=MappingProxyType(validated_fields))
    raise ManifestError(f"{location}.encoding must be one of: json, form (got: {encoding!r})")


def _validate_placeholders_in_structure(
    value: JsonValue,
    *,
    location: str,
    seen_ids: set[str],
    allowed_test_value_keys: frozenset[str],
) -> None:
    """Recursively validate placeholders in all string leaves of a JSON structure.

    Walks dicts and lists depth-first, checking each string leaf for valid
    ``${...}`` placeholder syntax and forward-reference violations.

    Args:
        value: JSON value (possibly nested) to validate.
        location: Dot-path location string used in error messages.
        seen_ids: Set of step ids already parsed (for forward-ref detection).
        allowed_test_value_keys: Test-value keys declared by the manifest root.

    Raises:
        ManifestError: If any string leaf contains malformed or forward-referencing
            placeholders.
    """
    if isinstance(value, str):
        _validate_placeholder_syntax(
            value,
            location=location,
            seen_ids=seen_ids,
            allowed_test_value_keys=allowed_test_value_keys,
        )
    elif isinstance(value, dict):
        for key, child in value.items():
            _validate_placeholders_in_structure(
                child,
                location=f"{location}.{key}",
                seen_ids=seen_ids,
                allowed_test_value_keys=allowed_test_value_keys,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_placeholders_in_structure(
                child,
                location=f"{location}[{index}]",
                seen_ids=seen_ids,
                allowed_test_value_keys=allowed_test_value_keys,
            )


def _parse_test(raw_test: dict[str, JsonValue], *, index: int) -> ManifestTest:
    """Parse a single test entry from the manifest tests array.

    Args:
        raw_test: Raw JSON object representing one manifest test.
        index: Zero-based position in the tests array, used for error locations.

    Returns:
        Validated manifest test with request, assertions, and optional follow-up.

    Raises:
        ManifestError: If required fields are missing or contain invalid values.
    """
    location = f"tests[{index}]"
    _reject_unknown_keys(
        raw_test,
        allowed_keys={"id", "name", "request", "assertions", "followUp"},
        location=location,
    )

    assertions = _required_object_array(raw_test, "assertions", location=location)
    has_follow_up = "followUp" in raw_test
    raw_follow_up = raw_test["followUp"] if has_follow_up else None

    test_id = _required_string(raw_test, "id", location=location)
    _validate_step_id(test_id, location=location)

    return ManifestTest(
        id=test_id,
        name=_required_string(raw_test, "name", location=location),
        request=_parse_request(
            _required_object(raw_test, "request", location=location),
            location=f"{location}.request",
        ),
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
        follow_up=_parse_follow_up(raw_follow_up, location=f"{location}.followUp") if has_follow_up else None,
    )


def _parse_request(raw_request: dict[str, JsonValue], *, location: str) -> ManifestRequest:
    """Parse and validate a manifest test request object.

    Args:
        raw_request: Raw JSON object expected to contain ``method`` and ``url``.
        location: Dot-path location string used in error messages.

    Returns:
        Validated request with a GET method and a hardened HTTPS URL.

    Raises:
        ManifestError: If required fields are missing, invalid, or unknown keys
            are present.
    """
    _reject_unknown_keys(raw_request, allowed_keys={"method", "url"}, location=location)
    return ManifestRequest(
        method=_required_get_method(raw_request, location=location),
        url=_required_https_url(raw_request, "url", location=location),
    )


def _parse_follow_up(raw_follow_up: JsonValue, *, location: str) -> ManifestFollowUp:
    """Parse a JWKS follow-up step from a manifest test.

    Validates that the follow-up declares a ``jwks`` type with
    ``response.body.jwks_uri`` as the URL source, then recursively parses
    the nested request and assertions.

    Args:
        raw_follow_up: Raw JSON value expected to be the followUp object.
        location: Manifest path prefix for error messages.

    Returns:
        Validated follow-up step ready for execution.

    Raises:
        ManifestError: If the follow-up shape is invalid or unsupported.
    """
    if not isinstance(raw_follow_up, dict):
        raise ManifestError(f"{location} must be a JSON object")
    _reject_unknown_keys(
        raw_follow_up,
        allowed_keys={"type", "urlSource", "request", "assertions"},
        location=location,
    )

    follow_up_type = _required_string(raw_follow_up, "type", location=location)
    if follow_up_type != "jwks":
        raise ManifestError(f"{location}.type must be jwks")

    url_source = _required_string(raw_follow_up, "urlSource", location=location)
    if url_source != "response.body.jwks_uri":
        raise ManifestError(f"{location}.urlSource must be response.body.jwks_uri")

    assertions = _required_object_array(raw_follow_up, "assertions", location=location)

    return ManifestFollowUp(
        type="jwks",
        url_source="response.body.jwks_uri",
        request=_parse_follow_up_request(
            _required_object(raw_follow_up, "request", location=location),
            location=f"{location}.request",
        ),
        assertions=tuple(
            _parse_assertion(raw_assertion, location=f"{location}.assertions[{assertion_index}]")
            for assertion_index, raw_assertion in enumerate(assertions)
        ),
    )


def _parse_follow_up_request(raw_request: dict[str, JsonValue], *, location: str) -> FollowUpRequest:
    """Parse the request object inside a manifest follow-up step.

    Args:
        raw_request: Raw JSON object expected to contain only ``method``.
        location: Dot-path location string used in error messages.

    Returns:
        Validated follow-up request with a GET method.

    Raises:
        ManifestError: If the method is missing, not GET, or unknown keys
            are present.
    """
    _reject_unknown_keys(raw_request, allowed_keys={"method"}, location=location)
    return FollowUpRequest(method=_required_get_method(raw_request, location=location))


def _parse_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> ManifestAssertion:
    """Parse and dispatch a single assertion by its type discriminator.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A typed assertion dataclass.

    Raises:
        ManifestError: If the assertion type is missing, unsupported, or
            required fields are invalid.
    """
    assertion_type = _required_assertion_type(raw_assertion, location=location)
    if assertion_type == "http_status":
        _reject_unknown_keys(raw_assertion, allowed_keys={"type", "expected"}, location=location)
        return HttpStatusAssertion(type="http_status", expected=_required_status_code(raw_assertion, location=location))
    if assertion_type == "json_field":
        return _parse_json_field_assertion(raw_assertion, location=location)
    if assertion_type == "header":
        return _parse_header_assertion(raw_assertion, location=location)
    if assertion_type == "response_schema":
        return _parse_response_schema_assertion(raw_assertion, location=location)
    if assertion_type == "ob_error_code":
        return _parse_ob_error_code_assertion(raw_assertion, location=location)
    if assertion_type == "response_signature":
        return _parse_response_signature_assertion(raw_assertion, location=location)
    # Defensive: _required_assertion_type already constrains assertion_type to the
    # AssertionType literal, but an explicit raise removes the implicit None
    # fall-through and guards against future literal additions.
    raise ManifestError(f"{location}.type has unexpected value: {assertion_type!r}")


def _parse_ob_error_code_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> ObErrorCodeAssertion:
    """Parse an ``ob_error_code`` assertion from a raw manifest assertion object.

    Args:
        raw_assertion: Raw JSON object for the assertion.
        location: Dot-path location string for error messages.

    Returns:
        Parsed ``ObErrorCodeAssertion`` with the acceptable error-code set.

    Raises:
        ManifestError: If ``codes`` is absent, not a non-empty array, or contains
            non-string items.
    """
    _reject_unknown_keys(raw_assertion, allowed_keys={"type", "codes"}, location=location)
    if "codes" not in raw_assertion:
        raise ManifestError(f"{location}.codes is required for ob_error_code assertions")
    raw_codes = raw_assertion["codes"]
    if not isinstance(raw_codes, list) or not raw_codes:
        raise ManifestError(f"{location}.codes must be a non-empty array for ob_error_code assertions")
    codes = []
    for i, code in enumerate(raw_codes):
        if not isinstance(code, str) or not code.strip():
            raise ManifestError(f"{location}.codes[{i}] must be a non-empty string")
        codes.append(code.strip())
    return ObErrorCodeAssertion(type="ob_error_code", codes=tuple(codes))


def _parse_response_signature_assertion(
    raw_assertion: dict[str, JsonValue],
    *,
    location: str,
) -> ResponseSignatureAssertion:
    """Parse a response-signature assertion from a raw manifest object.

    Args:
        raw_assertion: Raw JSON object for the assertion.
        location: Dot-path location string for error messages.

    Returns:
        Parsed response-signature assertion.

    Raises:
        ManifestError: If required fields are absent, malformed, or unsafe.
    """
    _reject_unknown_keys(raw_assertion, allowed_keys={"type", "jwksStepId", "headerName"}, location=location)
    jwks_step_id = _required_string(raw_assertion, "jwksStepId", location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(jwks_step_id):
        raise ManifestError(f"{location}.jwksStepId must not contain placeholders")
    header_name = (
        _required_string(raw_assertion, "headerName", location=location)
        if "headerName" in raw_assertion
        else "x-jws-signature"
    )
    if _PLACEHOLDER_FIND_PATTERN.search(header_name):
        raise ManifestError(f"{location}.headerName must not contain placeholders")
    if not _HEADER_NAME_PATTERN.fullmatch(header_name):
        raise ManifestError(f"{location}.headerName is not a valid HTTP header name")
    return ResponseSignatureAssertion(
        type="response_signature",
        jwks_step_id=jwks_step_id,
        header_name=header_name,
    )


def _parse_json_field_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> JsonFieldAssertion:
    """Parse a ``json_field`` assertion with rule-specific validation.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated :class:`JsonFieldAssertion`.

    Raises:
        ManifestError: If the assertion contains unsupported keys or misses
            required rule-specific fields.
    """
    rule = _required_json_field_rule(raw_assertion, location=location)
    allowed_keys = {"type", "path", "rule"}
    if rule == "equals":
        allowed_keys.add("value")
    elif rule == "one_of":
        allowed_keys.add("values")
    elif rule == "min_items":
        allowed_keys.add("minItems")
    elif rule == "all_items_have_field" or rule == "all_items_absent_field":
        allowed_keys.add("field")
    _reject_unknown_keys(raw_assertion, allowed_keys=allowed_keys, location=location)

    assertion = JsonFieldAssertion(
        type="json_field",
        path=_required_string(raw_assertion, "path", location=location),
        rule=rule,
    )
    if rule == "equals":
        return JsonFieldAssertion(
            type=assertion.type,
            path=assertion.path,
            rule=assertion.rule,
            value=_required_json_compatible_value(
                raw_assertion,
                key="value",
                location=location,
                missing_message=f"{location}.value must be present for json_field rule equals",
            ),
        )
    if rule == "one_of":
        return JsonFieldAssertion(
            type=assertion.type,
            path=assertion.path,
            rule=assertion.rule,
            values=_required_json_compatible_values(raw_assertion, location=location),
        )
    if rule == "min_items":
        return JsonFieldAssertion(
            type=assertion.type,
            path=assertion.path,
            rule=assertion.rule,
            min_items=_required_min_items(raw_assertion, location=location),
        )
    if rule == "all_items_have_field" or rule == "all_items_absent_field":
        return JsonFieldAssertion(
            type=assertion.type,
            path=assertion.path,
            rule=assertion.rule,
            field=_required_string(raw_assertion, "field", location=location),
        )
    return assertion


def _parse_header_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> HeaderAssertion:
    """Parse a ``header`` assertion with rule-specific validation.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated :class:`HeaderAssertion`.

    Raises:
        ManifestError: If the assertion contains unsupported keys or misses
            required rule-specific fields.
    """
    rule = _required_header_rule(raw_assertion, location=location)
    value: str | None
    match rule:
        case "equals" | "contains":
            _reject_unknown_keys(raw_assertion, allowed_keys={"type", "name", "rule", "value"}, location=location)
            value = _required_header_rule_value(raw_assertion, rule=rule, location=location)
        case "matches_request_header":
            _reject_unknown_keys(
                raw_assertion,
                allowed_keys={"type", "name", "rule", "requestHeader"},
                location=location,
            )
            value = None
        case _:
            _reject_unknown_keys_with_singular_message(
                raw_assertion,
                allowed_keys={"type", "name", "rule"},
                location=location,
            )
            value = None

    name = _required_string(raw_assertion, "name", location=location)
    if not _HEADER_NAME_PATTERN.fullmatch(name):
        raise ManifestError(f"{location}.name is not a valid HTTP header name")

    # Resolve request_header for matches_request_header rule.
    request_header: str | None = None
    if rule == "matches_request_header":
        if "requestHeader" not in raw_assertion:
            # Default: use the response header name as the request header name.
            request_header = name
        else:
            raw_rh = raw_assertion.get("requestHeader")
            if not isinstance(raw_rh, str) or not raw_rh.strip():
                raise ManifestError(f"{location}.requestHeader must be a non-empty string")
            stripped_rh = raw_rh.strip()
            if not _HEADER_NAME_PATTERN.fullmatch(stripped_rh):
                raise ManifestError(f"{location}.requestHeader is not a valid HTTP header name")
            request_header = stripped_rh

    return HeaderAssertion(type="header", name=name, rule=rule, value=value, request_header=request_header)


_ALLOWED_RESPONSE_SCHEMA_DOCUMENTS: set[str] = {
    "ob-read-write-v4.0-account-info-openapi",
    "ob-read-write-v4.0-payment-initiation-openapi",
    "ob-read-write-v4.0.1-account-info-openapi",
}
"""Allowlisted bundled standards documents addressable by ``response_schema`` assertions."""


def _parse_response_schema_assertion(raw_assertion: dict[str, JsonValue], *, location: str) -> ResponseSchemaAssertion:
    """Parse a ``response_schema`` assertion with source/document controls.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated :class:`ResponseSchemaAssertion`.

    Raises:
        ManifestError: If required fields are missing, unknown keys are
            present, source/document are unsupported, or schema reference
            and inline schema forms are misconfigured.
    """
    _reject_unknown_keys(
        raw_assertion,
        allowed_keys={"type", "source", "document", "schemaRef", "schema", "bodyPath"},
        location=location,
    )

    source = _required_response_schema_source(raw_assertion, location=location)
    document = _required_response_schema_document(raw_assertion, location=location)
    body_path = _optional_response_schema_body_path(raw_assertion, location=location)

    has_schema_ref = "schemaRef" in raw_assertion
    has_inline_schema = "schema" in raw_assertion
    if has_schema_ref == has_inline_schema:
        raise ManifestError(f"{location} must provide exactly one of schemaRef or schema")

    if has_schema_ref:
        schema_ref = _required_string(raw_assertion, "schemaRef", location=location)
        if _PLACEHOLDER_FIND_PATTERN.search(schema_ref):
            raise ManifestError(f"{location}.schemaRef must not contain placeholders")
        return ResponseSchemaAssertion(
            type="response_schema",
            source=source,
            document=document,
            schema_ref=schema_ref,
            body_path=body_path,
        )

    inline_schema = raw_assertion["schema"]
    if not isinstance(inline_schema, dict) or not inline_schema:
        raise ManifestError(f"{location}.schema must be a non-empty JSON object")
    schema_copy = copy.deepcopy(inline_schema)
    return ResponseSchemaAssertion(
        type="response_schema",
        source=source,
        document=document,
        schema=MappingProxyType(schema_copy),
        body_path=body_path,
    )


def _required_response_schema_source(raw_assertion: dict[str, JsonValue], *, location: str) -> ResponseSchemaSource:
    """Extract and validate the source selector for a ``response_schema`` assertion.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        The validated schema source selector.

    Raises:
        ManifestError: If source is missing, contains placeholders, or is
            unsupported.
    """
    source = _required_string(raw_assertion, "source", location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(source):
        raise ManifestError(f"{location}.source must not contain placeholders")
    if source == "bundled_openapi":
        return "bundled_openapi"
    raise ManifestError(f"{location}.source must be one of: bundled_openapi")


def _required_response_schema_document(raw_assertion: dict[str, JsonValue], *, location: str) -> str:
    """Extract and validate the bundled document id for ``response_schema``.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        The validated allowlisted bundled document id.

    Raises:
        ManifestError: If document is missing, contains placeholders, or is
            not allowlisted.
    """
    document = _required_string(raw_assertion, "document", location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(document):
        raise ManifestError(f"{location}.document must not contain placeholders")
    if document not in _ALLOWED_RESPONSE_SCHEMA_DOCUMENTS:
        allowed = ", ".join(sorted(_ALLOWED_RESPONSE_SCHEMA_DOCUMENTS))
        raise ManifestError(f"{location}.document must be one of: {allowed}")
    return document


def _optional_response_schema_body_path(raw_assertion: dict[str, JsonValue], *, location: str) -> str | None:
    """Extract and validate the optional ``bodyPath`` selector.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        Stripped ``bodyPath`` when present, otherwise ``None``.

    Raises:
        ManifestError: If ``bodyPath`` is present but blank or contains
            placeholders.
    """
    if "bodyPath" not in raw_assertion:
        return None
    body_path = _required_string(raw_assertion, "bodyPath", location=location)
    if _PLACEHOLDER_FIND_PATTERN.search(body_path):
        raise ManifestError(f"{location}.bodyPath must not contain placeholders")
    return body_path


def _required_assertion_type(raw_assertion: dict[str, JsonValue], *, location: str) -> AssertionType:
    """Extract and validate the assertion type discriminator.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated assertion type literal.

    Raises:
        ManifestError: If the assertion type is missing or unsupported.
    """
    assertion_type = _required_string(raw_assertion, "type", location=location)
    if assertion_type == "http_status":
        return "http_status"
    if assertion_type == "json_field":
        return "json_field"
    if assertion_type == "header":
        return "header"
    if assertion_type == "response_schema":
        return "response_schema"
    if assertion_type == "ob_error_code":
        return "ob_error_code"
    if assertion_type == "response_signature":
        return "response_signature"
    raise ManifestError(
        f"{location}.type must be one of: http_status, json_field, header, "
        "response_schema, ob_error_code, response_signature"
    )


def _required_get_method(raw_config: dict[str, JsonValue], *, location: str) -> Literal["GET"]:
    """Extract and validate that the request method is GET.

    Args:
        raw_config: The parent JSON object containing a ``method`` field.
        location: Dot-path location string used in error messages.

    Returns:
        The literal string ``"GET"``.

    Raises:
        ManifestError: If the method is missing or not GET.
    """
    method = _required_string(raw_config, "method", location=location)
    if method != "GET":
        raise ManifestError(f"{location}.method must be GET")
    return "GET"


def _required_json_field_rule(raw_assertion: dict[str, JsonValue], *, location: str) -> JsonFieldRule:
    """Extract and validate the JSON field assertion rule.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated JSON field rule literal.

    Raises:
        ManifestError: If the JSON field rule is missing or unsupported.
    """
    rule = _required_string(raw_assertion, "rule", location=location)
    if rule == "required":
        return "required"
    if rule == "https_url":
        return "https_url"
    if rule == "array":
        return "array"
    if rule == "absent":
        return "absent"
    if rule == "string":
        return "string"
    if rule == "number":
        return "number"
    if rule == "boolean":
        return "boolean"
    if rule == "object":
        return "object"
    if rule == "non_empty_array":
        return "non_empty_array"
    if rule == "min_items":
        return "min_items"
    if rule == "equals":
        return "equals"
    if rule == "one_of":
        return "one_of"
    if rule == "all_items_have_field":
        return "all_items_have_field"
    if rule == "all_items_absent_field":
        return "all_items_absent_field"
    raise ManifestError(
        f"{location}.rule must be one of: required, https_url, array, absent, string, number, boolean, object, "
        "non_empty_array, min_items, equals, one_of, all_items_have_field, all_items_absent_field"
    )


def _required_header_rule(raw_assertion: dict[str, JsonValue], *, location: str) -> HeaderRule:
    """Extract and validate the header assertion rule.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        A validated header rule literal.

    Raises:
        ManifestError: If the header rule is missing or unsupported.
    """
    rule = _required_string(raw_assertion, "rule", location=location)
    if rule == "present":
        return "present"
    if rule == "absent":
        return "absent"
    if rule == "equals":
        return "equals"
    if rule == "contains":
        return "contains"
    if rule == "matches_request_header":
        return "matches_request_header"
    raise ManifestError(f"{location}.rule must be one of: present, absent, equals, contains, matches_request_header")


def _required_header_rule_value(
    raw_assertion: dict[str, JsonValue], *, rule: Literal["equals", "contains"], location: str
) -> str:
    """Extract the required string value for a header comparison rule.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        rule: Header rule requiring a comparison value.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped non-empty comparison value.

    Raises:
        ManifestError: If the value is missing or not a non-empty string.
    """
    value = raw_assertion.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.value must be a non-empty string for header rule {rule}")
    return value.strip()


def _required_json_compatible_value(
    raw_assertion: dict[str, JsonValue],
    *,
    key: str,
    location: str,
    missing_message: str,
) -> JsonValue:
    """Extract a required JSON-compatible value from an assertion object.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        key: Key expected to contain the JSON-compatible value.
        location: Dot-path location string used in error messages.
        missing_message: Error emitted when the key is absent.

    Returns:
        A defensive deep copy of the JSON-compatible value.

    Raises:
        ManifestError: If the key is absent or the value is not JSON-compatible.
    """
    if key not in raw_assertion:
        raise ManifestError(missing_message)
    value = raw_assertion[key]
    if not _is_json_compatible_value(value):
        raise ManifestError(f"{location}.{key} must be valid JSON-compatible data")
    return copy.deepcopy(value)


def _required_json_compatible_values(raw_assertion: dict[str, JsonValue], *, location: str) -> tuple[JsonValue, ...]:
    """Extract a required non-empty array of JSON-compatible values.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        Tuple of defensive deep copies of the JSON-compatible values.

    Raises:
        ManifestError: If the values key is missing, empty, or contains
            non-JSON-compatible members.
    """
    values = raw_assertion.get("values")
    if not isinstance(values, list) or not values:
        raise ManifestError(f"{location}.values must be a non-empty array")
    parsed_values: list[JsonValue] = []
    for index, value in enumerate(values):
        if not _is_json_compatible_value(value):
            raise ManifestError(f"{location}.values[{index}] must be valid JSON-compatible data")
        parsed_values.append(copy.deepcopy(value))
    return tuple(parsed_values)


def _required_min_items(raw_assertion: dict[str, JsonValue], *, location: str) -> int:
    """Extract and validate the ``minItems`` threshold for a JSON array rule.

    Args:
        raw_assertion: Raw assertion dict from the manifest JSON.
        location: Dot-path location string used in error messages.

    Returns:
        Minimum required array length.

    Raises:
        ManifestError: If the value is missing or not an integer >= 1.
    """
    value = raw_assertion.get("minItems")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ManifestError(f"{location}.minItems must be an integer greater than or equal to 1")
    return value


def _is_json_compatible_value(value: object) -> bool:
    """Return whether an arbitrary Python value can be represented as JSON.

    Args:
        value: Candidate value to validate.

    Returns:
        ``True`` when the value is composed only of JSON-compatible scalars,
        arrays, and objects with string keys.
    """
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_compatible_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_compatible_value(item) for key, item in value.items())
    return False


def _required_status_code(raw_assertion: dict[str, JsonValue], *, location: str) -> int:
    """Extract and validate an HTTP status code (100–599).

    Args:
        raw_assertion: Raw assertion dict expected to contain an ``expected`` field.
        location: Dot-path location string used in error messages.

    Returns:
        An integer HTTP status code in the 100–599 range.

    Raises:
        ManifestError: If the value is not an HTTP status code.
    """
    value = raw_assertion.get("expected")
    if not isinstance(value, int) or isinstance(value, bool) or value < 100 or value > 599:
        raise ManifestError(f"{location}.expected must be an HTTP status code")
    return value


def _required_string(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string from a JSON object.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped, non-empty string value.

    Raises:
        ManifestError: If the value is missing or not a non-empty string.
    """
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _parse_optional_warning(raw_step: dict[str, JsonValue], *, location: str) -> str | None:
    """Extract an optional ``warning`` message from a v1 step.

    The ``warning`` field is optional. When absent, ``None`` is returned. When
    present it must be a non-empty string (after stripping). An empty or
    whitespace-only string is rejected to fail fast on misauthored manifests
    rather than silently emit an empty warning at runtime.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped warning message, or ``None`` if no ``warning`` key was set.

    Raises:
        ManifestError: If ``warning`` is present but is not a non-empty string.
    """
    if "warning" not in raw_step:
        return None
    value = raw_step["warning"]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{location}.warning must be a non-empty string when present")
    return value.strip()


def _parse_optional_mandatory(raw_step: dict[str, JsonValue], *, location: str) -> bool:
    """Extract the optional ``mandatory`` flag from a v1 step.

    The ``mandatory`` field is optional. When absent, ``False`` is returned
    (steps are opt-in to mandatory coverage). When present it must be a JSON
    boolean. Truthy/falsy coercion is intentionally rejected so that integer,
    string, or ``null`` values fail fast at parse time rather than silently
    flip certification eligibility on a misauthored manifest.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        ``True`` if the step is mandatory for certification, ``False`` otherwise.

    Raises:
        ManifestError: If ``mandatory`` is present but is not a JSON boolean.
    """
    if "mandatory" not in raw_step:
        return False
    value = raw_step["mandatory"]
    # ``isinstance(value, bool)`` is required: in Python ``bool`` is a
    # subclass of ``int``, so a bare ``isinstance(value, int)`` would also
    # admit integers. We want to reject ``1``/``0`` and other truthy values.
    if not isinstance(value, bool):
        raise ManifestError(f"{location}.mandatory must be a JSON boolean when present")
    return value


def _parse_optional_optional(raw_step: dict[str, JsonValue], *, location: str) -> bool:
    """Extract the optional ``optional`` flag from a v1 step.

    The ``optional`` field is opt-in for the default test plan. When absent
    ``False`` is returned (the step is part of default coverage and selected
    in the default plan). When present it must be a JSON boolean — truthy
    integers and other coercible values are rejected so that a misauthored
    manifest cannot silently change which steps default-run.

    Mutual exclusion with ``mandatory`` is enforced by the caller, not here,
    so the parse-time error message can name both fields together.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        ``True`` if the step is opt-in (deselected in the default plan),
        ``False`` otherwise.

    Raises:
        ManifestError: If ``optional`` is present but is not a JSON boolean.
    """
    if "optional" not in raw_step:
        return False
    value = raw_step["optional"]
    if not isinstance(value, bool):
        raise ManifestError(f"{location}.optional must be a JSON boolean when present")
    return value


def _parse_optional_group(raw_step: dict[str, JsonValue], *, location: str) -> str:
    """Extract and validate the optional execution ``group`` for a v1 step.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        The validated group id. Defaults to ``"default"`` when absent.

    Raises:
        ManifestError: If ``group`` is present but not a valid step-id shaped
            identifier.
    """
    if "group" not in raw_step:
        return _DEFAULT_STEP_GROUP
    group = _required_string(raw_step, "group", location=location)
    _validate_group_id(group, location=location)
    return group


def _parse_optional_phase(raw_step: dict[str, JsonValue], *, location: str) -> StepPhase:
    """Extract and validate the optional scheduling ``phase`` for a v1 step.

    Args:
        raw_step: Raw JSON object for the step.
        location: Dot-path location string used in error messages.

    Returns:
        ``"setup"`` or ``"execution"``. Defaults to ``"execution"`` when
        absent.

    Raises:
        ManifestError: If ``phase`` is present but not one of the supported
            literal values.
    """
    if "phase" not in raw_step:
        return _DEFAULT_STEP_PHASE
    phase = _required_string(raw_step, "phase", location=location)
    if phase == "setup":
        return "setup"
    if phase == "execution":
        return "execution"
    raise ManifestError(f"{location}.phase must be one of: setup, execution (got: {phase!r})")


def _required_https_url(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract and validate a hardened HTTPS URL from a JSON object.

    Rejects non-HTTPS schemes, embedded credentials, control characters,
    IP-literal hostnames, and malformed DNS hostnames. Used for all
    manifest URLs that will be fetched over the network.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The validated HTTPS URL string.

    Raises:
        ManifestError: If the value is not a safe, well-formed HTTPS URL.
    """
    value = _required_string(raw_config, key, location=location)
    try:
        validate_https_url(value, label=f"{location}.{key}")
    except HttpsUrlValidationError as error:
        raise ManifestError(str(error)) from error
    return value


def _required_object(raw_config: dict[str, JsonValue], key: str, *, location: str) -> dict[str, JsonValue]:
    """Extract a required JSON object from a parent object.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The nested JSON object (dict).

    Raises:
        ManifestError: If the value is missing or not a JSON object.
    """
    value = raw_config.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{location}.{key} must be a JSON object")
    return value


def _required_object_array(raw_config: dict[str, JsonValue], key: str, *, location: str) -> list[dict[str, JsonValue]]:
    """Extract a required non-empty array of JSON objects.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        A non-empty list of validated JSON object dicts.

    Raises:
        ManifestError: If the value is missing, empty, or contains non-objects.
    """
    value = raw_config.get(key)
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{location}.{key} must be a non-empty array")
    objects: list[dict[str, JsonValue]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ManifestError(f"{location}.{key}[{index}] must be a JSON object")
        objects.append(item)
    return objects


_STEP_ID_PATTERN = re.compile(r"^" + _STEP_ID_CHAR_CLASS + r"$")
"""Compiled pattern for validating step/test IDs at parse time."""


def _validate_step_id(step_id: str, *, location: str) -> None:
    """Validate that a step or test ID uses only allowed characters.

    IDs must start with an alphanumeric character and may contain only
    alphanumerics, hyphens, and underscores. Dots are forbidden because
    the placeholder resolver splits on dots.

    Args:
        step_id: The candidate ID string to validate.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If the ID contains invalid characters.
    """
    if not _STEP_ID_PATTERN.match(step_id):
        raise ManifestError(
            f"{location}.id '{step_id}' contains invalid characters (must match [A-Za-z0-9][A-Za-z0-9_-]*)"
        )


def _validate_group_id(group: str, *, location: str) -> None:
    """Validate that an execution group id uses the step-id character set.

    Args:
        group: Candidate execution group identifier.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If the group id contains invalid characters.
    """
    if not _STEP_ID_PATTERN.match(group):
        raise ManifestError(
            f"{location}.group '{group}' contains invalid characters (must match [A-Za-z0-9][A-Za-z0-9_-]*)"
        )


def _reject_unknown_keys(raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Raise if the JSON object contains keys outside the allowed set.

    Args:
        raw_config: The JSON object to validate.
        allowed_keys: Set of permitted key names.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If any keys are outside the allowed set.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ManifestError(f"Unknown {location} field(s): {joined_keys}")


def _reject_unknown_keys_with_singular_message(
    raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str
) -> None:
    """Raise on unknown keys using singular wording when exactly one key is present.

    Args:
        raw_config: The JSON object to validate.
        allowed_keys: Set of permitted key names.
        location: Dot-path location string used in error messages.

    Raises:
        ManifestError: If any keys are outside the allowed set.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if not unknown_keys:
        return
    if len(unknown_keys) == 1:
        raise ManifestError(f"Unknown {location} field: {unknown_keys[0]}")
    joined_keys = ", ".join(unknown_keys)
    raise ManifestError(f"Unknown {location} field(s): {joined_keys}")
