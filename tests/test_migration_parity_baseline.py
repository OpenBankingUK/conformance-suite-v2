"""Tests for the phase-1 migration parity baseline artefacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from conformance.json_types import JsonObject

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_JSON = REPO_ROOT / "docs" / "requirements" / "suite-coverage" / "migration-parity-baseline.json"
BASELINE_MD = REPO_ROOT / "docs" / "requirements" / "suite-coverage" / "migration-parity-baseline.md"
CURRENT_SUITES_DIR = REPO_ROOT / "conformance" / "suites"
LEGACY_BASELINES = (
    REPO_ROOT / "docs" / "requirements" / "suite-coverage" / "v4-ais-prior-fcs-inventory.json",
    REPO_ROOT / "docs" / "requirements" / "suite-coverage" / "v4-pis-prior-fcs-inventory.json",
)


def _load_json(path: Path) -> JsonObject:
    """Load a JSON object from disk.

    Args:
        path: Path to the JSON document.

    Returns:
        The parsed JSON object.
    """
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def _current_source_ids() -> set[str]:
    """Return the unique source IDs from the bundled current suite manifests.

    Returns:
        The unique current-suite step identifiers.
    """
    ids: set[str] = set()
    for path in CURRENT_SUITES_DIR.glob("*.json"):
        manifest = _load_json(path)
        for raw_step in cast(list[object], manifest["steps"]):
            step = cast(JsonObject, raw_step)
            ids.add(cast(str, step["id"]))
    return ids


def _legacy_source_ids() -> set[str]:
    """Return the unique legacy source IDs from the parity inventory inputs.

    Returns:
        The unique previous-FCS script identifiers.
    """
    ids: set[str] = set()
    for path in LEGACY_BASELINES:
        inventory = _load_json(path)
        for raw_row in cast(list[object], inventory["records"]):
            row = cast(JsonObject, raw_row)
            ids.add(cast(str, row["legacyScriptId"]))
    return ids


@pytest.mark.unit
def test_migration_parity_baseline_covers_all_source_ids() -> None:
    """Verify the baseline ledger maps every current and legacy source ID."""
    baseline = _load_json(BASELINE_JSON)
    records = cast(list[object], baseline["records"])

    current_records = []
    legacy_records = []
    for raw_record in records:
        record = cast(JsonObject, raw_record)
        if cast(str, record["sourceKind"]) == "current-suite":
            current_records.append(record)
        if cast(str, record["sourceKind"]) == "previous-fcs":
            legacy_records.append(record)
    current_record_ids = {cast(str, record["sourceId"]) for record in current_records}
    legacy_record_ids = {cast(str, record["sourceId"]) for record in legacy_records}
    current_ids = _current_source_ids()
    legacy_ids = _legacy_source_ids()

    assert baseline["summary"] == {
        "currentSourceFiles": 25,
        "currentUniqueSourceIds": 98,
        "currentAppearances": 208,
        "legacySourceFiles": 2,
        "legacyUniqueSourceIds": 124,
        "legacyAppearances": 124,
        "totalRecords": 222,
        "directBenchmarkMapped": 48,
        "legacyPreserved": 76,
    }
    assert len(current_records) == 98
    assert len(legacy_records) == 124
    assert current_ids <= current_record_ids
    assert legacy_ids <= legacy_record_ids
    assert all(cast(list[object], cast(JsonObject, raw_record)["targetCatalogueTestIds"]) for raw_record in records)
    assert {cast(str, cast(JsonObject, raw_record)["mappingMode"]) for raw_record in records} <= {
        "current-step-id",
        "direct-benchmark",
        "legacy-preserved",
    }


@pytest.mark.unit
def test_migration_parity_baseline_markdown_matches_json_record_count() -> None:
    """Verify the generated reviewer table stays aligned with the JSON ledger."""
    baseline = _load_json(BASELINE_JSON)
    markdown_lines = BASELINE_MD.read_text(encoding="utf-8").splitlines()

    table_rows = [
        line for line in markdown_lines if line.startswith("| current-suite |") or line.startswith("| previous-fcs |")
    ]

    assert len(table_rows) == len(cast(list[object], baseline["records"]))


@pytest.mark.unit
def test_migration_parity_baseline_tracks_the_source_collections() -> None:
    """Verify the baseline references the intended manifest, inventory, and catalogue inputs."""
    baseline = _load_json(BASELINE_JSON)
    source_collections = cast(list[object], baseline["sourceCollections"])

    assert [cast(JsonObject, raw)["kind"] for raw in source_collections] == [
        "current-suite-manifests",
        "previous-fcs-inventories",
        "catalogue-and-standards-sources",
    ]
    assert cast(JsonObject, source_collections[0])["manifestCount"] == 25
    assert cast(str, cast(JsonObject, source_collections[0])["path"]) == "conformance/suites/*.json"
    assert cast(list[object], cast(JsonObject, source_collections[1])["paths"]) == [
        "docs/requirements/suite-coverage/v4-ais-prior-fcs-inventory.json",
        "docs/requirements/suite-coverage/v4-pis-prior-fcs-inventory.json",
    ]
