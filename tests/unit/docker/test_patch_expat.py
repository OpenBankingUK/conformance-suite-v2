"""Tests for the temporary Expat APK inventory patch."""

import pytest

from docker.patch_expat import merge_expat_records

pytestmark = pytest.mark.unit


def _db(version: str, extra: str = "", arch: str = "aarch64") -> str:
    return (
        f"P:expat\nV:{version}\nA:{arch}\n\n"
        f"P:libexpat\nV:{version}\nA:{arch}\n\n"
        "P:musl\nV:1.2.6-r0\nA:aarch64\n\n" + extra
    )


def test_preserves_runtime_inventory_and_uses_complete_new_records() -> None:
    runtime = _db("2.8.5-r0", "P:runtime-only\nV:1\n\n")
    before = _db("2.8.5-r0", "P:dev-only\nV:1\n\n")
    after = _db("2.9.0-r0", "P:dev-only\nV:1\n\n").replace("P:libexpat", "C:verified-apk-checksum\nP:libexpat")
    result = merge_expat_records(runtime, before, after)
    assert result == after.replace("P:dev-only", "P:runtime-only")
    assert "2.8.5-r0" not in result


def test_rejects_musl_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_expat_records(_db("2.8.5-r0"), _db("2.8.5-r0"), _db("2.9.0-r0").replace("1.2.6-r0", "1.2.7-r0"))


@pytest.mark.parametrize("arch", ["x86_64", ""])
def test_rejects_incompatible_architecture(arch: str) -> None:
    with pytest.raises(ValueError, match="architecture"):
        merge_expat_records(_db("2.8.5-r0"), _db("2.8.5-r0"), _db("2.9.0-r0", arch=arch))


def test_rejects_wrong_fixed_version() -> None:
    with pytest.raises(ValueError, match="pinned fixed version"):
        merge_expat_records(_db("2.8.5-r0"), _db("2.8.5-r0"), _db("2.9.1-r0"))


@pytest.mark.parametrize("database", ["V:1\n", "P:expat\n\nP:expat\n"])
def test_rejects_ambiguous_database(database: str) -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        merge_expat_records(database, _db("2.8.5-r0"), _db("2.9.0-r0"))
