"""Parsed DCR registration response, runtime client state, and evidence types.

This module defines the data types that flow through the DCR runner:

- :class:`DcrClientState` — parsed, masked registration response used to drive
  subsequent GET, PUT, DELETE, and token-endpoint requests.
- :class:`DcrTokenResponse` — masked token-endpoint response for evidence.
- :class:`DcrStepEvidence` — masked HTTP request/response pair captured for
  each scenario step so failures can be diagnosed without re-running.
- :class:`DcrScenarioResult` — the top-level result for one DCR scenario,
  combining outcome, evidence, and assertion detail.

Sensitive values (``client_secret``, ``registration_access_token``,
``access_token``) are masked via :mod:`conformance.masking` before inclusion
in any evidence field.  Callers must never store or transmit unmasked values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conformance.json_types import JsonObject
from conformance.masking import MASKED_VALUE, mask_json_value

# ---------------------------------------------------------------------------
# Outcome literals
# ---------------------------------------------------------------------------

DcrOutcome = Literal["passed", "failed", "skipped"]
"""Scenario-level conformance outcome.

``"passed"``
    The ASPSP response satisfied all assertions for this scenario.

``"failed"``
    One or more assertions were violated.

``"skipped"``
    The scenario could not run because a prerequisite step failed or the
    ASPSP does not advertise the required operation.
"""

# ---------------------------------------------------------------------------
# Evidence types
# ---------------------------------------------------------------------------

_SENSITIVE_RESPONSE_KEYS: frozenset[str] = frozenset(
    {
        "client_secret",
        "registration_access_token",
        "access_token",
        "refresh_token",
        "id_token",
    }
)
"""Response body keys whose values must always be masked in evidence."""


@dataclass(frozen=True)
class DcrStepEvidence:
    """Masked HTTP request/response pair captured for one DCR scenario step.

    All sensitive header values and response body fields are masked before
    this object is constructed.  Callers must use the factory function
    :func:`build_step_evidence` rather than constructing instances directly.

    Attributes:
        request_url: Full request URL (no masking needed — URLs must not
            carry sensitive query parameters in DCR flows).
        request_method: Uppercase HTTP method string (e.g. ``"POST"``).
        request_content_type: Value of the ``Content-Type`` request header,
            or an empty string when absent.
        request_headers_masked: Sanitised request headers with sensitive
            values replaced by ``"***"``.
        response_status: HTTP status code returned by the ASPSP.
        response_headers_masked: Sanitised response headers.
        response_body_masked: Parsed and masked JSON response body, or an
            empty dict for no-content responses.
    """

    request_url: str
    request_method: str
    request_content_type: str
    request_headers_masked: dict[str, str]
    response_status: int
    response_headers_masked: dict[str, str]
    response_body_masked: JsonObject


def build_step_evidence(
    *,
    request_url: str,
    request_method: str,
    request_content_type: str,
    request_headers: dict[str, str],
    response_status: int,
    response_headers: dict[str, str],
    response_body: JsonObject,
) -> DcrStepEvidence:
    """Build a masked :class:`DcrStepEvidence` from raw HTTP values.

    Applies :func:`~conformance.masking.mask_headers` to both header maps
    and :func:`~conformance.masking.mask_json_value` to the response body
    before storing them.

    Args:
        request_url: Full request URL.
        request_method: Uppercase HTTP method string.
        request_content_type: Value of the ``Content-Type`` request header.
        request_headers: Raw request headers (may contain sensitive values).
        response_status: HTTP status code from the ASPSP.
        response_headers: Raw response headers.
        response_body: Parsed JSON response body (may contain sensitive values).

    Returns:
        A :class:`DcrStepEvidence` instance with all sensitive values masked.
    """
    from conformance.masking import mask_headers

    masked_response_body = _mask_registration_response(response_body)
    return DcrStepEvidence(
        request_url=request_url,
        request_method=request_method,
        request_content_type=request_content_type,
        request_headers_masked=mask_headers(request_headers),
        response_status=response_status,
        response_headers_masked=mask_headers(response_headers),
        response_body_masked=masked_response_body,
    )


def _mask_registration_response(body: JsonObject) -> JsonObject:
    """Mask sensitive fields in a DCR registration or token response body.

    Extends the standard :func:`~conformance.masking.mask_json_value` masking
    with DCR-specific sensitive fields (``registration_access_token``).

    Args:
        body: Parsed JSON response body dict.

    Returns:
        New dict with sensitive values replaced by :data:`~conformance.masking.MASKED_VALUE`.
    """
    masked: JsonObject = {}
    for key, value in body.items():
        if key.lower() in _SENSITIVE_RESPONSE_KEYS:
            masked[key] = MASKED_VALUE
        else:
            masked[key] = mask_json_value(value)
    return masked


# ---------------------------------------------------------------------------
# Token response
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DcrTokenResponse:
    """Masked token-endpoint response from a client credentials grant.

    The ``access_token`` field value is replaced with
    :data:`~conformance.masking.MASKED_VALUE` before this object is
    constructed to prevent accidental persistence.

    Attributes:
        access_token_masked: Masked access token (always ``"***"``).
        expires_in: Token lifetime in seconds, or ``None`` when absent.
        token_type: Token type string (usually ``"Bearer"``).
        scope: Granted scope string, or ``None`` when absent.
    """

    access_token_masked: str
    expires_in: int | None
    token_type: str
    scope: str | None = None


def parse_token_response(body: JsonObject) -> DcrTokenResponse:
    """Parse a token endpoint JSON response into a masked :class:`DcrTokenResponse`.

    Args:
        body: Parsed JSON body from the token endpoint.

    Returns:
        A :class:`DcrTokenResponse` with the access token masked.

    Raises:
        ValueError: If ``token_type`` is missing or not a string.
    """
    token_type_raw = body.get("token_type")
    if not isinstance(token_type_raw, str):
        raise ValueError("token_type must be a string in token response")

    expires_in: int | None = None
    expires_in_raw = body.get("expires_in")
    if isinstance(expires_in_raw, int):
        expires_in = expires_in_raw

    scope_raw = body.get("scope")
    scope: str | None = scope_raw if isinstance(scope_raw, str) else None

    return DcrTokenResponse(
        access_token_masked=MASKED_VALUE,
        expires_in=expires_in,
        token_type=token_type_raw,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Client state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DcrClientState:
    """Runtime client state parsed from a successful DCR registration response.

    Sensitive fields (``client_secret``, ``registration_access_token``) are
    masked with :data:`~conformance.masking.MASKED_VALUE`.  The masked values
    are stored only in :attr:`raw_response_masked`; the typed fields are
    ``None`` when absent rather than storing masked placeholders, so callers
    can branch on whether the ASPSP returned a value without pattern-matching
    on the mask sentinel.

    Attributes:
        client_id: Registered OAuth ``client_id``.
        client_secret_present: ``True`` when the ASPSP returned a
            ``client_secret`` (masked; value is not accessible here).
        registration_access_token_present: ``True`` when the ASPSP returned a
            ``registration_access_token`` (masked).
        registration_client_uri: URI to manage the registered client, or
            ``None`` when not returned.
        token_endpoint_auth_method: Token-endpoint authentication method
            granted by the ASPSP.
        granted_scopes: Scope string returned by the ASPSP, or ``None``.
        raw_response_masked: Full registration response with sensitive values
            masked via :func:`_mask_registration_response`.
    """

    client_id: str
    client_secret_present: bool
    registration_access_token_present: bool
    registration_client_uri: str | None
    token_endpoint_auth_method: str
    granted_scopes: str | None
    raw_response_masked: JsonObject

    # Runtime-only sensitive fields stored in memory (not serialised).
    # These are used by the token helpers and cleared after the run.
    _client_secret: str | None = None
    _registration_access_token: str | None = None

    def client_secret(self) -> str | None:
        """Return the raw client secret for runtime use only.

        This value must never be persisted or included in evidence.  Use
        :attr:`raw_response_masked` for evidence inclusion.

        Returns:
            The raw ``client_secret`` value, or ``None`` when absent.
        """
        return self._client_secret

    def registration_access_token(self) -> str | None:
        """Return the raw registration access token for runtime use only.

        This value must never be persisted or included in evidence.  Use
        :attr:`raw_response_masked` for evidence inclusion.

        Returns:
            The raw ``registration_access_token`` value, or ``None`` when absent.
        """
        return self._registration_access_token


def parse_client_state(body: JsonObject) -> DcrClientState:
    """Parse a DCR POST /register 201 response into a :class:`DcrClientState`.

    Extracts typed fields and constructs a masked response dict for evidence.
    Sensitive values are retained in private fields for runtime use but are
    never included in the returned masked response.

    Args:
        body: Parsed JSON body from a successful POST /register response.

    Returns:
        A :class:`DcrClientState` with typed fields and a masked evidence dict.

    Raises:
        ValueError: If ``client_id`` or ``token_endpoint_auth_method`` is
            missing or not a string.
    """
    client_id_raw = body.get("client_id")
    if not isinstance(client_id_raw, str) or not client_id_raw:
        raise ValueError("client_id must be a non-empty string in registration response")

    auth_method_raw = body.get("token_endpoint_auth_method")
    if not isinstance(auth_method_raw, str) or not auth_method_raw:
        raise ValueError("token_endpoint_auth_method must be a non-empty string in registration response")

    client_secret_raw = body.get("client_secret")
    client_secret: str | None = client_secret_raw if isinstance(client_secret_raw, str) else None

    rat_raw = body.get("registration_access_token")
    registration_access_token: str | None = rat_raw if isinstance(rat_raw, str) else None

    rcu_raw = body.get("registration_client_uri")
    registration_client_uri: str | None = rcu_raw if isinstance(rcu_raw, str) else None

    scope_raw = body.get("scope")
    granted_scopes: str | None = scope_raw if isinstance(scope_raw, str) else None

    raw_response_masked = _mask_registration_response(body)

    return DcrClientState(
        client_id=client_id_raw,
        client_secret_present=client_secret is not None,
        registration_access_token_present=registration_access_token is not None,
        registration_client_uri=registration_client_uri,
        token_endpoint_auth_method=auth_method_raw,
        granted_scopes=granted_scopes,
        raw_response_masked=raw_response_masked,
        _client_secret=client_secret,
        _registration_access_token=registration_access_token,
    )


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DcrScenarioResult:
    """Conformance result for one DCR scenario.

    Attributes:
        scenario_id: Stable scenario identifier (e.g. ``"DCR-001"``).
        outcome: Conformance outcome for this scenario.
        evidence: Captured HTTP request/response evidence, or ``None`` when
            the scenario was skipped before any HTTP call was made.
        assertion_detail: Human-readable description of what was checked and
            what the outcome was (pass/fail/skip reason).
    """

    scenario_id: str
    outcome: DcrOutcome
    evidence: DcrStepEvidence | None
    assertion_detail: str


def skipped_result(scenario_id: str, reason: str) -> DcrScenarioResult:
    """Build a skipped :class:`DcrScenarioResult` with no evidence.

    Args:
        scenario_id: The scenario that was skipped.
        reason: Human-readable reason for skipping.

    Returns:
        A :class:`DcrScenarioResult` with ``outcome="skipped"`` and no evidence.
    """
    return DcrScenarioResult(
        scenario_id=scenario_id,
        outcome="skipped",
        evidence=None,
        assertion_detail=reason,
    )


def passed_result(
    scenario_id: str,
    *,
    detail: str,
    evidence: DcrStepEvidence,
) -> DcrScenarioResult:
    """Build a passed :class:`DcrScenarioResult`.

    Args:
        scenario_id: The scenario that passed.
        detail: Human-readable description of what was validated.
        evidence: Masked HTTP evidence for the scenario step.

    Returns:
        A :class:`DcrScenarioResult` with ``outcome="passed"``.
    """
    return DcrScenarioResult(
        scenario_id=scenario_id,
        outcome="passed",
        evidence=evidence,
        assertion_detail=detail,
    )


def failed_result(
    scenario_id: str,
    *,
    detail: str,
    evidence: DcrStepEvidence | None,
) -> DcrScenarioResult:
    """Build a failed :class:`DcrScenarioResult`.

    Args:
        scenario_id: The scenario that failed.
        detail: Human-readable description of what assertion was violated.
        evidence: Masked HTTP evidence for the scenario step, or ``None``
            when the failure occurred before any HTTP call (e.g. config error).

    Returns:
        A :class:`DcrScenarioResult` with ``outcome="failed"``.
    """
    return DcrScenarioResult(
        scenario_id=scenario_id,
        outcome="failed",
        evidence=evidence,
        assertion_detail=detail,
    )


def evidence_from_http_response(
    *,
    request_url: str,
    request_method: str,
    request_content_type: str,
    response_status: int,
    response_headers: dict[str, str],
    response_body: JsonObject,
) -> DcrStepEvidence:
    """Build evidence from HTTP response components with masked headers.

    A convenience wrapper around :func:`build_step_evidence` that omits the
    request headers (which are not captured at the httpx call sites in the
    runner).  Only the response headers and body are masked.

    Args:
        request_url: Full request URL.
        request_method: Uppercase HTTP method.
        request_content_type: ``Content-Type`` header value for the request.
        response_status: HTTP status code from the ASPSP.
        response_headers: Raw response headers from httpx.
        response_body: Parsed JSON response body.

    Returns:
        A :class:`DcrStepEvidence` with masked response values.
    """
    return build_step_evidence(
        request_url=request_url,
        request_method=request_method,
        request_content_type=request_content_type,
        request_headers={},
        response_status=response_status,
        response_headers=response_headers,
        response_body=response_body,
    )
