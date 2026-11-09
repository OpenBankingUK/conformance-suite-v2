"""Merge only the temporary Expat and zlib APK records into the DHI runtime inventory."""

from pathlib import Path

PATCHED_VERSIONS = {
    "expat": "2.10.0-r0",
    "libexpat": "2.10.0-r0",
    "zlib": "1.4-r0",
    "zlib-dev": "1.4-r0",
}
RUNTIME_PACKAGES = {"expat", "libexpat", "zlib"}
STAGING_PACKAGES = {"zlib-dev"}


def _records(database: str) -> dict[str, str]:
    records: dict[str, str] = {}
    for record in database.strip().split("\n\n"):
        names = [line[2:] for line in record.splitlines() if line.startswith("P:")]
        if len(names) != 1 or names[0] in records:
            raise ValueError("APK database has missing or duplicate package names")
        records[names[0]] = record
    return records


def _field(record: str, prefix: str, package: str) -> str:
    fields = [line for line in record.splitlines() if line.startswith(prefix)]
    if len(fields) != 1:
        raise ValueError(f"{package} has missing or duplicate {prefix} fields")
    return fields[0]


def merge_patched_records(runtime: str, before: str, after: str) -> str:
    """Replace pinned runtime records, rejecting package and architecture drift."""
    runtime_records = _records(runtime)
    before_records = _records(before)
    after_records = _records(after)
    changed = {
        name
        for name in before_records.keys() | after_records.keys()
        if before_records.get(name) != after_records.get(name)
    }
    if STAGING_PACKAGES & runtime_records.keys():
        raise ValueError(
            f"Staging-only packages appear in the runtime APK database: {sorted(STAGING_PACKAGES & runtime_records.keys())}"
        )
    expected_changed = RUNTIME_PACKAGES | (changed & STAGING_PACKAGES)
    if changed != expected_changed:
        raise ValueError(f"Expected only {sorted(expected_changed)} upgrades; apk changed {sorted(changed)}")

    for name, version in PATCHED_VERSIONS.items():
        try:
            after_record = after_records[name]
        except KeyError as error:
            raise ValueError(f"{error.args[0]} is missing from an APK database") from error

        if _field(after_record, "V:", name) != f"V:{version}":
            raise ValueError(f"{name} does not have the pinned fixed version")
        if _field(after_record, "P:", name) != f"P:{name}":
            raise ValueError(f"{name} APK record has an unexpected package name")

        before_record = before_records.get(name)
        if name in RUNTIME_PACKAGES:
            try:
                runtime_record = runtime_records[name]
                before_record = before_records[name]
            except KeyError as error:
                raise ValueError(f"{error.args[0]} is missing from an APK database") from error
            records: tuple[str, ...] = (runtime_record, before_record, after_record)
        else:
            records = tuple(record for record in (before_record, after_record) if record is not None)
            if not records:
                raise ValueError(f"{name} is missing from an APK database")

        architectures = {_field(record, "A:", name) for record in records}
        if name == "zlib-dev":
            architectures.add(_field(after_records["zlib"], "A:", "zlib"))
        if len(architectures) != 1:
            raise ValueError(f"{name} architecture differs from the runtime")
        if name in RUNTIME_PACKAGES:
            runtime_records[name] = after_record

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
