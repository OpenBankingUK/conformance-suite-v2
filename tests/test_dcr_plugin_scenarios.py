"""Unit tests for conformance.plugins.dcr.scenarios module."""

from __future__ import annotations

import pytest

from conformance.plugins.dcr.scenarios import (
    ALL_SCENARIOS,
    applicable_scenarios,
    scenario_by_id,
)

_ALL_SCENARIO_IDS = {s.scenario_id for s in ALL_SCENARIOS}


@pytest.mark.unit
class TestAllScenarios:
    """Verify ALL_SCENARIOS registry completeness and structure."""

    def test_all_required_scenario_ids_present(self) -> None:
        """All documented scenario IDs are registered in ALL_SCENARIOS."""
        expected = {
            "DCR-001",
            "DCR-002",
            "DCR-003",
            "DCR-004",
            "DCR-005",
            "DCR-007",
            "DCR-008",
            "DCR-009",
            "DCR-010",
            "DCR-011",
        }
        assert expected.issubset(_ALL_SCENARIO_IDS)

    def test_dcr001_is_positive(self) -> None:
        """DCR-001 is a positive (non-negative) scenario."""
        scenario = scenario_by_id("DCR-001")
        assert scenario is not None
        assert scenario.is_negative is False

    def test_negative_scenarios_are_marked(self) -> None:
        """DCR-005 through DCR-011 are all marked as negative scenarios."""
        for sid in ("DCR-005", "DCR-007", "DCR-008", "DCR-009", "DCR-010", "DCR-011"):
            scenario = scenario_by_id(sid)
            assert scenario is not None, f"{sid} not found"
            assert scenario.is_negative is True, f"{sid} should be negative"

    def test_dcr002_requires_get(self) -> None:
        """DCR-002 is marked as requiring GET to be advertised."""
        scenario = scenario_by_id("DCR-002")
        assert scenario is not None
        assert scenario.requires_get is True

    def test_dcr003_requires_put(self) -> None:
        """DCR-003 is marked as requiring PUT to be advertised."""
        scenario = scenario_by_id("DCR-003")
        assert scenario is not None
        assert scenario.requires_put is True

    def test_dcr004_requires_delete_advertised(self) -> None:
        """DCR-004 is marked as requiring DELETE to be advertised."""
        scenario = scenario_by_id("DCR-004")
        assert scenario is not None
        assert scenario.requires_delete_advertised is True

    def test_dcr010_requires_delete_succeeded(self) -> None:
        """DCR-010 requires both DELETE advertised and delete succeeded."""
        scenario = scenario_by_id("DCR-010")
        assert scenario is not None
        assert scenario.requires_delete is True
        assert scenario.requires_delete_advertised is True

    def test_scenario_by_id_returns_none_for_unknown(self) -> None:
        """scenario_by_id returns None for an unknown scenario ID."""
        assert scenario_by_id("DCR-999") is None

    def test_catalogue_entry_ids_are_non_empty(self) -> None:
        """All scenarios have non-empty catalogue_entry_id values."""
        for scenario in ALL_SCENARIOS:
            assert scenario.catalogue_entry_id, f"{scenario.scenario_id} has empty catalogue_entry_id"


@pytest.mark.unit
class TestApplicableScenarios:
    """Verify applicable_scenarios filtering logic."""

    def test_returns_all_scenarios_when_all_advertised(self) -> None:
        """All scenarios are returned when all operations are advertised and delete succeeded."""
        scenarios = applicable_scenarios(
            advertise_get=True,
            advertise_put=True,
            advertise_delete=True,
            delete_succeeded=True,
        )
        scenario_ids = {s.scenario_id for s in scenarios}
        expected_ids = {
            "DCR-001",
            "DCR-002",
            "DCR-003",
            "DCR-004",
            "DCR-005",
            "DCR-007",
            "DCR-008",
            "DCR-009",
            "DCR-010",
            "DCR-011",
        }
        assert expected_ids.issubset(scenario_ids)

    def test_excludes_dcr002_when_get_not_advertised(self) -> None:
        """DCR-002 is excluded when advertise_get=False."""
        scenarios = applicable_scenarios(advertise_get=False)
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-002" not in ids

    def test_excludes_dcr003_when_put_not_advertised(self) -> None:
        """DCR-003 is excluded when advertise_put=False."""
        scenarios = applicable_scenarios(advertise_put=False)
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-003" not in ids

    def test_excludes_dcr004_when_delete_not_advertised(self) -> None:
        """DCR-004 is excluded when advertise_delete=False."""
        scenarios = applicable_scenarios(advertise_delete=False)
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-004" not in ids

    def test_excludes_dcr010_when_delete_not_advertised(self) -> None:
        """DCR-010 is excluded when advertise_delete=False."""
        scenarios = applicable_scenarios(advertise_delete=False, delete_succeeded=False)
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-010" not in ids

    def test_excludes_dcr010_when_delete_not_succeeded(self) -> None:
        """DCR-010 is excluded when advertise_delete=True but delete_succeeded=False."""
        scenarios = applicable_scenarios(
            advertise_delete=True,
            delete_succeeded=False,
        )
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-010" not in ids

    def test_includes_dcr010_when_delete_succeeded(self) -> None:
        """DCR-010 is included when both advertise_delete=True and delete_succeeded=True."""
        scenarios = applicable_scenarios(
            advertise_delete=True,
            delete_succeeded=True,
        )
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-010" in ids

    def test_dcr001_always_included(self) -> None:
        """DCR-001 is always in the applicable scenarios regardless of flags."""
        scenarios = applicable_scenarios(advertise_get=False, advertise_put=False, advertise_delete=False)
        ids = {s.scenario_id for s in scenarios}
        assert "DCR-001" in ids

    def test_negative_tests_always_included(self) -> None:
        """Negative scenarios DCR-005, 007, 008, 009, 011 are always included."""
        scenarios = applicable_scenarios(advertise_get=False, advertise_put=False, advertise_delete=False)
        ids = {s.scenario_id for s in scenarios}
        for sid in ("DCR-005", "DCR-007", "DCR-008", "DCR-009", "DCR-011"):
            assert sid in ids, f"{sid} should always be applicable"

    def test_returns_tuple(self) -> None:
        """applicable_scenarios returns a tuple, not a list."""
        result = applicable_scenarios()
        assert isinstance(result, tuple)
