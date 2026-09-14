"""Tests for schema-authoritative immutable configuration loading."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DiagnosticCode,
    dump_suite_release,
    load_suite_release,
    parse_suite_release,
    validate_bundled_schemas,
    verify_suite_release_artifacts,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1"
_SCHEMA_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "schemas" / "v1"


def test_bundled_configuration_schemas_and_references_are_valid() -> None:
    assert validate_bundled_schemas() == ()


def test_valid_suite_release_loads_immutable_typed_data_and_round_trips() -> None:
    fixture_path = _FIXTURE_ROOT / "suite-release.valid.json"

    suite_release = load_suite_release(fixture_path)

    assert suite_release.schema_version == "1.0"
    assert suite_release.document_type == "suite-release"
    assert suite_release.id == "obl-suite-2026-09"
    assert suite_release.release_version == "2026.09"
    assert suite_release.tool_releases[0].id == "conformance-suite-v2"
    assert suite_release.artifacts[0].kind == "json-schema"
    assert isinstance(suite_release.artifacts, tuple)
    with pytest.raises(FrozenInstanceError):
        _set_attribute(suite_release, "release_version", "changed")
    with pytest.raises(FrozenInstanceError):
        _set_attribute(suite_release.artifacts[0], "uri", "changed")
    assert dump_suite_release(suite_release) == fixture_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("fixture_name", "expected_code", "expected_path"),
    [
        (
            "suite-release.invalid-unknown-property.json",
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            "/unexpected",
        ),
        (
            "suite-release.invalid-id.json",
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            "/artifacts/0/id",
        ),
        (
            "suite-release.invalid-schema-version.json",
            DiagnosticCode.SCHEMA_VERSION_UNSUPPORTED,
            "/schemaVersion",
        ),
        (
            "suite-release.invalid-duplicate-artifact-id.json",
            DiagnosticCode.DUPLICATE_ID,
            "/artifacts/1/id",
        ),
        (
            "suite-release.invalid-self-reference.json",
            DiagnosticCode.SUITE_RELEASE_SELF_REFERENCE,
            "/artifacts/0/id",
        ),
        (
            "suite-release.invalid-artifact-uri.json",
            DiagnosticCode.SCHEMA_VALIDATION_FAILED,
            "/artifacts/0/uri",
        ),
    ],
)
def test_invalid_suite_release_fixtures_have_stable_diagnostics(
    fixture_name: str,
    expected_code: DiagnosticCode,
    expected_path: str,
) -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_suite_release(_FIXTURE_ROOT / fixture_name)

    assert captured.value.diagnostics[0].code is expected_code
    assert captured.value.diagnostics[0].instance_path == expected_path


def test_suite_release_artifact_references_verify_exact_versioned_bytes() -> None:
    suite_release = load_suite_release(_FIXTURE_ROOT / "suite-release.valid.json")
    artifact_bytes = {
        ("json-schema", "configuration-common-v1"): (_SCHEMA_ROOT / "common.schema.json").read_bytes(),
        ("json-schema", "suite-release-v1"): (_SCHEMA_ROOT / "suite-release.schema.json").read_bytes(),
    }

    assert verify_suite_release_artifacts(suite_release, artifact_bytes) == ()


def test_stable_artifact_ids_are_scoped_by_kind() -> None:
    raw_document: object = json.loads((_FIXTURE_ROOT / "suite-release.valid.json").read_text(encoding="utf-8"))
    assert isinstance(raw_document, dict)
    artifacts = raw_document["artifacts"]
    assert isinstance(artifacts, list)
    second_artifact = artifacts[1]
    assert isinstance(second_artifact, dict)
    second_artifact["id"] = "configuration-common-v1"
    second_artifact["kind"] = "requirements-catalogue"

    suite_release = parse_suite_release(raw_document)

    assert suite_release.artifacts[0].id == suite_release.artifacts[1].id
    assert suite_release.artifacts[0].kind != suite_release.artifacts[1].kind


def test_broken_artifact_reference_has_stable_instance_path() -> None:
    suite_release = load_suite_release(_FIXTURE_ROOT / "suite-release.invalid-broken-reference.json")

    diagnostics = verify_suite_release_artifacts(suite_release, {})

    assert len(diagnostics) == 1
    assert diagnostics[0].code is DiagnosticCode.ARTIFACT_UNRESOLVED
    assert diagnostics[0].instance_path == "/artifacts/0/uri"


def test_artifact_digest_mismatch_has_stable_instance_path() -> None:
    suite_release = load_suite_release(_FIXTURE_ROOT / "suite-release.valid.json")
    first_artifact = suite_release.artifacts[0]

    diagnostics = verify_suite_release_artifacts(
        suite_release,
        {(str(first_artifact.kind), str(first_artifact.id)): b"not the referenced schema"},
    )

    assert [diagnostic.code for diagnostic in diagnostics] == [
        DiagnosticCode.ARTIFACT_DIGEST_MISMATCH,
        DiagnosticCode.ARTIFACT_UNRESOLVED,
    ]
    assert [diagnostic.instance_path for diagnostic in diagnostics] == [
        "/artifacts/0/digest",
        "/artifacts/1/uri",
    ]


def test_invalid_json_has_structured_diagnostic(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"schemaVersion":', encoding="utf-8")

    with pytest.raises(ConfigurationContractError) as captured:
        load_suite_release(invalid_path)

    assert captured.value.diagnostics[0].code is DiagnosticCode.JSON_INVALID
    assert captured.value.diagnostics[0].instance_path == ""


def test_missing_file_has_structured_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationContractError) as captured:
        load_suite_release(tmp_path / "missing.json")

    assert captured.value.diagnostics[0].code is DiagnosticCode.IO_READ_FAILED
    assert captured.value.diagnostics[0].instance_path == ""


def test_valid_fixture_is_json_object() -> None:
    decoded: object = json.loads((_FIXTURE_ROOT / "suite-release.valid.json").read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)


def _set_attribute(instance: object, name: str, value: object) -> None:
    setattr(instance, name, value)
