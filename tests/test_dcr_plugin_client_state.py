"""Unit tests for conformance.plugins.dcr.client_state module."""

from __future__ import annotations

from typing import cast

import pytest

from conformance.json_types import JsonObject
from conformance.masking import MASKED_VALUE
from conformance.plugins.dcr.client_state import (
    DcrScenarioResult,
    build_step_evidence,
    evidence_from_http_response,
    failed_result,
    parse_client_state,
    parse_token_response,
    passed_result,
    skipped_result,
)


def _minimal_registration_body(**overrides: object) -> JsonObject:
    """Build a minimal valid registration response body."""
    base: JsonObject = {
        "client_id": "test-client-001",
        "redirect_uris": ["https://tpp.example.com/callback"],
        "token_endpoint_auth_method": "tls_client_auth",
    }
    extra: JsonObject = cast(JsonObject, overrides)
    return {**base, **extra}


@pytest.mark.unit
class TestParseClientState:
    """Verify DcrClientState parsing from registration responses."""

    def test_parses_minimal_response(self) -> None:
        """Minimal response with client_id and auth method is parsed correctly."""
        state = parse_client_state(_minimal_registration_body())
        assert state.client_id == "test-client-001"
        assert state.token_endpoint_auth_method == "tls_client_auth"  # noqa: S105
        assert state.client_secret_present is False
        assert state.registration_access_token_present is False
        assert state.registration_client_uri is None
        assert state.granted_scopes is None

    def test_records_client_secret_presence(self) -> None:
        """client_secret_present is True when client_secret is in response."""
        state = parse_client_state(_minimal_registration_body(client_secret="super-secret"))  # noqa: S106
        assert state.client_secret_present is True

    def test_records_rat_presence(self) -> None:
        """registration_access_token_present is True when RAT is in response."""
        state = parse_client_state(_minimal_registration_body(registration_access_token="rat-value"))  # noqa: S106
        assert state.registration_access_token_present is True

    def test_raw_response_masks_client_secret(self) -> None:
        """raw_response_masked replaces client_secret with MASKED_VALUE."""
        state = parse_client_state(_minimal_registration_body(client_secret="my-secret"))  # noqa: S106
        assert state.raw_response_masked.get("client_secret") == MASKED_VALUE

    def test_raw_response_masks_registration_access_token(self) -> None:
        """raw_response_masked replaces registration_access_token with MASKED_VALUE."""
        state = parse_client_state(_minimal_registration_body(registration_access_token="rat-token"))  # noqa: S106
        assert state.raw_response_masked.get("registration_access_token") == MASKED_VALUE

    def test_client_secret_accessible_at_runtime(self) -> None:
        """client_secret() returns the raw value for runtime use."""
        state = parse_client_state(_minimal_registration_body(client_secret="runtime-secret"))  # noqa: S106
        assert state.client_secret() == "runtime-secret"

    def test_rat_accessible_at_runtime(self) -> None:
        """registration_access_token() returns the raw value for runtime use."""
        state = parse_client_state(_minimal_registration_body(registration_access_token="runtime-rat"))  # noqa: S106
        assert state.registration_access_token() == "runtime-rat"

    def test_raises_when_client_id_missing(self) -> None:
        """ValueError is raised when client_id is absent."""
        body = _minimal_registration_body()
        del body["client_id"]
        with pytest.raises(ValueError, match="client_id"):
            parse_client_state(body)

    def test_raises_when_auth_method_missing(self) -> None:
        """ValueError is raised when token_endpoint_auth_method is absent."""
        body = _minimal_registration_body()
        del body["token_endpoint_auth_method"]
        with pytest.raises(ValueError, match="token_endpoint_auth_method"):
            parse_client_state(body)

    def test_parses_registration_client_uri(self) -> None:
        """registration_client_uri is extracted when present."""
        state = parse_client_state(
            _minimal_registration_body(registration_client_uri="https://as.example.com/register/test-client-001")
        )
        assert state.registration_client_uri == "https://as.example.com/register/test-client-001"

    def test_parses_scope(self) -> None:
        """granted_scopes is extracted when present."""
        state = parse_client_state(_minimal_registration_body(scope="openid accounts"))
        assert state.granted_scopes == "openid accounts"

    def test_non_string_fields_ignored_gracefully(self) -> None:
        """Non-string client_secret is treated as absent."""
        state = parse_client_state(_minimal_registration_body(client_secret=42))
        assert state.client_secret_present is False


@pytest.mark.unit
class TestParseTokenResponse:
    """Verify DcrTokenResponse parsing from token endpoint responses."""

    def test_parses_minimal_token_response(self) -> None:
        """Minimal token response with token_type is parsed correctly."""
        body: JsonObject = {"access_token": "live-token", "token_type": "Bearer", "expires_in": 3600}
        result = parse_token_response(body)
        assert result.access_token_masked == MASKED_VALUE
        assert result.token_type == "Bearer"  # noqa: S105
        assert result.expires_in == 3600

    def test_access_token_always_masked(self) -> None:
        """access_token_masked is always MASKED_VALUE regardless of input."""
        body: JsonObject = {"access_token": "very-sensitive-token", "token_type": "Bearer"}
        result = parse_token_response(body)
        assert result.access_token_masked == MASKED_VALUE

    def test_raises_when_token_type_missing(self) -> None:
        """ValueError is raised when token_type is absent."""
        with pytest.raises(ValueError, match="token_type"):
            parse_token_response({"access_token": "token"})  # missing token_type — intentionally invalid

    def test_optional_expires_in_defaults_to_none(self) -> None:
        """expires_in defaults to None when absent."""
        body: JsonObject = {"access_token": "tok", "token_type": "Bearer"}
        result = parse_token_response(body)
        assert result.expires_in is None

    def test_optional_scope_extracted(self) -> None:
        """scope is extracted when present."""
        body: JsonObject = {"access_token": "tok", "token_type": "Bearer", "scope": "openid"}
        result = parse_token_response(body)
        assert result.scope == "openid"


@pytest.mark.unit
class TestBuildStepEvidence:
    """Verify DcrStepEvidence construction with masking."""

    def test_masks_authorization_header(self) -> None:
        """Authorization header value is replaced with MASKED_VALUE."""
        evidence = build_step_evidence(
            request_url="https://as.example.com/register",
            request_method="POST",
            request_content_type="application/jose",
            request_headers={"Authorization": "Bearer live-token", "Content-Type": "application/jose"},
            response_status=201,
            response_headers={},
            response_body={},
        )
        assert evidence.request_headers_masked["Authorization"] == MASKED_VALUE
        assert evidence.request_headers_masked["Content-Type"] == "application/jose"

    def test_masks_access_token_in_response_body(self) -> None:
        """access_token in response body is masked."""
        evidence = build_step_evidence(
            request_url="https://as.example.com/token",
            request_method="POST",
            request_content_type="application/x-www-form-urlencoded",
            request_headers={},
            response_status=200,
            response_headers={},
            response_body={"access_token": "live-token", "token_type": "Bearer"},
        )
        assert evidence.response_body_masked["access_token"] == MASKED_VALUE

    def test_preserves_non_sensitive_response_fields(self) -> None:
        """Non-sensitive fields in the response body are preserved."""
        evidence = build_step_evidence(
            request_url="https://as.example.com/register",
            request_method="POST",
            request_content_type="application/jose",
            request_headers={},
            response_status=201,
            response_headers={},
            response_body={"client_id": "abc123", "token_endpoint_auth_method": "tls_client_auth"},
        )
        assert evidence.response_body_masked["client_id"] == "abc123"
        assert evidence.response_body_masked["token_endpoint_auth_method"] == "tls_client_auth"  # noqa: S105

    def test_evidence_from_http_response_factory(self) -> None:
        """evidence_from_http_response builds evidence without request headers."""
        evidence = evidence_from_http_response(
            request_url="https://as.example.com/register",
            request_method="POST",
            request_content_type="application/jose",
            response_status=201,
            response_headers={"Content-Type": "application/json"},
            response_body={"client_id": "test-id"},
        )
        assert evidence.response_status == 201
        assert evidence.response_body_masked["client_id"] == "test-id"
        assert evidence.request_headers_masked == {}


@pytest.mark.unit
class TestScenarioResultFactories:
    """Verify scenario result factory functions."""

    def test_skipped_result_has_no_evidence(self) -> None:
        """skipped_result produces a result with no evidence."""
        result = skipped_result("DCR-002", "GET not advertised")
        assert isinstance(result, DcrScenarioResult)
        assert result.scenario_id == "DCR-002"
        assert result.outcome == "skipped"
        assert result.evidence is None
        assert "GET not advertised" in result.assertion_detail

    def test_passed_result_carries_evidence(self) -> None:
        """passed_result preserves the supplied evidence."""
        evidence = evidence_from_http_response(
            request_url="https://as.example.com/register",
            request_method="POST",
            request_content_type="application/jose",
            response_status=201,
            response_headers={},
            response_body={},
        )
        result = passed_result("DCR-001", detail="Passed!", evidence=evidence)
        assert result.outcome == "passed"
        assert result.evidence is evidence

    def test_failed_result_allows_no_evidence(self) -> None:
        """failed_result accepts None evidence for pre-HTTP failures."""
        result = failed_result("DCR-001", detail="Config error", evidence=None)
        assert result.outcome == "failed"
        assert result.evidence is None
