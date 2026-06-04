import json
from pathlib import Path

import pytest

from conformance.approved_releases import (
    APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
    ApprovedReleasePolicyError,
    load_approved_release_policy,
    parse_approved_release_policy,
)


@pytest.mark.unit
def test_parse_approved_release_policy_strips_versions_and_checks_exact_matches() -> None:
    policy = parse_approved_release_policy(
        {
            "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            "approvedToolVersions": [" 1.2.3 ", "1.2.30"],
        }
    )

    assert policy.approved_tool_versions == ("1.2.3", "1.2.30")
    assert policy.is_tool_version_approved("1.2.3") is True
    assert policy.is_tool_version_approved("1.2") is False


@pytest.mark.unit
def test_parse_approved_release_policy_rejects_empty_versions() -> None:
    with pytest.raises(ApprovedReleasePolicyError, match=r"approvedToolVersions\[0\] must not be empty"):
        parse_approved_release_policy(
            {
                "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
                "approvedToolVersions": ["   "],
            }
        )


@pytest.mark.unit
def test_load_approved_release_policy_loads_json_file(tmp_path: Path) -> None:
    policy_path = tmp_path / "approved-releases.json"
    policy_path.write_text(
        json.dumps(
            {
                "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
                "approvedToolVersions": ["1.2.3"],
            }
        ),
        encoding="utf-8",
    )

    policy = load_approved_release_policy(policy_path)

    assert policy.schema_version == APPROVED_RELEASE_POLICY_SCHEMA_VERSION
    assert policy.approved_tool_versions == ("1.2.3",)


@pytest.mark.unit
def test_load_approved_release_policy_wraps_invalid_json(tmp_path: Path) -> None:
    policy_path = tmp_path / "approved-releases.json"
    policy_path.write_text("{", encoding="utf-8")

    with pytest.raises(ApprovedReleasePolicyError, match="Invalid JSON approved-release policy"):
        load_approved_release_policy(policy_path)
