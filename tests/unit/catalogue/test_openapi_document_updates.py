"""Tests for selectable Open Banking Read/Write OpenAPI document updates."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import cast

import pytest

from conformance import schema_validation
from conformance.catalogue import (
    CatalogueError,
    PlanDocumentV2,
    compile_test_plan_document,
    parse_test_plan_document,
    plan_document_to_json_object,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.catalogues.ais import AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE
from conformance.catalogues.read_write_v4_0_1 import (
    AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    CVRP_V401_LEGACY_FCS_CATALOGUE,
)
from conformance.catalogues.vrp import CVRP_LEGACY_FCS_CATALOGUE
from conformance.json_types import JsonObject
from conformance.openapi_documents import (
    OPENAPI_DOCUMENTS_ROOT,
    READ_WRITE_OPENAPI_DOCUMENT_NAMES,
    bind_read_write_document,
    bundled_openapi_document_paths,
    bundled_read_write_document,
    is_logical_read_write_document,
    logical_read_write_document,
)
from conformance.results import openapi_document_update_to_json
from conformance.specification_registry import (
    OpenApiDocumentUpdate,
    all_openapi_document_updates,
    openapi_document_update_by_catalogue_id,
    specification_version_for_catalogue,
)

pytestmark = pytest.mark.unit

pytestmark = pytest.mark.unit

_EXPECTED_UPDATES = {
    "3.1.11": ("Baseline", "Release-2", "Release-3", "Release-4", "Release-5"),
    "4.0.0": ("Baseline", "Release-2", "Update-3", "Update-4", "Update-5"),
    "4.0.1": ("Baseline", "Update-1"),
}


def _canonical_plan(*, version: str = "4.0.1", update: str | None = "Update-1") -> JsonObject:
    specification: JsonObject = {"family": "OBL_READ_WRITE", "version": version, "profile": "FAPI1_ADVANCED"}
    if update is not None:
        specification["openApiDocumentUpdate"] = update
    return {
        "schemaVersion": "1.0",
        "specification": specification,
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://rs.example.com",
        },
        "resourceGroups": [
            {
                "id": "AIS",
                "label": "Accounts",
                "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
            }
        ],
        "businessTestData": {"inputs": {"consentedAccountId": {"value": "account-123"}}},
        "metadata": {},
    }


def _schema_documents(document: PlanDocumentV2) -> set[str]:
    compiled = compile_test_plan_document(document, supported_catalogues())
    documents: set[str] = set()
    for test_case in compiled.test_cases:
        for assertion in test_case.assertions:
            for key in ("document", "schemaDocument"):
                value = assertion.rule.get(key)
                if isinstance(value, str):
                    documents.add(value)
    return documents


def test_registry_publishes_historical_update_terminology() -> None:
    updates: dict[str, list[str]] = {}
    for update in all_openapi_document_updates():
        updates.setdefault(update.specification_version, []).append(update.update)

    assert {version: tuple(values) for version, values in updates.items()} == _EXPECTED_UPDATES


@pytest.mark.parametrize("update", all_openapi_document_updates(), ids=lambda update: update.catalogue_id)
def test_every_update_bundles_all_documents_with_verified_provenance(update: OpenApiDocumentUpdate) -> None:
    directory = OPENAPI_DOCUMENTS_ROOT / update.catalogue_id
    sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))

    assert sources["specVersion"] == update.specification_version
    assert sources["openApiDocumentUpdate"] == update.update
    assert sources["catalogueId"] == update.catalogue_id
    assert sources["upstreamCommit"] == update.upstream_commit
    assert sorted(source["file"] for source in sources["sources"]) == sorted(
        f"{name}.json" for name in READ_WRITE_OPENAPI_DOCUMENT_NAMES
    )
    for source in sources["sources"]:
        assert source["commit"] == update.upstream_commit
        assert update.upstream_commit in source["url"]
        digest = hashlib.sha256((directory / source["file"]).read_bytes()).hexdigest()
        assert digest == "".join(source["sha256Chunks"]), source["file"]


def test_bundled_snapshot_directories_match_registry() -> None:
    directories = {path.name for path in OPENAPI_DOCUMENTS_ROOT.iterdir() if path.is_dir()}

    assert directories == {update.catalogue_id for update in all_openapi_document_updates()}
    assert set(bundled_openapi_document_paths()) >= {
        bundled_read_write_document(update, name)
        for update in all_openapi_document_updates()
        for name in READ_WRITE_OPENAPI_DOCUMENT_NAMES
    }


def test_update_display_metadata() -> None:
    update = openapi_document_update_by_catalogue_id("v4.0.0-Release-2")

    assert update.label == "Release 2"
    assert update.display_name == "v4.0.0 Release 2"
    assert openapi_document_update_to_json(update) == {
        "update": "Release-2",
        "label": "Release 2",
        "catalogueId": "v4.0.0-Release-2",
        "displayName": "v4.0.0 Release 2",
        "upstreamTag": update.upstream_tag,
        "upstreamCommit": update.upstream_commit,
    }


def test_bind_read_write_document_targets_selected_update_only() -> None:
    baseline = openapi_document_update_by_catalogue_id("v4.0.1-Baseline")
    update_1 = openapi_document_update_by_catalogue_id("v4.0.1-Update-1")
    logical = logical_read_write_document("account-info-openapi")

    assert is_logical_read_write_document(logical)
    assert bind_read_write_document(logical, update_1) == "ob-read-write/v4.0.1-Update-1/account-info-openapi"
    assert bind_read_write_document("inline-test-document", update_1) == "inline-test-document"
    with pytest.raises(ValueError):
        bind_read_write_document(bundled_read_write_document(baseline, "account-info-openapi"), update_1)
    with pytest.raises(ValueError):
        bind_read_write_document(logical_read_write_document("unknown-openapi"), update_1)


@pytest.mark.parametrize(
    "catalogue",
    [*supported_catalogues(), CVRP_LEGACY_FCS_CATALOGUE, CVRP_V401_LEGACY_FCS_CATALOGUE],
    ids=lambda catalogue: f"{catalogue.key.api}-{catalogue.key.specification_version}",
)
def test_every_catalogue_schema_ref_resolves_in_every_update(catalogue: object) -> None:
    from conformance.catalogue import TestCatalogue, _test_case_applies_to_specification_version

    test_catalogue = cast(TestCatalogue, catalogue)
    version_definition = specification_version_for_catalogue(
        standard=test_catalogue.key.standard,
        endpoint_version=test_catalogue.key.version,
        specification_version=test_catalogue.key.specification_version,
        api=None,
    )
    if version_definition is None or not version_definition.openapi_document_updates:
        pytest.skip("catalogue does not use selectable OpenAPI document updates")
    for test_case in test_catalogue.test_cases:
        if not _test_case_applies_to_specification_version(test_case, test_catalogue.key.specification_version):
            continue
        for assertion in test_case.assertions:
            document = assertion.rule.get("document")
            schema_ref = assertion.rule.get("schemaRef")
            if not (isinstance(document, str) and isinstance(schema_ref, str)):
                continue
            assert is_logical_read_write_document(document), test_case.test_case_id
            for update in version_definition.openapi_document_updates:
                schema_validation._prepared_schema_ref(
                    source="bundled_openapi",
                    document=bind_read_write_document(document, update),
                    schema_ref=schema_ref,
                )


def test_v4_0_1_catalogues_are_independent_copies_of_v4_0_0() -> None:
    v400 = AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE
    v401 = AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE

    assert v400.key.version == v401.key.version == "v4.0"
    assert (v400.key.specification_version, v401.key.specification_version) == ("4.0.0", "4.0.1")
    assert [case.test_case_id for case in v400.test_cases] == [case.test_case_id for case in v401.test_cases]
    assert all(a is not b for a, b in zip(v400.test_cases, v401.test_cases, strict=True)), (
        "4.0.1 cases must not share objects with 4.0.0"
    )


@pytest.mark.parametrize(
    ("version", "update", "expected_document"),
    [
        ("4.0.1", "Baseline", "ob-read-write/v4.0.1-Baseline/account-info-openapi"),
        ("4.0.1", "Update-1", "ob-read-write/v4.0.1-Update-1/account-info-openapi"),
        ("4.0.0", "Release-2", "ob-read-write/v4.0.0-Release-2/account-info-openapi"),
    ],
)
def test_compile_binds_schema_assertions_to_selected_update(version: str, update: str, expected_document: str) -> None:
    document = parse_test_plan_document(_canonical_plan(version=version, update=update))
    assert isinstance(document, PlanDocumentV2)

    compiled = compile_test_plan_document(document, supported_catalogues())

    assert _schema_documents(document) == {expected_document}
    assert compiled.catalogue_key.specification_version == version
    assert compiled.traceability.openapi_document_update is not None
    assert compiled.traceability.openapi_document_update.update == update
    assert compiled.traceability.endpoint_version == "v4.0"


def test_canonical_plan_requires_openapi_document_update_for_read_write() -> None:
    with pytest.raises(CatalogueError, match=r"openApiDocumentUpdate"):
        parse_test_plan_document(_canonical_plan(update=None))


def test_canonical_plan_rejects_update_from_another_version() -> None:
    with pytest.raises(CatalogueError, match=r"openApiDocumentUpdate must be one of: Baseline, Update-1"):
        parse_test_plan_document(_canonical_plan(version="4.0.1", update="Update-5"))


def test_dcr_plan_rejects_openapi_document_update() -> None:
    raw: JsonObject = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
            "openApiDocumentUpdate": "Baseline",
        },
        "securityEnvironment": {},
        "endpoints": [],
        "metadata": {},
    }

    with pytest.raises(CatalogueError):
        parse_test_plan_document(raw)


def test_removed_specification_version_4_0_is_rejected() -> None:
    with pytest.raises(CatalogueError):
        parse_test_plan_document(_canonical_plan(version="4.0", update="Update-5"))


def test_export_round_trips_openapi_document_update() -> None:
    document = parse_test_plan_document(_canonical_plan(version="4.0.0", update="Update-4"))
    assert isinstance(document, PlanDocumentV2)

    exported = plan_document_to_json_object(document)
    specification = cast(JsonObject, exported["specification"])

    assert specification["openApiDocumentUpdate"] == "Update-4"
    reparsed = parse_test_plan_document(copy.deepcopy(exported))
    assert isinstance(reparsed, PlanDocumentV2)
    assert reparsed.openapi_document_update == "Update-4"
