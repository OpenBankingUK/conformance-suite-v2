from __future__ import annotations

from typing import cast

import pytest

import conformance.suite_catalog as suite_catalog
from conformance.manifest import ManifestStep, PsuAuthorizationStep
from conformance.model_bank_config import SuiteName, SuiteSelection, SuiteSpecVersion
from conformance.suite_catalog import SuiteCatalogError, resolve_suite


def _selection(spec_version: SuiteSpecVersion = "v4.0", suite_name: str = "discovery-jwks") -> SuiteSelection:
    return SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite=cast(SuiteName, suite_name),
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_resolve_suite_returns_bundled_manifest_for_supported_versions(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.suite == "discovery-jwks"
    assert resolved.metadata.catalog_id == f"ob-read-write/{spec_version}/fapi1-advanced/discovery-jwks"
    assert "smoke" in resolved.metadata.label
    assert "not full Read/Write API certification coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == ["openid-discovery", "jwks-fetch"]
    assert [step.mandatory for step in manifest.steps] == [True, True]

    discovery_step = cast(ManifestStep, manifest.steps[0])
    jwks_step = cast(ManifestStep, manifest.steps[1])
    assert discovery_step.request.url == "${config.discoveryUrl}"
    assert jwks_step.request.url == "${steps.openid-discovery.response.body.jwks_uri}"


@pytest.mark.unit
def test_list_supported_suites_is_deterministic() -> None:
    first = suite_catalog.list_supported_suites()
    second = suite_catalog.list_supported_suites()

    assert first == second
    assert [metadata.catalog_id for metadata in first] == sorted(metadata.catalog_id for metadata in first)
    assert [metadata.spec_version for metadata in first] == ["v3.1.11", "v3.1.11", "v4.0", "v4.0"]
    assert [metadata.suite for metadata in first] == [
        "discovery-jwks",
        "psu-auth-starter",
        "discovery-jwks",
        "psu-auth-starter",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_resolve_psu_auth_starter_returns_bundled_manifest_for_supported_versions(
    spec_version: SuiteSpecVersion,
) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.metadata.standard == "ob-read-write"
    assert resolved.metadata.spec_version == spec_version
    assert resolved.metadata.profile == "fapi1-advanced"
    assert resolved.metadata.suite == "psu-auth-starter"
    assert resolved.metadata.catalog_id == f"ob-read-write/{spec_version}/fapi1-advanced/psu-auth-starter"
    assert "PSU auth starter" in resolved.metadata.label
    assert "not full Read/Write API certification coverage" in resolved.metadata.description

    manifest = resolved.manifest
    assert manifest.schema_version == "v1"
    assert manifest.certification_coverage == "partial"
    assert manifest.name == resolved.metadata.label
    assert [step.id for step in manifest.steps] == ["openid-discovery", "jwks-fetch", "psu-authorization"]
    assert [step.mandatory for step in manifest.steps] == [True, True, True]

    discovery_step = cast(ManifestStep, manifest.steps[0])
    jwks_step = cast(ManifestStep, manifest.steps[1])
    psu_step = cast(PsuAuthorizationStep, manifest.steps[2])

    assert discovery_step.request.url == "${config.discoveryUrl}"
    assert jwks_step.request.url == "${steps.openid-discovery.response.body.jwks_uri}"
    assert psu_step.mode == "manual"
    assert psu_step.authorization_endpoint == "${steps.openid-discovery.response.body.authorization_endpoint}"
    assert psu_step.client_id == "${config.oauth.clientId}"
    assert psu_step.redirect_uri == "https://conformance.example.com/callback"
    assert psu_step.scope == "openid accounts"


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_psu_auth_starter_manifest_is_partial_coverage(spec_version: SuiteSpecVersion) -> None:
    resolved = resolve_suite(_selection(spec_version, suite_name="psu-auth-starter"))

    assert resolved.manifest.certification_coverage == "partial"


@pytest.mark.unit
def test_resolve_suite_rejects_unsupported_catalog_key() -> None:
    unsupported = SuiteSelection(
        standard="ob-read-write",
        spec_version=cast("SuiteSpecVersion", "v9.9"),
        profile="fapi1-advanced",
        suite="discovery-jwks",
    )

    with pytest.raises(SuiteCatalogError, match="Unsupported suite selection: .*specVersion=v9.9"):
        resolve_suite(unsupported)


@pytest.mark.unit
def test_resolve_suite_reports_missing_bundled_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    key: suite_catalog.SuiteCatalogKey = ("ob-read-write", "v4.0", "fapi1-advanced", "discovery-jwks")
    missing_entry = suite_catalog._CatalogEntry(
        key=key,
        resource_name="missing-discovery-jwks.json",
        label="Missing discovery suite",
        description="Missing resource test entry",
    )
    monkeypatch.setattr(suite_catalog, "_CATALOG_BY_KEY", {key: missing_entry})

    with pytest.raises(
        SuiteCatalogError, match="Bundled suite manifest resource not found: missing-discovery-jwks.json"
    ):
        resolve_suite(_selection())


@pytest.mark.unit
def test_resolve_suite_reports_invalid_bundled_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_resource_text(resource_name: str) -> str:
        return '{"schemaVersion": "v1",'

    monkeypatch.setattr(suite_catalog, "_read_resource_text", fake_read_resource_text)

    with pytest.raises(SuiteCatalogError, match="Invalid JSON in bundled suite manifest"):
        resolve_suite(_selection())


@pytest.mark.unit
def test_resolve_suite_reports_invalid_bundled_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_resource_text(resource_name: str) -> str:
        return '{"schemaVersion": "v1", "name": "Broken", "steps": []}'

    monkeypatch.setattr(suite_catalog, "_read_resource_text", fake_read_resource_text)

    with pytest.raises(SuiteCatalogError, match="Invalid bundled suite manifest .*steps must be a non-empty array"):
        resolve_suite(_selection())
