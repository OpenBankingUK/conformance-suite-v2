"""Unit tests for conformance.plugins.dcr.schema_validation module."""

from __future__ import annotations

import pytest

from conformance.json_types import JsonObject
from conformance.plugins.dcr.schema_validation import (
    REQUIRED_FIELDS_BY_VERSION,
    SUPPORTED_VERSIONS,
    DcrSchemaValidationError,
    validate_dcr_registration_response,
)


def _body_with_fields(*fields: str) -> JsonObject:
    """Build a response body dict containing the specified fields."""
    return {field: f"value-for-{field}" for field in fields}


@pytest.mark.unit
class TestRequiredFieldsByVersion:
    """Verify required field sets are consistent across versions."""

    def test_32_required_fields(self) -> None:
        """DCR 3.2 requires exactly client_id, redirect_uris, and auth method."""
        assert REQUIRED_FIELDS_BY_VERSION["3.2"] == frozenset(
            {"client_id", "redirect_uris", "token_endpoint_auth_method"}
        )

    def test_33_adds_grant_and_response_types(self) -> None:
        """DCR 3.3 adds grant_types and response_types to 3.2 requirements."""
        assert "grant_types" in REQUIRED_FIELDS_BY_VERSION["3.3"]
        assert "response_types" in REQUIRED_FIELDS_BY_VERSION["3.3"]

    def test_34_adds_software_statement(self) -> None:
        """DCR 3.4 adds software_statement echo to 3.3 requirements."""
        assert "software_statement" in REQUIRED_FIELDS_BY_VERSION["3.4"]

    def test_versions_are_cumulative(self) -> None:
        """Each version's required set is a superset of the previous version."""
        v32 = REQUIRED_FIELDS_BY_VERSION["3.2"]
        v33 = REQUIRED_FIELDS_BY_VERSION["3.3"]
        v34 = REQUIRED_FIELDS_BY_VERSION["3.4"]
        assert v32.issubset(v33)
        assert v33.issubset(v34)

    def test_all_supported_versions_present(self) -> None:
        """SUPPORTED_VERSIONS matches the keys in REQUIRED_FIELDS_BY_VERSION."""
        assert set(SUPPORTED_VERSIONS) == set(REQUIRED_FIELDS_BY_VERSION.keys())


@pytest.mark.unit
class TestValidateDcrRegistrationResponse:
    """Verify validation passes and fails appropriately for each version."""

    def test_32_passes_with_required_fields(self) -> None:
        """DCR 3.2 validation passes when all required fields are present."""
        body = _body_with_fields("client_id", "redirect_uris", "token_endpoint_auth_method")
        validate_dcr_registration_response(body, "3.2")

    def test_32_fails_when_client_id_missing(self) -> None:
        """DcrSchemaValidationError raised when client_id missing for 3.2."""
        body = _body_with_fields("redirect_uris", "token_endpoint_auth_method")
        with pytest.raises(DcrSchemaValidationError) as exc_info:
            validate_dcr_registration_response(body, "3.2")
        assert "client_id" in exc_info.value.missing_fields
        assert exc_info.value.version == "3.2"

    def test_33_passes_with_all_required_fields(self) -> None:
        """DCR 3.3 validation passes when all v3.3 required fields are present."""
        body = _body_with_fields(
            "client_id",
            "redirect_uris",
            "token_endpoint_auth_method",
            "grant_types",
            "response_types",
        )
        validate_dcr_registration_response(body, "3.3")

    def test_33_fails_when_grant_types_missing(self) -> None:
        """DcrSchemaValidationError raised when grant_types missing for 3.3."""
        body = _body_with_fields("client_id", "redirect_uris", "token_endpoint_auth_method", "response_types")
        with pytest.raises(DcrSchemaValidationError) as exc_info:
            validate_dcr_registration_response(body, "3.3")
        assert "grant_types" in exc_info.value.missing_fields

    def test_33_fails_when_response_types_missing(self) -> None:
        """DcrSchemaValidationError raised when response_types missing for 3.3."""
        body = _body_with_fields("client_id", "redirect_uris", "token_endpoint_auth_method", "grant_types")
        with pytest.raises(DcrSchemaValidationError) as exc_info:
            validate_dcr_registration_response(body, "3.3")
        assert "response_types" in exc_info.value.missing_fields

    def test_34_passes_with_all_required_fields(self) -> None:
        """DCR 3.4 validation passes when all v3.4 required fields are present."""
        body = _body_with_fields(
            "client_id",
            "redirect_uris",
            "token_endpoint_auth_method",
            "grant_types",
            "response_types",
            "software_statement",
        )
        validate_dcr_registration_response(body, "3.4")

    def test_34_fails_when_software_statement_missing(self) -> None:
        """DcrSchemaValidationError raised when software_statement missing for 3.4."""
        body = _body_with_fields(
            "client_id",
            "redirect_uris",
            "token_endpoint_auth_method",
            "grant_types",
            "response_types",
        )
        with pytest.raises(DcrSchemaValidationError) as exc_info:
            validate_dcr_registration_response(body, "3.4")
        assert "software_statement" in exc_info.value.missing_fields

    def test_error_message_contains_version(self) -> None:
        """DcrSchemaValidationError message includes the version string."""
        body = _body_with_fields("redirect_uris", "token_endpoint_auth_method")
        with pytest.raises(DcrSchemaValidationError, match="3.3"):
            validate_dcr_registration_response(body, "3.3")

    def test_error_lists_all_missing_fields(self) -> None:
        """missing_fields contains all absent required fields, not just the first."""
        body: JsonObject = {}
        with pytest.raises(DcrSchemaValidationError) as exc_info:
            validate_dcr_registration_response(body, "3.2")
        assert len(exc_info.value.missing_fields) == 3  # noqa: PLR2004

    def test_raises_for_unsupported_version(self) -> None:
        """ValueError is raised for an unsupported version string."""
        with pytest.raises(ValueError, match="Unsupported DCR specification version"):
            validate_dcr_registration_response({}, "2.0")

    def test_body_with_extra_fields_passes(self) -> None:
        """Extra fields in the response body do not cause validation failure."""
        body = _body_with_fields(
            "client_id",
            "redirect_uris",
            "token_endpoint_auth_method",
            "some_extra_field",
            "another_extra_field",
        )
        validate_dcr_registration_response(body, "3.2")


@pytest.mark.unit
class TestDcrSchemaValidationError:
    """Verify DcrSchemaValidationError attributes."""

    def test_stores_missing_fields_list(self) -> None:
        """missing_fields attribute stores the sorted list."""
        exc = DcrSchemaValidationError(missing_fields=["z_field", "a_field"], version="3.3")
        assert exc.missing_fields == ["z_field", "a_field"]

    def test_stores_version(self) -> None:
        """version attribute stores the version string."""
        exc = DcrSchemaValidationError(missing_fields=["client_id"], version="3.4")
        assert exc.version == "3.4"
