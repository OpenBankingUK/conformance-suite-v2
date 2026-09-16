"""Adversarial preflight tests for suite-release artifact resolution."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from conformance.configuration_contracts import (
    ExecutionManifest,
    ExecutionManifestAssertion,
    Sha256Digest,
    StableId,
    SuiteRelease,
    SuiteReleaseArtifactError,
    SuiteReleaseArtifactErrorCode,
    execution_manifest_id,
    load_execution_manifest,
    load_suite_release,
    preflight_suite_release_artifacts,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1" / "execution-manifest.valid.json"
_RELEASE_PATH = (
    REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "open-banking-mvp" / "suite-release.json"
)


@pytest.mark.parametrize(
    ("source_id", "pointer"),
    [
        (
            StableId("ob-rw-v401.payment-initiation-openapi"),
            "#/components/schemas/OBWriteDomesticConsentResponse5",
        ),
        (
            StableId("ob-dcr-v34.openapi"),
            "#/components/schemas/RegistrationError",
        ),
    ],
)
def test_preflight_resolves_bound_json_and_yaml_schema_sources(
    source_id: StableId,
    pointer: str,
) -> None:
    release = load_suite_release(_RELEASE_PATH)
    manifest = _manifest_with_schema(release, source_id=source_id, pointer=pointer)

    resolver = preflight_suite_release_artifacts(manifest, release)

    key = (manifest.steps[0].id, manifest.steps[0].assertions[0].id)
    assert key in resolver.resolved_assertion_schemas
    assert resolver.resolved_artifacts[source_id].reference.kind == "technical-source"


def test_preflight_rejects_wrong_release_before_artifact_access() -> None:
    release = load_suite_release(_RELEASE_PATH)
    manifest = _manifest_with_schema(
        release,
        source_id=StableId("ob-dcr-v34.openapi"),
        pointer="#/components/schemas/RegistrationError",
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(
            manifest,
            replace(release, release_version="different"),
        )

    assert captured.value.code is SuiteReleaseArtifactErrorCode.RELEASE_MISMATCH


def test_preflight_rejects_altered_artifact_bytes() -> None:
    release = load_suite_release(_RELEASE_PATH)
    source_id = StableId("ob-dcr-v34.openapi")
    changed_release = _replace_artifact(
        release,
        source_id,
        digest=Sha256Digest(f"sha256:{'0' * 64}"),
    )
    manifest = _manifest_with_schema(
        changed_release,
        source_id=source_id,
        pointer="#/components/schemas/RegistrationError",
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(manifest, changed_release)

    assert captured.value.code is SuiteReleaseArtifactErrorCode.DIGEST_MISMATCH


def test_preflight_rejects_resolved_path_escape() -> None:
    release = load_suite_release(_RELEASE_PATH)
    source_id = StableId("ob-dcr-v34.openapi")
    escaped_path = REPO_ROOT / "pyproject.toml"
    changed_release = _replace_artifact(
        release,
        source_id,
        uri="../pyproject.toml",
        digest=_digest(escaped_path),
        media_type="application/json",
    )
    manifest = _manifest_with_schema(
        changed_release,
        source_id=source_id,
        pointer="#",
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(
            manifest,
            changed_release,
            trusted_root=REPO_ROOT / "conformance",
        )

    assert captured.value.code is SuiteReleaseArtifactErrorCode.PATH_OUTSIDE_ROOT


def test_preflight_rejects_unbound_or_absent_schema_source() -> None:
    release = load_suite_release(_RELEASE_PATH)
    unbound = _manifest_with_schema(
        release,
        source_id=StableId("unbound.openapi"),
        pointer="#/components/schemas/Missing",
    )
    absent = _manifest_with_schema(
        release,
        source_id=None,
        pointer="#/components/schemas/Missing",
    )

    with pytest.raises(SuiteReleaseArtifactError) as unbound_error:
        preflight_suite_release_artifacts(unbound, release)
    with pytest.raises(SuiteReleaseArtifactError) as absent_error:
        preflight_suite_release_artifacts(absent, release)

    assert unbound_error.value.code is SuiteReleaseArtifactErrorCode.ARTIFACT_UNBOUND
    assert absent_error.value.code is SuiteReleaseArtifactErrorCode.SCHEMA_SOURCE_REQUIRED


@pytest.mark.parametrize(
    ("pointer", "expected_code"),
    [
        ("#/components/~2schemas", SuiteReleaseArtifactErrorCode.POINTER_MALFORMED),
        ("#/components/schemas/DoesNotExist", SuiteReleaseArtifactErrorCode.POINTER_UNRESOLVED),
        ("https://example.com/schema", SuiteReleaseArtifactErrorCode.POINTER_MALFORMED),
    ],
)
def test_preflight_rejects_malformed_or_unresolved_pointer(
    pointer: str,
    expected_code: SuiteReleaseArtifactErrorCode,
) -> None:
    release = load_suite_release(_RELEASE_PATH)
    manifest = _manifest_with_schema(
        release,
        source_id=StableId("ob-dcr-v34.openapi"),
        pointer=pointer,
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(manifest, release)

    assert captured.value.code is expected_code


def test_preflight_rejects_malformed_document_for_declared_media_type() -> None:
    release = load_suite_release(_RELEASE_PATH)
    source_id = StableId("ob-dcr-v34.openapi")
    changed_release = _replace_artifact(
        release,
        source_id,
        uri="pyproject.toml",
        digest=_digest(REPO_ROOT / "pyproject.toml"),
        media_type="application/json",
    )
    manifest = _manifest_with_schema(
        changed_release,
        source_id=source_id,
        pointer="#",
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(manifest, changed_release)

    assert captured.value.code is SuiteReleaseArtifactErrorCode.DOCUMENT_MALFORMED


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        (
            {"media_type": "text/plain"},
            SuiteReleaseArtifactErrorCode.MEDIA_TYPE_UNSUPPORTED,
        ),
        (
            {"kind": StableId("json-schema")},
            SuiteReleaseArtifactErrorCode.ARTIFACT_KIND_MISMATCH,
        ),
    ],
)
def test_preflight_verifies_schema_artifact_media_type_and_kind(
    changes: dict[str, object],
    expected_code: SuiteReleaseArtifactErrorCode,
) -> None:
    release = load_suite_release(_RELEASE_PATH)
    source_id = StableId("ob-dcr-v34.openapi")
    changed_release = _replace_artifact(release, source_id, **changes)  # type: ignore[arg-type]  # parametrized typed replacements
    manifest = _manifest_with_schema(
        changed_release,
        source_id=source_id,
        pointer="#",
    )

    with pytest.raises(SuiteReleaseArtifactError) as captured:
        preflight_suite_release_artifacts(manifest, changed_release)

    assert captured.value.code is expected_code


def _manifest_with_schema(
    release: SuiteRelease,
    *,
    source_id: StableId | None,
    pointer: str,
) -> ExecutionManifest:
    manifest = load_execution_manifest(_MANIFEST_PATH)
    first_step = manifest.steps[0]
    schema_assertion = ExecutionManifestAssertion(
        id=first_step.assertions[0].id,
        type="response-schema",
        schema_ref=pointer,
        schema_source_id=source_id,
    )
    provenance = replace(
        manifest.provenance,
        suite_release_id=release.id,
        suite_release_version=release.release_version,
        suite_published_at=release.published_at,
        tool_releases=release.tool_releases,
        artifacts=release.artifacts,
    )
    provisional = replace(
        manifest,
        steps=(
            replace(
                first_step,
                assertions=(schema_assertion, *first_step.assertions[1:]),
            ),
            *manifest.steps[1:],
        ),
        provenance=provenance,
    )
    return replace(provisional, id=execution_manifest_id(provisional))


def _replace_artifact(
    release: SuiteRelease,
    artifact_id: StableId,
    *,
    digest: Sha256Digest | None = None,
    uri: str | None = None,
    media_type: str | None = None,
    kind: StableId | None = None,
) -> SuiteRelease:
    artifacts = tuple(
        replace(
            artifact,
            digest=artifact.digest if digest is None else digest,
            kind=artifact.kind if kind is None else kind,
            uri=artifact.uri if uri is None else uri,
            media_type=artifact.media_type if media_type is None else media_type,
        )
        if artifact.id == artifact_id
        else artifact
        for artifact in release.artifacts
    )
    return replace(release, artifacts=artifacts)


def _digest(path: Path) -> Sha256Digest:
    return Sha256Digest(f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}")
