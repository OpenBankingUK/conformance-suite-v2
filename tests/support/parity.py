"""Assertions that bind catalogue migration reports to pinned legacy bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from conformance.json_types import JsonObject


def assert_parity_report_matches_baseline(report: JsonObject, baseline_path: Path) -> None:
    """Assert that every baseline row has one exact parity classification."""
    baseline = cast(JsonObject, report["baseline"])
    classifications = cast(list[JsonObject], report["classifications"])
    baseline_document = cast(JsonObject, json.loads(baseline_path.read_text(encoding="utf-8")))
    scripts = cast(list[JsonObject], baseline_document["scripts"])

    assert baseline["file"] == baseline_path.name
    assert baseline["digest"] == f"sha256:{hashlib.sha256(baseline_path.read_bytes()).hexdigest()}"
    assert len(classifications) == len(scripts)

    for index, (classification, script) in enumerate(zip(classifications, scripts, strict=True)):
        script_id = script["id"]
        assert isinstance(script_id, str)
        assert classification["legacyScriptId"] == script_id
        assert classification["sourceRow"] == f"{baseline_path.name}#{index}:{script_id}"
