"""Unit tests for :mod:`conformance.execution_schedule`."""

from __future__ import annotations

from typing import cast

import pytest

from conformance.execution_schedule import build_execution_schedule
from conformance.json_types import JsonValue
from conformance.manifest import ManifestStep, PsuAuthorizationStep, parse_manifest
from conformance.test_plan import TestPlan


def _grouped_manifest() -> dict[str, JsonValue]:
    """Build a v1 manifest with setup + grouped execution steps.

    Returns:
        Manifest object used by schedule-focused tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "Grouped execution",
        "steps": [
            {
                "id": "setup-discovery",
                "name": "Discovery",
                "phase": "setup",
                "group": "bootstrap",
                "request": {"method": "GET", "url": "https://example.com/discovery"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "exec-zeta-1",
                "name": "Group zeta step 1",
                "group": "zeta",
                "request": {"method": "GET", "url": "https://example.com/zeta-1"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "exec-alpha-1",
                "name": "Group alpha step 1",
                "group": "alpha",
                "request": {"method": "GET", "url": "https://example.com/alpha-1"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "exec-zeta-2",
                "name": "Group zeta step 2",
                "group": "zeta",
                "request": {"method": "GET", "url": "https://example.com/zeta-2"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "exec-default",
                "name": "Default group step",
                "request": {"method": "GET", "url": "https://example.com/default"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }


@pytest.mark.unit
def test_build_execution_schedule_orders_setup_then_grouped_execution() -> None:
    """Selected setup steps and execution groups are derived deterministically."""
    manifest = parse_manifest(_grouped_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest)

    schedule = build_execution_schedule(manifest, plan)

    assert [step.id for step in schedule.setup_steps] == ["setup-discovery"]
    assert [group.group_id for group in schedule.execution_groups] == ["zeta", "alpha", "default"]
    assert [step.id for step in schedule.execution_groups[0].steps] == ["exec-zeta-1", "exec-zeta-2"]
    assert [step.id for step in schedule.execution_groups[1].steps] == ["exec-alpha-1"]
    assert [step.id for step in schedule.execution_groups[2].steps] == ["exec-default"]


@pytest.mark.unit
def test_build_execution_schedule_respects_deselection() -> None:
    """Deselected setup and execution steps are omitted from the schedule."""
    manifest = parse_manifest(_grouped_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest).with_deselection(["setup-discovery", "exec-alpha-1"])

    schedule = build_execution_schedule(manifest, plan)

    assert schedule.setup_steps == ()
    assert [group.group_id for group in schedule.execution_groups] == ["zeta", "default"]
    assert [step.id for step in schedule.execution_groups[0].steps] == ["exec-zeta-1", "exec-zeta-2"]


@pytest.mark.unit
def test_build_execution_schedule_supports_psu_steps() -> None:
    """PSU and HTTP steps can coexist in the same execution group."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "PSU grouped",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "phase": "setup",
                "request": {"method": "GET", "url": "https://example.com/discovery"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "manual",
                "group": "consent",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "synthetic-client-id-00000000",
                "redirectUri": "https://conformance.example.com/callback",
            },
            {
                "id": "token",
                "name": "Token",
                "group": "consent",
                "request": {"method": "POST", "url": "https://example.com/token", "body": {"code": "abc"}},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    plan = TestPlan.default_plan_from_manifest(manifest)

    schedule = build_execution_schedule(manifest, plan)

    assert [step.id for step in schedule.setup_steps] == ["discovery"]
    assert [group.group_id for group in schedule.execution_groups] == ["consent"]
    consent_steps = schedule.execution_groups[0].steps
    assert isinstance(consent_steps[0], PsuAuthorizationStep)
    assert isinstance(cast("ManifestStep", consent_steps[1]), ManifestStep)


@pytest.mark.unit
def test_build_execution_schedule_returns_empty_for_v0_manifest() -> None:
    """v0 manifests have no phase/group metadata and produce an empty schedule."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v0",
        "name": "v0",
        "tests": [
            {
                "id": "health",
                "name": "Health",
                "request": {"method": "GET", "url": "https://example.com/health"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }
    manifest = parse_manifest(raw_manifest)

    schedule = build_execution_schedule(manifest, TestPlan(entries=()))

    assert schedule.setup_steps == ()
    assert schedule.execution_groups == ()
