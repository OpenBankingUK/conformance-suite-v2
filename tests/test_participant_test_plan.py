"""Unit tests for participant-facing test-plan config parsing."""

from __future__ import annotations

import pytest

from conformance.json_types import JsonValue
from conformance.participant_test_plan import (
    ParticipantTestPlanParseError,
    parse_participant_test_plan,
    run_plan_v2_from_participant_test_plan,
    serialise_participant_test_plan,
)


def _valid_test_plan() -> dict[str, JsonValue]:
    """Return a valid participant testPlan JSON object.

    Returns:
        JSON object using the participant-facing test-plan contract.
    """
    return {
        "target": {
            "standard": "obl",
            "specification": "read-write",
            "securityProfile": "fapi1-advanced",
            "specificationVersion": "v4.0.1",
            "catalogueHash": "sha256:test",
        },
        "resourceGroups": ["ais"],
        "endpointSelections": [
            {
                "endpointId": "get-accounts",
                "operation": "GET",
                "selected": True,
                "fieldValues": {"accountId": "123"},
            }
        ],
        "testData": {"consentId": "abc"},
    }


@pytest.mark.unit
def test_parse_participant_test_plan_accepts_schema_less_contract() -> None:
    """testPlan parses without a schemaVersion discriminator."""
    plan = parse_participant_test_plan(_valid_test_plan())

    assert plan.target.specification == "read-write"
    assert plan.resource_groups == ("ais",)
    assert plan.endpoint_selections[0].field_values["accountId"] == "123"
    assert plan.test_data["consentId"] == "abc"


@pytest.mark.unit
def test_parse_participant_test_plan_rejects_schema_version() -> None:
    """testPlan rejects the retired RunPlanV2 schema discriminator."""
    with pytest.raises(ParticipantTestPlanParseError, match="must not include schemaVersion"):
        parse_participant_test_plan({"schemaVersion": "2", **_valid_test_plan()})


@pytest.mark.unit
def test_participant_test_plan_round_trips_through_internal_adapter() -> None:
    """Participant testPlan can be adapted to the current catalogue planner."""
    plan = parse_participant_test_plan(_valid_test_plan())
    internal_plan = run_plan_v2_from_participant_test_plan(plan)
    restored = parse_participant_test_plan(serialise_participant_test_plan(plan))

    assert internal_plan.schema_version == "2"
    assert internal_plan.target.catalogue_hash == "sha256:test"
    assert restored == plan
