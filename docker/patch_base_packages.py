"""Merge signature-verified base-package APK records without dev-only packages.

Temporary Security-approved mitigation for CVE-2026-102633/CVE-2026-77214
(Expat) and CVE-2026-85091 (zlib). The runtime APK database, not the dev
image's inventory, is authoritative.
"""

from pathlib import Path

PATCHED_VERSIONS = {
    "expat": "2.9.0-r0",
    "libexpat": "2.9.0-r0",
    "zlib": "1.3.2-r1",
}


def _records(database: str) -> dict[str, str]:
    records: dict[str, str] = {}
    for record in database.strip().split("\n\n"):
        names = [line[2:] for line in record.splitlines() if line.startswith("P:")]
        if len(names) != 1 or names[0] in records:
            raise ValueError("APK database has missing or duplicate package names")
        records[names[0]] = record
    return records


def merge_patched_records(runtime: str, before: str, after: str) -> str:
    """Replace only allowlisted records, rejecting dependency or architecture drift."""
    runtime_records = _records(runtime)
    before_records = _records(before)
    after_records = _records(after)
    changed = {
        name
        for name in before_records.keys() | after_records.keys()
        if before_records.get(name) != after_records.get(name)
    }
    if changed != PATCHED_VERSIONS.keys():
        raise ValueError(f"Expected only {sorted(PATCHED_VERSIONS)} upgrades; apk changed {sorted(changed)}")
    for name, version in PATCHED_VERSIONS.items():
        old = runtime_records[name]
        new = after_records[name]
        if f"V:{version}" not in new.splitlines():
            raise ValueError(f"{name} does not have the pinned fixed version")
        old_arch = [line for line in old.splitlines() if line.startswith("A:")]
        new_arch = [line for line in new.splitlines() if line.startswith("A:")]
        if len(old_arch) != 1 or old_arch != new_arch:
            raise ValueError(f"{name} architecture differs from the runtime")
        runtime_records[name] = new
    return "\n\n".join(runtime_records.values()) + "\n\n"


if __name__ == "__main__":
    runtime_db = Path("/patch/lib/apk/db/installed")
    runtime_db.write_text(
        merge_patched_records(
            runtime_db.read_text(),
            Path("/before-installed").read_text(),
            Path("/lib/apk/db/installed").read_text(),
        )
    )
