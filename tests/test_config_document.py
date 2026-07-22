"""Unit tests for enhanced participant config document parsing."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from conformance.config_document import ConfigDocumentError, parse_participant_config_document
from conformance.json_types import JsonValue


def _test_plan_doc(
    *,
    specification: str = "read-write",
    specification_version: str = "v4.0.1",
    resource_groups: list[str] | None = None,
) -> dict[str, JsonValue]:
    """Return a valid testPlan JSON object for config-document tests.

    Args:
        specification: Target specification value to include in the plan.
        specification_version: Target specification version to include.
        resource_groups: Optional resource groups to include.

    Returns:
        JSON object representing a minimal participant test-plan document.
    """
    return {
        "target": {
            "standard": "obl",
            "specification": specification,
            "securityProfile": "fapi1-advanced",
            "specificationVersion": specification_version,
            "catalogueHash": "sha256:test",
        },
        "resourceGroups": cast(JsonValue, resource_groups or []),
    }


def _target_config(*, specification_version: str = "v4.0.1") -> dict[str, JsonValue]:
    """Return a participant config testTarget object.

    Args:
        specification_version: Target specification version to include.

    Returns:
        JSON object representing a valid ``testTarget`` section.
    """
    return {
        "standard": "obl",
        "specification": "read-write",
        "securityProfile": "fapi1-advanced",
        "specificationVersion": specification_version,
        "resourceGroups": ["ais"],
    }


@pytest.mark.unit
def test_parse_participant_config_document_accepts_embedded_test_plan(tmp_path: Path) -> None:
    """Enhanced config parsing returns both strict config and embedded testPlan."""
    document = parse_participant_config_document(
        {
            "environment": "test-env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testTarget": _target_config(),
            "testPlan": _test_plan_doc(resource_groups=["ais"]),
        },
        base_dir=tmp_path,
    )

    assert document.config.environment == "test-env"
    assert document.config.test_target is not None
    assert document.test_plan is not None
    assert document.test_plan.target.specification == "read-write"


@pytest.mark.unit
def test_parse_participant_config_document_rejects_test_plan_target_mismatch(tmp_path: Path) -> None:
    """Config/testPlan target coordinate mismatches are rejected."""
    with pytest.raises(ConfigDocumentError, match="specificationVersion"):
        parse_participant_config_document(
            {
                "environment": "test-env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testTarget": _target_config(specification_version="v4.0.0"),
                "testPlan": _test_plan_doc(specification_version="v4.0.1", resource_groups=["ais"]),
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_participant_config_document_rejects_bare_run_plan_confusion(tmp_path: Path) -> None:
    """Bare RunPlanV2 JSON is not accepted as config JSON."""
    bare_run_plan = {"schemaVersion": "2", **_test_plan_doc(resource_groups=["ais"])}

    with pytest.raises(ConfigDocumentError, match="bare RunPlanV2"):
        parse_participant_config_document(bare_run_plan, base_dir=tmp_path)


@pytest.mark.unit
def test_parse_participant_config_document_rejects_embedded_run_plan(tmp_path: Path) -> None:
    """Old top-level runPlan inputs are rejected instead of migrated."""
    with pytest.raises(ConfigDocumentError, match="runPlan.*no longer supported"):
        parse_participant_config_document(
            {
                "environment": "test-env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testTarget": _target_config(),
                "runPlan": {"schemaVersion": "2", **_test_plan_doc(resource_groups=["ais"])},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_participant_config_document_rejects_dcr_section_for_read_write_target(tmp_path: Path) -> None:
    """Read/Write configs with top-level DCR runtime fields get a target-specific error."""
    with pytest.raises(ConfigDocumentError, match="top-level 'dcr'.*read-write"):
        parse_participant_config_document(
            {
                "environment": "test-env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testTarget": _target_config(),
                "testPlan": _test_plan_doc(resource_groups=["ais"]),
                "dcr": {"tokenEndpointAuthMethod": "tls_client_auth"},
            },
            base_dir=tmp_path,
        )
