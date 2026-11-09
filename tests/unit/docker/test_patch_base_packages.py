"""Tests for the temporary Expat and zlib APK inventory patch."""

import pytest

from docker.patch_base_packages import merge_patched_records

pytestmark = pytest.mark.unit


def _db(
    expat: str,
    zlib: str,
    extra: str = "",
    arch: str = "aarch64",
    zlib_dev: str | None = None,
    zlib_arch: str | None = None,
) -> str:
    records = [
        f"P:expat\nV:{expat}\nA:{arch}",
        f"P:libexpat\nV:{expat}\nA:{arch}",
        f"P:zlib\nV:{zlib}\nA:{arch if zlib_arch is None else zlib_arch}",
        "P:musl\nV:1.2.6-r0\nA:aarch64",
    ]
    if zlib_dev is not None:
        records.append(f"P:zlib-dev\nV:{zlib_dev}\nA:{arch if zlib_arch is None else zlib_arch}")
    return "\n\n".join(records) + "\n\n" + extra


OLD_EXPAT = "2.9.0-r1"
NEW_EXPAT = "2.10.0-r0"
OLD_ZLIB = "1.3.2-r1"
NEW_ZLIB = "1.4-r0"


def test_preserves_runtime_inventory_and_uses_patched_records() -> None:
    runtime = _db(OLD_EXPAT, OLD_ZLIB, "P:runtime-only\nV:1\n\n")
    before = _db(OLD_EXPAT, OLD_ZLIB, "P:dev-only\nV:1\n\n", zlib_dev=OLD_ZLIB)
    after = _db(NEW_EXPAT, NEW_ZLIB, "P:dev-only\nV:1\n\n", zlib_dev=NEW_ZLIB)

    result = merge_patched_records(runtime, before, after)

    assert result == _db(NEW_EXPAT, NEW_ZLIB, "P:runtime-only\nV:1\n\n")
    assert "P:dev-only" not in result
    assert "P:zlib-dev" not in result
    assert OLD_EXPAT not in result
    assert OLD_ZLIB not in result


def test_rejects_staging_only_packages_in_runtime_inventory() -> None:
    with pytest.raises(ValueError, match="Staging-only packages"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(NEW_EXPAT, NEW_ZLIB, zlib_dev=NEW_ZLIB),
        )


def test_rejects_unrelated_package_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(NEW_EXPAT, NEW_ZLIB, zlib_dev=NEW_ZLIB).replace("V:1.2.6-r0", "V:1.2.7-r0"),
        )


def test_rejects_missing_zlib_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(NEW_EXPAT, OLD_ZLIB, zlib_dev=NEW_ZLIB),
        )


@pytest.mark.parametrize("arch", ["x86_64", ""])
def test_rejects_zlib_architecture_drift(arch: str) -> None:
    with pytest.raises(ValueError, match="architecture"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(NEW_EXPAT, NEW_ZLIB, zlib_dev=NEW_ZLIB, zlib_arch=arch),
        )


@pytest.mark.parametrize("version", ["1.4-r1", "1.3.9-r0"])
def test_rejects_wrong_pinned_zlib_version(version: str) -> None:
    with pytest.raises(ValueError, match="pinned fixed version"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(NEW_EXPAT, version, zlib_dev=NEW_ZLIB),
        )


def test_rejects_missing_expat_upgrade() -> None:
    with pytest.raises(ValueError, match="apk changed"):
        merge_patched_records(
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(OLD_EXPAT, OLD_ZLIB, zlib_dev=OLD_ZLIB),
            _db(OLD_EXPAT, NEW_ZLIB, zlib_dev=NEW_ZLIB),
        )


@pytest.mark.parametrize("database", ["V:1\n", "P:expat\n\nP:expat\n"])
def test_rejects_ambiguous_database(database: str) -> None:
    with pytest.raises(ValueError, match="missing or duplicate"):
        merge_patched_records(
            database,
            _db(OLD_EXPAT, OLD_ZLIB),
            _db(NEW_EXPAT, NEW_ZLIB, zlib_dev=NEW_ZLIB),
        )
