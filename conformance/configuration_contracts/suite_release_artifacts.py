"""Fail-closed local resolution of suite-release-bound execution artefacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

import yaml

from conformance.configuration_contracts.loader import execution_manifest_id as _v1_execution_manifest_id
from conformance.configuration_contracts.models import (
    ArtifactReference,
    ExecutionManifest,
    Sha256Digest,
    StableId,
    SuiteRelease,
)
from conformance.json_types import JsonValue

if TYPE_CHECKING:
    from conformance.configuration_contracts.v2_models import ExecutionManifest as V2ExecutionManifest

type SupportedExecutionManifest = ExecutionManifest | V2ExecutionManifest

TRUSTED_CONFIGURATION_ROOT = Path(__file__).resolve().parents[2]
"""Repository root against which suite-release artifact URIs are interpreted."""

_JSON_MEDIA_TYPES = frozenset({"application/json", "application/schema+json"})
_YAML_MEDIA_TYPES = frozenset({"application/yaml"})
_TECHNICAL_SOURCE_KIND = StableId("technical-source")


class SuiteReleaseArtifactErrorCode(StrEnum):
    """Stable machine-readable suite artifact preflight failures."""

    RELEASE_MISMATCH = "suite-artifact.release-mismatch"
    MANIFEST_ID_MISMATCH = "suite-artifact.manifest-id-mismatch"
    ARTIFACT_IDENTITY_MISMATCH = "suite-artifact.identity-mismatch"
    ARTIFACT_UNBOUND = "suite-artifact.unbound"
    ARTIFACT_KIND_MISMATCH = "suite-artifact.kind-mismatch"
    MEDIA_TYPE_UNSUPPORTED = "suite-artifact.media-type-unsupported"
    PATH_OUTSIDE_ROOT = "suite-artifact.path-outside-root"
    ARTIFACT_READ_FAILED = "suite-artifact.read-failed"
    DIGEST_MISMATCH = "suite-artifact.digest-mismatch"
    DOCUMENT_MALFORMED = "suite-artifact.document-malformed"
    SCHEMA_SOURCE_REQUIRED = "suite-artifact.schema-source-required"
    POINTER_MALFORMED = "suite-artifact.pointer-malformed"
    POINTER_UNRESOLVED = "suite-artifact.pointer-unresolved"


class SuiteReleaseArtifactError(ValueError):
    """Stable domain error raised before execution can perform network I/O."""

    def __init__(
        self,
        code: SuiteReleaseArtifactErrorCode,
        message: str,
        *,
        artifact_id: StableId | None = None,
        assertion_id: StableId | None = None,
    ) -> None:
        """Create a structured artifact-resolution failure."""
        self.code = code
        self.artifact_id = artifact_id
        self.assertion_id = assertion_id
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class ResolvedSuiteArtifact:
    """Digest-verified local suite artifact and its immutable parsed document."""

    reference: ArtifactReference
    path: Path
    content: bytes
    document: JsonValue


@dataclass(frozen=True, slots=True)
class SuiteReleaseArtifactResolver:
    """Immutable manifest-bound resolver populated by complete local preflight."""

    manifest: SupportedExecutionManifest
    suite_release: SuiteRelease
    trusted_root: Path = TRUSTED_CONFIGURATION_ROOT
    resolved_artifacts: Mapping[StableId, ResolvedSuiteArtifact] = field(init=False)
    resolved_assertion_schemas: Mapping[tuple[StableId, StableId], JsonValue] = field(init=False)

    def __post_init__(self) -> None:
        """Bind identity and resolve every released byte before network I/O."""
        _verify_release_binding(self.manifest, self.suite_release)
        root = _resolve_root(self.trusted_root)
        artifacts: dict[StableId, ResolvedSuiteArtifact] = {}
        schemas: dict[tuple[StableId, StableId], JsonValue] = {}
        if self.suite_release.schema_version == "2.0":
            for reference in self.suite_release.artifacts:
                artifacts[reference.id] = _resolve_artifact(reference, root=root)
        for step in self.manifest.steps:
            for assertion in step.assertions:
                if assertion.type != "response-schema":
                    continue
                if assertion.schema_source_id is None:
                    raise SuiteReleaseArtifactError(
                        SuiteReleaseArtifactErrorCode.SCHEMA_SOURCE_REQUIRED,
                        f"Response-schema assertion {assertion.id!s} has no schemaSourceId",
                        assertion_id=assertion.id,
                    )
                artifact = artifacts.get(assertion.schema_source_id)
                if artifact is None:
                    reference = _bound_artifact(
                        self.suite_release,
                        assertion.schema_source_id,
                        expected_kind=_TECHNICAL_SOURCE_KIND,
                    )
                    artifact = _resolve_artifact(reference, root=root)
                    artifacts[assertion.schema_source_id] = artifact
                if assertion.schema_ref is None:
                    raise SuiteReleaseArtifactError(
                        SuiteReleaseArtifactErrorCode.POINTER_MALFORMED,
                        f"Response-schema assertion {assertion.id!s} has no schemaRef",
                        artifact_id=assertion.schema_source_id,
                        assertion_id=assertion.id,
                    )
                schemas[(step.id, assertion.id)] = _resolve_json_pointer(
                    artifact.document,
                    assertion.schema_ref,
                    artifact_id=assertion.schema_source_id,
                    assertion_id=assertion.id,
                )
        object.__setattr__(self, "trusted_root", root)
        object.__setattr__(self, "resolved_artifacts", MappingProxyType(artifacts))
        object.__setattr__(
            self,
            "resolved_assertion_schemas",
            MappingProxyType(schemas),
        )

    def resolve_schema(
        self,
        step_id: StableId,
        assertion_id: StableId,
    ) -> JsonValue:
        """Return one schema proven during preflight."""
        try:
            return self.resolved_assertion_schemas[(step_id, assertion_id)]
        except KeyError as error:
            raise SuiteReleaseArtifactError(
                SuiteReleaseArtifactErrorCode.POINTER_UNRESOLVED,
                f"No resolved schema for assertion {step_id!s}/{assertion_id!s}",
                assertion_id=assertion_id,
            ) from error

    def resolve_artifact(
        self,
        artifact_id: StableId,
        *,
        expected_kind: StableId,
    ) -> ResolvedSuiteArtifact:
        """Resolve one release-bound artifact with an explicit expected kind."""
        cached = self.resolved_artifacts.get(artifact_id)
        if cached is not None:
            if cached.reference.kind != expected_kind:
                raise SuiteReleaseArtifactError(
                    SuiteReleaseArtifactErrorCode.ARTIFACT_KIND_MISMATCH,
                    f"Artifact {artifact_id!s} is not a {expected_kind!s}",
                    artifact_id=artifact_id,
                )
            return cached
        reference = _bound_artifact(
            self.suite_release,
            artifact_id,
            expected_kind=expected_kind,
        )
        return _resolve_artifact(reference, root=self.trusted_root)

    def resolve_pointer(
        self,
        artifact_id: StableId,
        pointer: str,
        *,
        expected_kind: StableId,
    ) -> JsonValue:
        """Resolve a local JSON Pointer within one verified bound artifact."""
        artifact = self.resolve_artifact(
            artifact_id,
            expected_kind=expected_kind,
        )
        return _resolve_json_pointer(
            artifact.document,
            pointer,
            artifact_id=artifact_id,
            assertion_id=StableId("artifact-resolution"),
        )


def preflight_suite_release_artifacts(
    manifest: SupportedExecutionManifest,
    accepted_release: SuiteRelease,
    *,
    trusted_root: Path = TRUSTED_CONFIGURATION_ROOT,
) -> SuiteReleaseArtifactResolver:
    """Create a fully preflighted resolver for an accepted suite release."""
    return SuiteReleaseArtifactResolver(
        manifest=manifest,
        suite_release=accepted_release,
        trusted_root=trusted_root,
    )


def _verify_release_binding(manifest: SupportedExecutionManifest, release: SuiteRelease) -> None:
    if manifest.id != _execution_manifest_id(manifest):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.MANIFEST_ID_MISMATCH,
            "Execution manifest ID does not match its immutable content",
        )
    if (
        manifest.provenance.suite_release_id != release.id
        or manifest.provenance.suite_release_version != release.release_version
    ):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.RELEASE_MISMATCH,
            "Execution manifest suite release ID/version does not match the accepted release",
        )
    if manifest.provenance.artifacts != release.artifacts:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_IDENTITY_MISMATCH,
            "Execution manifest artifact identities do not match the accepted release",
        )


def _execution_manifest_id(manifest: SupportedExecutionManifest) -> StableId:
    """Dispatch content addressing without allowing v1 code into the active path."""
    if manifest.schema_version == "2.0":
        from conformance.configuration_contracts.v2_loader import execution_manifest_id

        return execution_manifest_id(cast("V2ExecutionManifest", manifest))
    return _v1_execution_manifest_id(cast(ExecutionManifest, manifest))


def _resolve_root(root: Path) -> Path:
    try:
        return root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_READ_FAILED,
            f"Trusted configuration root cannot be resolved: {root}",
        ) from error


def _bound_artifact(
    release: SuiteRelease,
    artifact_id: StableId,
    *,
    expected_kind: StableId,
) -> ArtifactReference:
    candidates = tuple(artifact for artifact in release.artifacts if artifact.id == artifact_id)
    if not candidates:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_UNBOUND,
            f"Artifact {artifact_id!s} is not bound by the accepted release",
            artifact_id=artifact_id,
        )
    for artifact in candidates:
        if artifact.kind == expected_kind:
            return artifact
    raise SuiteReleaseArtifactError(
        SuiteReleaseArtifactErrorCode.ARTIFACT_KIND_MISMATCH,
        f"Artifact {artifact_id!s} is not a {expected_kind!s}",
        artifact_id=artifact_id,
    )


def _resolve_artifact(reference: ArtifactReference, *, root: Path) -> ResolvedSuiteArtifact:
    if reference.media_type not in _JSON_MEDIA_TYPES | _YAML_MEDIA_TYPES:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.MEDIA_TYPE_UNSUPPORTED,
            f"Artifact {reference.id!s} has unsupported media type {reference.media_type!r}",
            artifact_id=reference.id,
        )
    try:
        path = (root / reference.uri).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_READ_FAILED,
            f"Artifact {reference.id!s} cannot be resolved",
            artifact_id=reference.id,
        ) from error
    if not path.is_relative_to(root):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.PATH_OUTSIDE_ROOT,
            f"Artifact {reference.id!s} resolves outside the trusted configuration root",
            artifact_id=reference.id,
        )
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_READ_FAILED,
            f"Artifact {reference.id!s} cannot be resolved",
            artifact_id=reference.id,
        ) from error
    try:
        content = path.read_bytes()
    except OSError as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.ARTIFACT_READ_FAILED,
            f"Artifact {reference.id!s} cannot be read",
            artifact_id=reference.id,
        ) from error
    actual_digest = Sha256Digest(f"sha256:{hashlib.sha256(content).hexdigest()}")
    if actual_digest != reference.digest:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.DIGEST_MISMATCH,
            f"Artifact {reference.id!s} digest does not match its release binding",
            artifact_id=reference.id,
        )
    document = _parse_document(
        content,
        media_type=reference.media_type,
        artifact_id=reference.id,
    )
    return ResolvedSuiteArtifact(
        reference=reference,
        path=path,
        content=content,
        document=document,
    )


def _parse_document(
    content: bytes,
    *,
    media_type: str,
    artifact_id: StableId,
) -> JsonValue:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
            f"Artifact {artifact_id!s} is not UTF-8",
            artifact_id=artifact_id,
        ) from error
    try:
        parsed: object = json.loads(text) if media_type in _JSON_MEDIA_TYPES else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
            f"Artifact {artifact_id!s} is malformed for media type {media_type!r}",
            artifact_id=artifact_id,
        ) from error
    return _freeze_json_document(parsed, artifact_id=artifact_id, ancestors=set())


def _freeze_json_document(
    value: object,
    *,
    artifact_id: StableId,
    ancestors: set[int],
) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
            f"Artifact {artifact_id!s} contains a non-finite number",
            artifact_id=artifact_id,
        )
    if value is None or isinstance(value, str | int | float | bool):
        return cast(JsonValue, value)
    if isinstance(value, dict | list):
        identity = id(value)
        if identity in ancestors:
            raise SuiteReleaseArtifactError(
                SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
                f"Artifact {artifact_id!s} contains a recursive YAML value",
                artifact_id=artifact_id,
            )
        ancestors.add(identity)
        try:
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise SuiteReleaseArtifactError(
                        SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
                        f"Artifact {artifact_id!s} contains a non-string object key",
                        artifact_id=artifact_id,
                    )
                frozen = {
                    cast(str, key): _freeze_json_document(
                        item,
                        artifact_id=artifact_id,
                        ancestors=ancestors,
                    )
                    for key, item in value.items()
                }
                return MappingProxyType(frozen)  # type: ignore[return-value]  # immutable parsed JSON
            return tuple(
                _freeze_json_document(
                    item,
                    artifact_id=artifact_id,
                    ancestors=ancestors,
                )
                for item in value
            )  # type: ignore[return-value]  # immutable parsed JSON
        finally:
            ancestors.remove(identity)
    raise SuiteReleaseArtifactError(
        SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED,
        f"Artifact {artifact_id!s} contains a non-JSON value",
        artifact_id=artifact_id,
    )


def _resolve_json_pointer(
    document: JsonValue,
    reference: str,
    *,
    artifact_id: StableId,
    assertion_id: StableId,
) -> JsonValue:
    if not reference.startswith("#"):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.POINTER_MALFORMED,
            f"Schema reference {reference!r} is not a local JSON Pointer fragment",
            artifact_id=artifact_id,
            assertion_id=assertion_id,
        )
    pointer = unquote(reference[1:])
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise SuiteReleaseArtifactError(
            SuiteReleaseArtifactErrorCode.POINTER_MALFORMED,
            f"Schema reference {reference!r} is not a JSON Pointer",
            artifact_id=artifact_id,
            assertion_id=assertion_id,
        )
    current = document
    for raw_token in pointer[1:].split("/"):
        if _has_malformed_escape(raw_token):
            raise SuiteReleaseArtifactError(
                SuiteReleaseArtifactErrorCode.POINTER_MALFORMED,
                f"Schema reference {reference!r} contains an invalid escape",
                artifact_id=artifact_id,
                assertion_id=assertion_id,
            )
        token = raw_token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, Mapping):
                current = current[token]
            elif isinstance(current, tuple | list):
                if not token.isdigit() or (
                    token.startswith("0") and token != "0"  # noqa: S105 - JSON Pointer array index
                ):
                    raise KeyError(token)
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError) as error:
            raise SuiteReleaseArtifactError(
                SuiteReleaseArtifactErrorCode.POINTER_UNRESOLVED,
                f"Schema reference {reference!r} does not resolve in artifact {artifact_id!s}",
                artifact_id=artifact_id,
                assertion_id=assertion_id,
            ) from error
    return current


def _has_malformed_escape(token: str) -> bool:
    index = 0
    while index < len(token):
        if token[index] == "~":
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                return True
            index += 2
        else:
            index += 1
    return False
