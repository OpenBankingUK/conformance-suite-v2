"""Unit tests for shared JSON-first test-plan validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.test_plan_validation import (
    TestPlanValidationError as PlanValidationError,
)
from conformance.test_plan_validation import (
    prepare_test_plan_for_run,
    validate_test_plan_for_load,
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
        "businessTestData": {"inputs": {"accessToken": {"value": "secret-access-token"}}},
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
    assert prepared.runtime_inputs["accessToken"] == "secret-access-token"
    assert prepared.snapshot["schemaVersion"] == "1.0"
    business_data = prepared.snapshot["businessTestData"]
    assert isinstance(business_data, dict)
    inputs = business_data["inputs"]
    assert isinstance(inputs, dict)
    access_token = inputs["accessToken"]
    assert isinstance(access_token, dict)
    assert access_token["value"] == ""
    assert "secret-access-token" not in json.dumps(prepared.snapshot)


@pytest.mark.unit
def test_validate_test_plan_for_load_reports_schema_errors() -> None:
    """Import validation reports missing canonical sections as schema errors."""
    raw_plan = _canonical_plan()
    raw_plan.pop("metadata")

    validation = validate_test_plan_for_load(raw_plan)

    assert validation.valid is False
    assert validation.issues[0].layer == "schema"
    assert "metadata" in validation.issues[0].message


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
