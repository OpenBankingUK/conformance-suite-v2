"""Unit tests for the structured conformance result gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.json_types import JsonObject
from conformance.result_gate import StructuredResultGateError, validate_structured_conformance_result

pytestmark = pytest.mark.unit


def test_structured_result_gate_rejects_transcript_style_false_positive() -> None:
    """The operational gate rejects a failed case despite a passing top-level label."""
    raw_result: JsonObject = {
        "status": "passed",
        "summary": {"failed": 0},
        "steps": [{"name": "DCR-001-C01-S01", "status": "passed", "message": "ok"}],
        "catalogue": {
            "api": "dcr",
            "traceGroups": [
                {
                    "traceGroupId": "DCR-001",
                    "status": "failed",
                    "testCases": [
                        {
                            "testCaseId": "DCR-001-C01",
                            "status": "failed",
                            "steps": [{"stepId": "DCR-001-C01-S01", "status": "failed"}],
                        }
                    ],
                }
            ],
        },
    }

    with pytest.raises(StructuredResultGateError, match="failed conformance scenario"):
        validate_structured_conformance_result(raw_result)


def test_structured_result_gate_accepts_explicit_optional_skips() -> None:
    """Endpoint-not-selected cases remain valid when every executed case passed."""
    raw_result: JsonObject = {
        "status": "passed",
        "summary": {"failed": 0},
        "steps": [{"name": "DCR-001-C01-S01", "status": "passed", "message": "ok"}],
        "catalogue": {
            "api": "dcr",
            "traceGroups": [
                {
                    "traceGroupId": "DCR-001",
                    "status": "passed",
                    "testCases": [
                        {
                            "testCaseId": "DCR-001-C01",
                            "status": "passed",
                            "steps": [{"stepId": "DCR-001-C01-S01", "status": "passed"}],
                        }
                    ],
                },
                {
                    "traceGroupId": "DCR-003",
                    "status": "skipped",
                    "skipReason": "endpoint-not-selected",
                    "testCases": [
                        {
                            "testCaseId": "DCR-003-C01",
                            "status": "skipped",
                            "skipReason": "endpoint-not-selected",
                            "steps": [{"stepId": "DCR-003-C01-S01", "status": "skipped"}],
                        }
                    ],
                },
            ],
        },
    }

    validated = validate_structured_conformance_result(raw_result)

    assert validated == {"status": "passed", "failed": 0, "stepCount": 1, "caseCount": 2}


def test_result_gate_cli_reports_conformance_failure(tmp_path: Path) -> None:
    """Result-gate CLI returns one for a structured failed result."""
    from conformance import result_gate

    result_path = tmp_path / "failed-result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "summary": {"failed": 1},
                "steps": [{"name": "DCR-001-C01-S01", "status": "failed", "message": "failed"}],
            }
        ),
        encoding="utf-8",
    )

    assert result_gate.run([str(result_path)]) == 1
