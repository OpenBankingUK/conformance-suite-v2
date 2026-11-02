"""Tests for the temporary Expat APK inventory patch."""

import pytest

from docker.patch_base_packages import merge_patched_records

pytestmark = pytest.mark.unit


def _db(expat: str, extra: str = "", arch: str = "aarch64") -> str:
    return (
        f"P:expat\nV:{expat}\nA:{arch}\n\nP:libexpat\nV:{expat}\nA:{arch}\n\nP:musl\nV:1.2.6-r0\nA:aarch64\n\n" + extra
    )


OLD = "2.9.0-r1"
NEW = "2.10.0-r0"


def test_preserves_runtime_inventory_and_uses_patched_records() -> None:
    runtime = _db(OLD, "P:runtime-only\nV:1\n\n")
    before = _db(OLD, "P:dev-only\nV:1\n\n")
    after = _db(NEW, "P:dev-only\nV:1\n\n")

    result = merge_patched_records(runtime, before, after)

    assert result == _db(NEW, "P:runtime-only\nV:1\n\n")
    assert "P:dev-only" not in result
    assert "V:2.9.0-r1" not in result


def test_rejects_unrelated_package_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_patched_records(
            _db(OLD),
            _db(OLD),
            _db(NEW).replace("V:1.2.6-r0", "V:1.2.7-r0"),
        )


def test_rejects_missing_expat_upgrade() -> None:
    with pytest.raises(ValueError, match=r"apk changed \['libexpat'\]"):
        merge_patched_records(
            _db(OLD),
            _db(OLD),
            _db(NEW).replace(f"V:{NEW}", f"V:{OLD}", 1),
        )


@pytest.mark.parametrize("arch", ["x86_64", ""])
def test_rejects_architecture_drift(arch: str) -> None:
    with pytest.raises(ValueError, match="architecture"):
        merge_patched_records(_db(OLD), _db(OLD), _db(NEW, arch=arch))


@pytest.mark.parametrize("version", ["2.10.0-r1", "2.9.9-r0"])
def test_rejects_wrong_pinned_version(version: str) -> None:
    with pytest.raises(ValueError, match="pinned fixed version"):
        merge_patched_records(_db(OLD), _db(OLD), _db(version))


@pytest.mark.parametrize("database", ["V:1\n", "P:expat\n\nP:expat\n"])
def test_rejects_ambiguous_database(database: str) -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        merge_patched_records(database, _db(OLD), _db(NEW))
