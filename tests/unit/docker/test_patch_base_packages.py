"""Tests for the temporary base-package APK inventory patch."""

import pytest

from docker.patch_base_packages import merge_patched_records

pytestmark = pytest.mark.unit


def _db(expat: str, zlib: str, extra: str = "", arch: str = "aarch64") -> str:
    return (
        f"P:expat\nV:{expat}\nA:{arch}\n\n"
        f"P:libexpat\nV:{expat}\nA:{arch}\n\n"
        "P:musl\nV:1.2.6-r0\nA:aarch64\n\n"
        f"P:zlib\nV:{zlib}\nA:{arch}\n\n" + extra
    )


OLD = ("2.8.5-r0", "1.3.2-r0")
NEW = ("2.9.0-r0", "1.3.2-r1")


def test_preserves_runtime_inventory_and_uses_complete_new_records() -> None:
    runtime = _db(*OLD, "P:runtime-only\nV:1\n\n")
    before = _db(*OLD, "P:dev-only\nV:1\n\n")
    after = (
        _db(*NEW, "P:dev-only\nV:1\n\n")
        .replace("P:libexpat", "C:verified-libexpat-checksum\nP:libexpat")
        .replace("P:zlib", "C:verified-zlib-checksum\nP:zlib")
    )
    result = merge_patched_records(runtime, before, after)
    assert result == after.replace("P:dev-only", "P:runtime-only")
    assert "2.8.5-r0" not in result
    assert "V:1.3.2-r0" not in result


def test_rejects_musl_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_patched_records(_db(*OLD), _db(*OLD), _db(*NEW).replace("1.2.6-r0", "1.2.7-r0"))


def test_rejects_missing_zlib_upgrade() -> None:
    with pytest.raises(ValueError, match=r"apk changed \['expat', 'libexpat'\]"):
        merge_patched_records(_db(*OLD), _db(*OLD), _db(NEW[0], OLD[1]))


@pytest.mark.parametrize("arch", ["x86_64", ""])
def test_rejects_incompatible_architecture(arch: str) -> None:
    with pytest.raises(ValueError, match="architecture"):
        merge_patched_records(_db(*OLD), _db(*OLD), _db(*NEW, arch=arch))


@pytest.mark.parametrize(("expat", "zlib"), [("2.9.1-r0", NEW[1]), (NEW[0], "1.3.2-r2")])
def test_rejects_wrong_fixed_version(expat: str, zlib: str) -> None:
    with pytest.raises(ValueError, match="pinned fixed version"):
        merge_patched_records(_db(*OLD), _db(*OLD), _db(expat, zlib))


@pytest.mark.parametrize("database", ["V:1\n", "P:expat\n\nP:expat\n"])
def test_rejects_ambiguous_database(database: str) -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        merge_patched_records(database, _db(*OLD), _db(*NEW))
