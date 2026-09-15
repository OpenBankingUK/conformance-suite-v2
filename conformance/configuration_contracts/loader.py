"""Schema-authoritative loading for versioned configuration documents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import cast

from jsonschema import (  # type: ignore[import-untyped]  # runtime schema library lacks stubs
    Draft202012Validator,
    FormatChecker,
    SchemaError,
    ValidationError,
)
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from conformance.configuration_contracts.diagnostics import (
    ConfigurationContractError,
    ConfigurationDiagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
)
from conformance.configuration_contracts.models import (
    ArtifactReference,
    Sha256Digest,
    StableId,
    SuiteRelease,
    ToolRelease,
)
from conformance.json_types import JsonObject, JsonValue

SUITE_RELEASE_SCHEMA_VERSION = "1.0"
"""Suite-release document version currently supported by this foundation."""

_SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas" / "v1"
_COMMON_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/common.schema.json"
_SUITE_RELEASE_SCHEMA_ID = "https://schemas.openbanking.org.uk/conformance/v1/suite-release.schema.json"
_SCHEMA_PATHS = {
    _COMMON_SCHEMA_ID: _SCHEMA_ROOT / "common.schema.json",
    _SUITE_RELEASE_SCHEMA_ID: _SCHEMA_ROOT / "suite-release.schema.json",
}


def load_suite_release(path: Path) -> SuiteRelease:
    """Load a suite-release descriptor from JSON into immutable typed data.

    Raises:
        ConfigurationContractError: If the file cannot be read, decoded,
            schema-validated, or semantically validated.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.IO_READ_FAILED,
                    f"Unable to read configuration document: {error}",
                    instance_path="",
                ),
            )
        ) from error

    try:
        raw_document: object = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.JSON_INVALID,
                    f"Invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
                    instance_path="",
                ),
            )
        ) from error
    return parse_suite_release(raw_document)


def parse_suite_release(raw_document: object) -> SuiteRelease:
    """Validate decoded JSON and map it into an immutable suite release.

    Structural rules are owned exclusively by the external JSON Schema. This
    function only selects the schema version, invokes it, maps valid fields,
    and applies cross-item semantic checks that JSON Schema does not duplicate.

    Raises:
        ConfigurationContractError: If validation fails.
    """
    schema_version = _selected_suite_release_schema_version(raw_document)
    if schema_version is not None and schema_version != SUITE_RELEASE_SCHEMA_VERSION:
        raise ConfigurationContractError(
            (
                _diagnostic(
                    DiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
                    f"Unsupported suite-release schema version {schema_version!r}",
                    instance_path="/schemaVersion",
                ),
            )
        )

    validation_diagnostics = _validate_document(raw_document, schema_id=_SUITE_RELEASE_SCHEMA_ID)
    if validation_diagnostics:
        raise ConfigurationContractError(validation_diagnostics)

    document = _suite_release_from_schema_valid_document(cast(dict[str, object], raw_document))
    semantic_diagnostics = _validate_suite_release_semantics(document)
    if semantic_diagnostics:
        raise ConfigurationContractError(semantic_diagnostics)
    return document


def verify_suite_release_artifacts(
    suite_release: SuiteRelease,
    artifact_bytes: Mapping[tuple[str, str], bytes],
) -> tuple[ConfigurationDiagnostic, ...]:
    """Verify every suite artefact reference against exact supplied bytes.

    The mapping key is ``(kind, id)`` because stable IDs are unique within
    their declared object kind and suite-release namespace.
    """
    diagnostics: list[ConfigurationDiagnostic] = []
    for index, artifact in enumerate(suite_release.artifacts):
        artifact_key = (str(artifact.kind), str(artifact.id))
        content = artifact_bytes.get(artifact_key)
        if content is None:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_UNRESOLVED,
                    f"Referenced artefact {artifact.kind!s}/{artifact.id!s} was not supplied",
                    instance_path=f"/artifacts/{index}/uri",
                )
            )
            continue
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual_digest != artifact.digest:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
                    (
                        f"Artefact {artifact.kind!s}/{artifact.id!s} has digest "
                        f"{actual_digest}; expected {artifact.digest!s}"
                    ),
                    instance_path=f"/artifacts/{index}/digest",
                )
            )
    return tuple(diagnostics)


def suite_release_to_document(suite_release: SuiteRelease) -> JsonObject:
    """Convert an immutable suite release into its schema-owned wire shape."""
    return {
        "artifacts": [
            {
                "digest": str(artifact.digest),
                "id": str(artifact.id),
                "kind": str(artifact.kind),
                "mediaType": artifact.media_type,
                "schemaVersion": artifact.schema_version,
                "uri": artifact.uri,
            }
            for artifact in suite_release.artifacts
        ],
        "documentType": suite_release.document_type,
        "id": str(suite_release.id),
        "publishedAt": suite_release.published_at,
        "releaseVersion": suite_release.release_version,
        "schemaVersion": suite_release.schema_version,
        "toolReleases": [
            {"id": str(tool_release.id), "version": tool_release.version}
            for tool_release in suite_release.tool_releases
        ],
    }


def dump_suite_release(suite_release: SuiteRelease) -> str:
    """Serialize a suite release deterministically with a trailing newline."""
    return json.dumps(suite_release_to_document(suite_release), indent=2, sort_keys=True) + "\n"


def validate_bundled_schemas() -> tuple[ConfigurationDiagnostic, ...]:
    """Return diagnostics for malformed bundled schemas or broken references."""
    try:
        schemas, registry = _schema_catalog()
    except ConfigurationContractError as error:
        return error.diagnostics

    diagnostics: list[ConfigurationDiagnostic] = []
    for schema_id, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                    f"Schema {schema_id!r} is invalid: {error.message}",
                    instance_path="",
                    schema_path=_json_pointer(error.absolute_schema_path),
                )
            )
        diagnostics.extend(_validate_schema_references(schema_id, schema, registry))
    return tuple(diagnostics)


def _selected_suite_release_schema_version(raw_document: object) -> str | None:
    if not isinstance(raw_document, Mapping):
        return None
    value = raw_document.get("schemaVersion")
    return value if isinstance(value, str) else None


def _validate_document(raw_document: object, *, schema_id: str) -> tuple[ConfigurationDiagnostic, ...]:
    schemas, registry = _schema_catalog()
    schema = schemas[schema_id]
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    try:
        errors = sorted(
            _without_redundant_unevaluated_property_errors(tuple(validator.iter_errors(raw_document))),
            key=_validation_error_sort_key,
        )
    except Unresolvable as error:
        return (
            _diagnostic(
                DiagnosticCode.SCHEMA_REFERENCE_UNRESOLVED,
                f"Schema reference could not be resolved: {error}",
                instance_path="",
            ),
        )
    return tuple(diagnostic for error in errors for diagnostic in _validation_error_diagnostics(error))


def _without_redundant_unevaluated_property_errors(
    errors: tuple[ValidationError, ...],
) -> tuple[ValidationError, ...]:
    specific_error_paths = tuple(
        tuple(error.absolute_path) for error in errors if error.validator != "unevaluatedProperties"
    )
    return tuple(
        error
        for error in errors
        if error.validator != "unevaluatedProperties"
        or not any(
            len(specific_path) > len(error.absolute_path)
            and specific_path[: len(error.absolute_path)] == tuple(error.absolute_path)
            for specific_path in specific_error_paths
        )
    )


@cache
def _schema_catalog() -> tuple[dict[str, JsonObject], Registry]:
    schemas: dict[str, JsonObject] = {}
    resources: list[tuple[str, Resource[JsonValue]]] = []
    for expected_schema_id, schema_path in _SCHEMA_PATHS.items():
        try:
            decoded: object = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Unable to load bundled schema {schema_path.name!r}: {error}",
                        instance_path="",
                    ),
                )
            ) from error
        if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Bundled schema {schema_path.name!r} must be a JSON object",
                        instance_path="",
                    ),
                )
            )
        schema = cast(JsonObject, decoded)
        if schema.get("$id") != expected_schema_id:
            raise ConfigurationContractError(
                (
                    _diagnostic(
                        DiagnosticCode.SCHEMA_DEFINITION_INVALID,
                        f"Bundled schema {schema_path.name!r} has an unexpected $id",
                        instance_path="/$id",
                    ),
                )
            )
        schemas[expected_schema_id] = schema
        resources.append((expected_schema_id, Resource.from_contents(schema)))
    return schemas, Registry().with_resources(resources)


def _validate_schema_references(
    schema_id: str,
    schema: JsonObject,
    registry: Registry,
) -> tuple[ConfigurationDiagnostic, ...]:
    resolver = registry.resolver(schema_id)
    diagnostics: list[ConfigurationDiagnostic] = []
    for schema_path, reference in _schema_references(schema):
        try:
            resolver.lookup(reference)
        except Unresolvable as error:
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.SCHEMA_REFERENCE_UNRESOLVED,
                    f"Schema reference {reference!r} could not be resolved: {error}",
                    instance_path="",
                    schema_path=schema_path,
                )
            )
    return tuple(diagnostics)


def _schema_references(value: JsonValue, path: tuple[str | int, ...] = ()) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, member in value.items():
            member_path = (*path, key)
            if key == "$ref" and isinstance(member, str):
                yield _json_pointer(member_path), member
            else:
                yield from _schema_references(member, member_path)
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _schema_references(member, (*path, index))


def _suite_release_from_schema_valid_document(document: dict[str, object]) -> SuiteRelease:
    tool_releases = cast(list[dict[str, object]], document["toolReleases"])
    artifacts = cast(list[dict[str, object]], document["artifacts"])
    return SuiteRelease(
        schema_version=cast(str, document["schemaVersion"]),
        document_type=cast(str, document["documentType"]),
        id=StableId(cast(str, document["id"])),
        release_version=cast(str, document["releaseVersion"]),
        published_at=cast(str, document["publishedAt"]),
        tool_releases=tuple(
            ToolRelease(
                id=StableId(cast(str, tool_release["id"])),
                version=cast(str, tool_release["version"]),
            )
            for tool_release in tool_releases
        ),
        artifacts=tuple(
            ArtifactReference(
                id=StableId(cast(str, artifact["id"])),
                kind=StableId(cast(str, artifact["kind"])),
                media_type=cast(str, artifact["mediaType"]),
                schema_version=cast(str, artifact["schemaVersion"]),
                uri=cast(str, artifact["uri"]),
                digest=Sha256Digest(cast(str, artifact["digest"])),
            )
            for artifact in artifacts
        ),
    )


def _validate_suite_release_semantics(suite_release: SuiteRelease) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics: list[ConfigurationDiagnostic] = []
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (str(tool_release.id), f"/toolReleases/{index}/id")
                for index, tool_release in enumerate(suite_release.tool_releases)
            ),
            object_kind="tool-release",
        )
    )
    diagnostics.extend(
        _duplicate_id_diagnostics(
            (
                (f"{artifact.kind!s}\0{artifact.id!s}", f"/artifacts/{index}/id")
                for index, artifact in enumerate(suite_release.artifacts)
            ),
            object_kind="artefact kind",
        )
    )
    diagnostics.extend(
        _diagnostic(
            DiagnosticCode.SUITE_RELEASE_SELF_REFERENCE,
            "A suite-release descriptor cannot bind its own bytes",
            instance_path=f"/artifacts/{index}/id",
        )
        for index, artifact in enumerate(suite_release.artifacts)
        if artifact.kind == suite_release.document_type and artifact.id == suite_release.id
    )
    return tuple(diagnostics)


def _duplicate_id_diagnostics(
    identifiers: Iterable[tuple[str, str]],
    *,
    object_kind: str,
) -> tuple[ConfigurationDiagnostic, ...]:
    seen: set[str] = set()
    diagnostics: list[ConfigurationDiagnostic] = []
    for identifier, instance_path in identifiers:
        if identifier in seen:
            display_identifier = identifier.rsplit("\0", maxsplit=1)[-1]
            diagnostics.append(
                _diagnostic(
                    DiagnosticCode.DUPLICATE_ID,
                    f"Stable ID {display_identifier!r} is duplicated within {object_kind}",
                    instance_path=instance_path,
                )
            )
        else:
            seen.add(identifier)
    return tuple(diagnostics)


def _validation_error_diagnostics(error: ValidationError) -> tuple[ConfigurationDiagnostic, ...]:
    unexpected_properties = (
        re.findall(r"'([^']+)'", error.message)
        if error.validator in {"additionalProperties", "unevaluatedProperties"}
        else []
    )
    if unexpected_properties:
        return tuple(
            _diagnostic(
                DiagnosticCode.SCHEMA_VALIDATION_FAILED,
                f"Property {property_name!r} is not allowed",
                instance_path=_json_pointer((*error.absolute_path, property_name)),
                schema_path=_json_pointer(error.absolute_schema_path),
            )
            for property_name in unexpected_properties
        )
    return (
        _diagnostic(
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            error.message,
            instance_path=_json_pointer(error.absolute_path),
            schema_path=_json_pointer(error.absolute_schema_path),
        ),
    )


def _validation_error_sort_key(error: ValidationError) -> tuple[str, str, str]:
    return (
        _json_pointer(error.absolute_path),
        _json_pointer(error.absolute_schema_path),
        error.message,
    )


def _json_pointer(path: Iterable[object]) -> str:
    tokens = (str(token).replace("~", "~0").replace("/", "~1") for token in path)
    return "".join(f"/{token}" for token in tokens)


def _diagnostic(
    code: DiagnosticCode,
    message: str,
    *,
    instance_path: str,
    schema_path: str | None = None,
) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        message=message,
        instance_path=instance_path,
        schema_path=schema_path,
    )
