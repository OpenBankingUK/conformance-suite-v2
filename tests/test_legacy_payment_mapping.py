"""Unit tests for the legacy PIS payment parity mapping artifact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
def test_legacy_payment_mapping_has_expected_counts_and_family_grouping() -> None:
    """Verify the legacy payment mapping keeps the expected family split."""
    mapping_path = (
        Path(__file__).resolve().parents[1]
        / "conformance"
        / "standards"
        / "ob_read_write"
        / "v4_0"
        / "legacy-ob_4.0_payment_fca_mapping.json"
    )
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    assert mapping["summary"] == {"mandatoryCount": 8, "conditionalCount": 21, "totalCount": 29}
    assert [family["family"] for family in mapping["families"]] == [
        "DomesticPayment",
        "DomesticScheduledPayment",
        "DomesticStandingOrder",
        "InternationalPayment",
        "InternationalScheduledPayment",
    ]

    rows_by_family = {family["family"]: family["rows"] for family in mapping["families"]}
    assert len(rows_by_family["DomesticPayment"]) == 8
    assert len(rows_by_family["DomesticScheduledPayment"]) == 7
    assert len(rows_by_family["DomesticStandingOrder"]) == 6
    assert len(rows_by_family["InternationalPayment"]) == 4
    assert len(rows_by_family["InternationalScheduledPayment"]) == 4


@pytest.mark.unit
def test_legacy_payment_mapping_includes_key_source_ids_and_gap_metadata() -> None:
    """Verify the mapping exposes the legacy ids and gap metadata needed by tests."""
    mapping_path = (
        Path(__file__).resolve().parents[1]
        / "conformance"
        / "standards"
        / "ob_read_write"
        / "v4_0"
        / "legacy-ob_4.0_payment_fca_mapping.json"
    )
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    rows = [row for family in mapping["families"] for row in family["rows"]]
    ids = {row["legacyId"] for row in rows}

    assert {
        "OB-400-DOP-100100",
        "OB-400-DOP-100110",
        "OB-400-DOP-100300",
        "OB-316-DOP-100310",
        "OB-400-DOP-100400",
        "OB-400-DOP-100500",
        "OB-400-DOP-100600",
        "OB-400-DOP-100700",
        "OB-400-DOP-100800",
        "OB-400-DOP-100810",
        "OB-400-DOP-100820",
        "OB-400-DOP-100900",
        "OB-400-DOP-101000",
        "OB-400-DOP-101100",
        "OB-400-DOP-101101",
        "OB-400-DOP-101200",
        "OB-400-DOP-101300",
        "OB-400-DOP-101400",
        "OB-400-DOP-101401",
        "OB-400-DOP-101500",
        "OB-400-DOP-101503",
        "OB-400-DOP-101600",
        "OB-400-DOP-101700",
        "OB-400-DOP-101800",
        "OB-400-DOP-101900",
        "OB-400-DOP-102000",
        "OB-400-DOP-102100",
        "OB-400-DOP-102200",
        "OB-400-DOP-102300",
    }.issubset(ids)

    standing_order_101500 = next(row for row in rows if row["legacyId"] == "OB-400-DOP-101500")
    assert standing_order_101500["legacyUri"] == "/domestic-standing-orders/$paymentID"
    assert standing_order_101500["legacyResource"] == "DomesticScheduledPayment"
    assert "DomesticStandingOrder" in mapping["familyNotes"]
    assert standing_order_101500["knownGaps"]
    assert any(
        "Legacy resource is labelled DomesticScheduledPayment" in gap for gap in standing_order_101500["knownGaps"]
    )

    for row in rows:
        coverage = row["currentAssertionCoverage"]
        assert "coverage" in coverage
        assert isinstance(coverage["legacyAssertions"], list)
        assert isinstance(coverage["legacyAssertionsOneOf"], list)
