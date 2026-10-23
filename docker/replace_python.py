"""Replace DHI's Python payload while preserving truthful Debian inventory.

Only the pinned DHI Python/packaging packages are removed. System libraries
retain their original ownership and Docker's backport assessments. The new
CPython payload is separately owned and must not inherit vendor Python VEX.
"""

from __future__ import annotations

import errno
import hashlib
import shutil
import sys
from pathlib import Path

PYTHON_VERSION = "3.14.9"
REPLACED_PACKAGES = frozenset(
    {
        "libpython-3.14",
        "libpython-3.14-minimal",
        "libpython-3.14-stdlib",
        "python-3.14",
        "python-3.14-minimal",
        "python3-pip",
        "python3-pip-whl",
        "python3-setuptools",
        "python3-setuptools-whl",
        "python3-wheel",
        "python3-wheel-whl",
    }
)


def replace_python(root: Path, payload: Path) -> None:
    """Remove package-owned old files and install the verified CPython payload.

    The build fails if the base's package set or ownership layout has changed.
    Shared directories are not recursively deleted. Inventory is written only
    after the new payload is installed successfully.
    """
    root = root.resolve()
    database = root / "var/lib/dpkg"
    paragraphs = database.joinpath("status").read_text().strip().split("\n\n")
    packages = {paragraph.splitlines()[0].removeprefix("Package: "): paragraph for paragraph in paragraphs}
    if not REPLACED_PACKAGES <= packages.keys():
        raise ValueError(f"Missing expected DHI packages: {sorted(REPLACED_PACKAGES - packages.keys())}")
    if (root / "opt/python").exists():
        raise ValueError("Replacement prefix /opt/python already exists")
    architecture = next(
        line.removeprefix("Architecture: ")
        for line in packages["python-3.14-minimal"].splitlines()
        if line.startswith("Architecture: ")
    )
    dependencies = sorted(
        {
            dependency
            for package in REPLACED_PACKAGES
            for line in packages[package].splitlines()
            if line.startswith("Depends: ")
            for dependency in line.removeprefix("Depends: ").split(", ")
            if dependency.split()[0] not in REPLACED_PACKAGES
        }
    )
    paths: set[Path] = set()
    metadata: list[Path] = []
    for package in sorted(REPLACED_PACKAGES):
        files = database / "info" / f"{package}.list"
        names = files.read_text().splitlines()
        # DHI's .list truncates a few filenames containing spaces; md5sums
        # retains the complete paths and is also part of package ownership.
        checksums_file = files.with_suffix(".md5sums")
        if checksums_file.exists():
            names.extend(line.split(maxsplit=1)[1] for line in checksums_file.read_text().splitlines())
        for name in names:
            path = root / name.lstrip("/")
            if path != root and not path.parent.resolve().is_relative_to(root):
                raise ValueError(f"Package path escapes runtime root: {name}")
            paths.add(path)
        metadata.extend(files.parent.glob(f"{package}.*"))
        metadata.append(database / "status.d" / package)
    for path in sorted(paths, key=lambda path: len(path.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir() and path != root:
            try:
                path.rmdir()
            except OSError as error:
                if error.errno != errno.ENOTEMPTY:
                    raise
    for path in metadata:
        path.unlink(missing_ok=True)
    shutil.copytree(payload / "opt/python", root / "opt/python", symlinks=True)
    for name in ("python", "python3", "python3.14"):
        (root / "usr/bin" / name).symlink_to("/opt/python/bin/python3.14")
    installed = sorted((root / "opt/python").rglob("*"))
    owned = [f"/{path.relative_to(root)}" for path in installed]
    owned += [f"/usr/bin/{name}" for name in ("python", "python3", "python3.14")]
    database.joinpath("info/conformance-python.list").write_text("\n".join(owned) + "\n")
    checksums = [
        f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(root)}"
        for path in installed
        if path.is_file() and not path.is_symlink()
    ]
    database.joinpath("info/conformance-python.md5sums").write_text("\n".join(checksums) + "\n")
    retained = [paragraph for name, paragraph in packages.items() if name not in REPLACED_PACKAGES]
    replacement = (
        "Package: conformance-python\n"
        "Status: install ok installed\n"
        f"Source: python-3.14 ({PYTHON_VERSION})\n"
        f"Version: {PYTHON_VERSION}-1+ob1\n"
        f"Architecture: {architecture}\n"
        f"Depends: {', '.join(dependencies)}\n"
        f"Provides: python3 (= {PYTHON_VERSION}), python3.14 (= {PYTHON_VERSION})\n"
        "Maintainer: Open Banking UK <standards@openbanking.org.uk>\n"
        "Homepage: https://www.python.org/\n"
        "Description: Upstream CPython built for the conformance runtime\n"
    )
    retained.append(replacement)
    database.joinpath("status").write_text("\n\n".join(retained) + "\n\n")
    database.joinpath("status.d").mkdir(exist_ok=True)
    database.joinpath("status.d/conformance-python").write_text(replacement)


if __name__ == "__main__":
    replace_python(Path(sys.argv[1]), Path(sys.argv[2]))
