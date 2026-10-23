"""Tests for removal of the old interpreter and truthful derivative inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

from docker.replace_python import REPLACED_PACKAGES, replace_python

pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runtime"
    info = root / "var/lib/dpkg/info"
    info.mkdir(parents=True)
    paragraphs: list[str] = []
    for package in sorted(REPLACED_PACKAGES):
        path = root / f"usr/lib/{package}/old.py"
        path.parent.mkdir(parents=True)
        path.write_text("old payload")
        (info / f"{package}.list").write_text(f"/\n/usr\n/usr/lib\n/usr/lib/{package}\n{str(path)[len(str(root)) :]}\n")
        (info / f"{package}.md5sums").write_text(f"0000  usr/lib/{package}/old.py\n")
        paragraphs.append(
            f"Package: {package}\nArchitecture: arm64\nVersion: 3.14.8-0\n"
            "Depends: libc6 (>= 2.34), python-3.14 (= 3.14.8-0)\n"
        )
    paragraphs.append("Package: zlib1g\nVersion: 1.3.1+dhi4\n")
    (root / "var/lib/dpkg/status").write_text("\n".join(paragraphs))
    split_status = root / "var/lib/dpkg/status.d"
    split_status.mkdir()
    for package in REPLACED_PACKAGES:
        (split_status / package).write_text(f"Package: {package}\nVersion: 3.14.8-0\n")
    (root / "usr/bin").mkdir()
    shared = root / "usr/lib/libz.so.1"
    shared.write_text("retained DHI backport")
    payload = tmp_path / "payload"
    binary = payload / "opt/python/bin/python3.14"
    binary.parent.mkdir(parents=True)
    binary.write_text("verified new binary")
    binary.chmod(0o755)
    (binary.parent / "python3").symlink_to("python3.14")
    return root, payload


def test_replaces_owned_files_but_retains_shared_library_and_records_new_package(tmp_path: Path) -> None:
    root, payload = _runtime(tmp_path)

    replace_python(root, payload)

    assert (root / "opt/python/bin/python3.14").read_text() == "verified new binary"
    assert (root / "opt/python/bin/python3.14").stat().st_mode & 0o111
    assert (root / "usr/bin/python3").readlink() == Path("/opt/python/bin/python3.14")
    assert (root / "usr/lib/libz.so.1").read_text() == "retained DHI backport"
    assert not (root / "usr/lib/libpython-3.14").exists()
    assert not list((root / "var/lib/dpkg/info").glob("python-3.14*"))
    status = (root / "var/lib/dpkg/status").read_text()
    assert "Package: zlib1g\nVersion: 1.3.1+dhi4" in status
    assert "Package: conformance-python\n" in status
    assert "Source: python-3.14 (3.14.9)\nVersion: 3.14.9-1+ob1" in status
    assert "Depends: libc6 (>= 2.34)\n" in status
    assert "3.14.8" not in status
    assert [path.name for path in (root / "var/lib/dpkg/status.d").iterdir()] == ["conformance-python"]
    assert "Version: 3.14.9-1+ob1" in (root / "var/lib/dpkg/status.d/conformance-python").read_text()
    assert "opt/python/bin/python3.14\n" in (root / "var/lib/dpkg/info/conformance-python.md5sums").read_text()


def test_changed_base_inventory_fails_before_removal(tmp_path: Path) -> None:
    root, payload = _runtime(tmp_path)
    status = root / "var/lib/dpkg/status"
    status.write_text(status.read_text().replace("Package: python-3.14\n", "Package: unexpected-python\n"))

    with pytest.raises(ValueError, match="Missing expected DHI packages"):
        replace_python(root, payload)
    assert (root / "usr/lib/python-3.14/old.py").exists()


def test_invalid_package_path_cannot_remove_files_outside_runtime(tmp_path: Path) -> None:
    root, payload = _runtime(tmp_path)
    outside = tmp_path / "keep"
    outside.write_text("keep")
    (root / "var/lib/dpkg/info/python-3.14.list").write_text("/../keep\n")

    with pytest.raises(ValueError, match="escapes runtime root"):
        replace_python(root, payload)
    assert outside.read_text() == "keep"


def test_source_download_and_runtime_version_match_package_inventory() -> None:
    from docker.replace_python import PYTHON_VERSION

    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text()
    assert f"/Python-{PYTHON_VERSION}.tar.xz" in dockerfile
    assert f'ENV PYTHON_VERSION="{PYTHON_VERSION}"' in dockerfile
    assert '"/usr/bin/python3", "-B", "/replacement/replace_python.py"' in dockerfile


def test_md5_inventory_removes_package_filenames_containing_spaces(tmp_path: Path) -> None:
    root, payload = _runtime(tmp_path)
    extra = root / "usr/lib/python3-setuptools/script (dev).tmpl"
    extra.write_text("old packaging payload")
    checksums = root / "var/lib/dpkg/info/python3-setuptools.md5sums"
    checksums.write_text(checksums.read_text() + "0000  usr/lib/python3-setuptools/script (dev).tmpl\n")

    replace_python(root, payload)

    assert not extra.exists()
    assert not extra.parent.exists()
