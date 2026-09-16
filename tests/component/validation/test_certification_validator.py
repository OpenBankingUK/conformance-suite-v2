"""Offline component proof for the approved-release certification boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.certification_validator import validate_certification_report
from conformance.configuration_contracts.v2_loader import (
    dump_execution_manifest,
    dump_resolved_plan,
)
from tests.support.certification import RELEASE_PATH, build_certification_fixture
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.component


def test_complete_shipped_release_run_is_independently_certification_eligible(
    tmp_path: Path,
) -> None:
    """Validate the full release-to-observation chain without network access."""
    fixture = build_certification_fixture(
        warning_step_id="dcr.v34.test.retrieval.positive.instance.request",
    )
    report_path = tmp_path / "result.json"
    resolved_path = tmp_path / "resolved-plan.json"
    manifest_path = tmp_path / "execution-manifest.json"
    report_path.write_text(json.dumps(fixture.report), encoding="utf-8")
    resolved_path.write_text(dump_resolved_plan(fixture.resolved_plan), encoding="utf-8")
    manifest_path.write_text(dump_execution_manifest(fixture.manifest), encoding="utf-8")

    result = validate_certification_report(
        report_path,
        suite_release_path=RELEASE_PATH,
        resolved_plan_path=resolved_path,
        manifest_path=manifest_path,
        trusted_root=REPO_ROOT,
    )

    assert result.valid is True
    assert result.complete is True
    assert result.automated_assessment == "passed"
    assert result.eligible is True
    assert {item.status for item in result.test_outcomes} == {"passed", "warn"}
