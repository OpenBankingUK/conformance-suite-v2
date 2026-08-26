"""Unit tests for shared JSON-first test-plan validation."""

from __future__ import annotations

from pathlib import Path

import pytest

import conformance.test_plan_validation as validation_module
from conformance.catalogue import PlanDocumentV2
from conformance.test_plan_validation import (
    TestPlanValidationError as PlanValidationError,
)
from conformance.test_plan_validation import (
    prepare_test_plan_for_run,
    validate_test_plan_for_load,
    validate_test_plan_for_run,
)


def _canonical_plan() -> dict[str, object]:
    """Build a minimal canonical Open Banking test plan.

    Returns:
        JSON-first test plan using the PRD schemaVersion 1.0 shape.
    """
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        },
        "resourceGroups": [
            {
                "id": "AIS",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/open-banking/v4.0/aisp/accounts",
                    }
                ],
            }
        ],
        "businessTestData": {},
        "metadata": {"aspspName": "Example Bank"},
    }


@pytest.mark.unit
def test_prepare_test_plan_for_run_returns_validation_and_safe_snapshot(tmp_path: Path) -> None:
    """Preparation compiles a canonical plan and snapshots it without secrets."""
    prepared = prepare_test_plan_for_run(_canonical_plan(), base_dir=tmp_path)

    assert prepared.validation.valid is True
    assert prepared.validation.execution_mode == "development"
    assert prepared.validation.issues[0].severity == "warning"
    assert "ais-at-accounts-list-200" in prepared.compiled_plan.traceability.generated_test_case_ids
    assert dict(prepared.runtime_inputs) == {
        "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
        "resourceBaseUrl": "https://resource.example.com",
    }
    assert prepared.snapshot["schemaVersion"] == "1.0"
    business_data = prepared.snapshot["businessTestData"]
    assert isinstance(business_data, dict)
    assert "inputs" not in business_data


@pytest.mark.unit
def test_validate_test_plan_for_load_reports_schema_errors() -> None:
    """Import validation reports missing canonical sections as schema errors."""
    raw_plan = _canonical_plan()
    raw_plan.pop("metadata")

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert validation.execution_mode == "development"
    assert validation.issues[0].layer == "schema"
    assert "metadata" in validation.issues[0].message


@pytest.mark.unit
def test_non_canonical_validation_preserves_raw_development_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-canonical rejection results preserve explicit raw execution mode."""
    parsed_document = PlanDocumentV2(
        schema_version="v2",
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.1",
        security_profile="FAPI1_ADVANCED",
        resource_groups=(),
        config={},
        runtime_inputs={},
    )
    raw_plan = {"schemaVersion": "v2", "executionMode": "development"}
    monkeypatch.setattr(validation_module, "parse_test_plan_document", lambda _: parsed_document)

    load_validation = validate_test_plan_for_load(raw_plan)
    run_validation = validate_test_plan_for_run(raw_plan, base_dir=tmp_path)

    assert load_validation.valid is False
    assert load_validation.execution_mode == "development"
    assert run_validation.valid is False
    assert run_validation.execution_mode == "development"


@pytest.mark.unit
def test_validate_test_plan_for_load_allows_missing_discovery_url() -> None:
    """Manual security configuration does not require discovery metadata."""
    raw_plan = _canonical_plan()
    security_environment = raw_plan["securityEnvironment"]
    assert isinstance(security_environment, dict)
    security_environment.pop("discoveryUrl")

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is True


@pytest.mark.unit
def test_validate_test_plan_for_run_requires_discovery_for_response_signatures(tmp_path: Path) -> None:
    """Selected response-signature cases require discovery for JWKS resolution."""
    raw_plan: dict[str, object] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {"resourceBaseUrl": "https://resource.example.com"},
        "resourceGroups": [
            {
                "id": "PIS",
                "endpoints": [
                    {
                        "method": "POST",
                        "path": "/open-banking/v4.0/pisp/domestic-payment-consents",
                    }
                ],
            }
        ],
        "businessTestData": {},
        "metadata": {},
    }

    validation = validate_test_plan_for_run(raw_plan, base_dir=tmp_path)

    assert validation.valid is False
    assert any(
        "discoveryUrl is required because the selected run validates response signatures" in issue.message
        for issue in validation.issues
    )


@pytest.mark.unit
def test_validate_test_plan_for_run_rejects_non_plan_runtime_inputs(tmp_path: Path) -> None:
    """Token, fixture, generated, and captured data cannot be supplied as plan config."""
    raw_plan = _canonical_plan()
    raw_plan["businessTestData"] = {
        "inputs": {
            "accessToken": {"value": "secret-access-token"},
        }
    }

    validation = validate_test_plan_for_run(raw_plan, base_dir=tmp_path)

    assert validation.valid is False
    assert any("accessToken" in issue.message and "token-sourced" in issue.message for issue in validation.issues)


@pytest.mark.unit
def test_validate_test_plan_for_load_rejects_security_timeout() -> None:
    """JSON-first test plans do not expose participant-configurable HTTP timeouts."""
    raw_plan = _canonical_plan()
    security_environment = raw_plan["securityEnvironment"]
    assert isinstance(security_environment, dict)
    security_environment["timeoutSeconds"] = 60

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert any(
        "securityEnvironment" in issue.message and "timeoutSeconds" in issue.message for issue in validation.issues
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("openBankingIntentId", "consent-789"),
        ("acrValuesSupported", ["urn:openbanking:psd2:sca"]),
        ("jwksUri", "https://auth.example.com/jwks"),
        ("tppSignatureIssuer", "synthetic-org-id"),
        ("tppSignatureTan", "openbanking.org.uk"),
        ("xFapiFinancialId", "financial-id"),
        ("sendXFapiCustomerIpAddress", True),
        ("xFapiCustomerIpAddress", "203.0.113.10"),
    ],
)
def test_validate_test_plan_for_load_rejects_removed_security_fields(
    field_name: str,
    value: object,
) -> None:
    """JSON-first test plans reject removed security config fields."""
    raw_plan = _canonical_plan()
    security_environment = raw_plan["securityEnvironment"]
    assert isinstance(security_environment, dict)
    security_environment[field_name] = value

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert any("securityEnvironment" in issue.message and field_name in issue.message for issue in validation.issues)


@pytest.mark.unit
def test_validate_test_plan_for_load_rejects_mtls_certificate_path_root() -> None:
    """JSON-first mTLS config uses direct path fields rather than a shared root."""
    raw_plan = _canonical_plan()
    security_environment = raw_plan["securityEnvironment"]
    assert isinstance(security_environment, dict)
    security_environment["mtls"] = {"certificatePathRoot": "/absolute/path/certs"}

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert any("certificatePathRoot" in issue.message for issue in validation.issues)


@pytest.mark.unit
def test_validate_test_plan_for_load_sorts_schema_errors_stably() -> None:
    """Schema validation returns deterministic issues for unrelated bad paths."""
    raw_plan = _canonical_plan()
    raw_plan["resourceGroups"] = [123]
    raw_plan["metadata"] = []

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert [issue.layer for issue in validation.issues] == ["schema", "schema"]


@pytest.mark.unit
def test_prepare_test_plan_for_run_rejects_legacy_v2_documents(tmp_path: Path) -> None:
    """Run preparation accepts canonical test plans only, not legacy v2 documents."""
    raw_plan = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {"resourceGroups": []},
        "config": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
    }

    with pytest.raises(PlanValidationError) as exc_info:
        prepare_test_plan_for_run(raw_plan, base_dir=tmp_path)

    assert exc_info.value.result.schema_version == "v2"
    assert "schemaVersion 1.0" in exc_info.value.result.summary_message()


@pytest.mark.unit
def test_parse_error_validation_preserves_development_mode(tmp_path: Path) -> None:
    """Parser errors preserve raw schema version and execution mode evidence."""
    raw_plan = _canonical_plan()
    raw_plan["executionMode"] = "development"
    raw_plan["specification"] = {"family": "OBL_READ_WRITE", "version": "9.9.9"}

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert validation.schema_version == "1.0"
    assert validation.execution_mode == "development"

    with pytest.raises(PlanValidationError) as exc_info:
        prepare_test_plan_for_run(raw_plan, base_dir=tmp_path)
    assert exc_info.value.result.schema_version == "1.0"
    assert exc_info.value.result.execution_mode == "development"
