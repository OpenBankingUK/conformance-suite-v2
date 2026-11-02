"""Merge only the temporary Expat APK records into the DHI runtime inventory."""

from pathlib import Path

PATCHED_VERSIONS = {
    "expat": "2.10.0-r0",
    "libexpat": "2.10.0-r0",
}


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
    """Replace only the pinned Expat records, rejecting package and architecture drift."""
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
        try:
            runtime_record = runtime_records[name]
            before_record = before_records[name]
            after_record = after_records[name]
        except KeyError as error:
            raise ValueError(f"{error.args[0]} is missing from an APK database") from error

        if _field(after_record, "V:", name) != f"V:{version}":
            raise ValueError(f"{name} does not have the pinned fixed version")
        architectures = {_field(record, "A:", name) for record in (runtime_record, before_record, after_record)}
        if len(architectures) != 1:
            raise ValueError(f"{name} architecture differs from the runtime")
        if _field(after_record, "P:", name) != f"P:{name}":
            raise ValueError(f"{name} APK record has an unexpected package name")
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
