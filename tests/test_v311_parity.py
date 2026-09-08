"""Regression guards for Open Banking Read/Write v3.1.11 parity."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, urlsplit

import pytest

from conformance.api.builder_wizard import catalogue_scope_hierarchy, config_visibility_for_plan_document
from conformance.assertions import evaluate_assertion
from conformance.catalogue import (
    CatalogueError,
    CatalogueKey,
    CatalogueTestCase,
    ImplementedEndpoint,
    PlanDocumentBoundary,
    PlanDocumentV2,
    TestPlanSpec,
    compile_test_plan,
    compile_test_plan_document,
    parse_test_plan_document,
    plan_document_to_json_object,
    supported_plan_document_boundaries,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.catalogues.ais import AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE
from conformance.catalogues.cbpii import CBPII_V31_FCS_CATALOGUE
from conformance.catalogues.pis import PIS_V31_PAYMENT_CATALOGUE
from conformance.catalogues.vrp import VRP_V31_LEGACY_FCS_CATALOGUE
from conformance.context import RuntimeConfig
from conformance.executor import _compiled_plan_to_manifest, compiled_plan_synthetic_inline_steps
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import DetachedJwsPolicy, JsonBody, ManifestStep
from conformance.results import build_smoke_check_result
from conformance.schema_validation import _load_bundled_document
from conformance.specification_registry import derived_security_profile_for_boundary

_STANDARDS_ROOT = Path(__file__).resolve().parents[1] / "conformance" / "standards" / "ob_read_write" / "v3_1_11"
"""Repository path containing the pinned v3.1.11 parity artifacts."""

_PARITY_CONTRACT_PATH = _STANDARDS_ROOT / "parity-contract.json"
"""Repository path of the machine-checkable v3.1.11 parity contract."""

_LEGACY_COMMIT = "1908789b52e26e0a79fc5a565e411f771006c3ce"
"""Pinned legacy FCS v1.10.0 commit."""

_EXPECTED_INVENTORY = {
    "ais": (96, 96, 35, 0),
    "pis": (32, 32, 31, 22),
    "cbpii": (14, 13, 10, 0),
    "vrp": (14, 14, 14, 11),
}
"""Expected row, unique-id, schema-check, and signature-check counts."""

_V311_CATALOGUES = {
    "ais": AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    "pis": PIS_V31_PAYMENT_CATALOGUE,
    "cbpii": CBPII_V31_FCS_CATALOGUE,
    "vrp": VRP_V31_LEGACY_FCS_CATALOGUE,
}
"""Dedicated v3.1.11 catalogues keyed by API family."""


def _parity_contract() -> JsonObject:
    """Load the checked-in v3.1.11 parity contract.

    Returns:
        Parsed parity contract object.
    """
    return cast("JsonObject", json.loads(_PARITY_CONTRACT_PATH.read_text(encoding="utf-8")))


def _sha256(path: Path) -> str:
    """Calculate a file's SHA-256 digest.

    Args:
        path: File to hash.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalogue_script_ids(api: str) -> tuple[str, ...]:
    """Extract ordered v3.1 legacy script ids from one catalogue.

    Args:
        api: Open Banking API family.

    Returns:
        Script ids represented by catalogue compliance-scope entries.
    """
    script_ids: list[str] = []
    for test_case in _V311_CATALOGUES[api].test_cases:
        script_ids.extend(_case_script_ids(test_case.compliance_scope, api=api))
    return tuple(script_ids)


def _case_script_ids(compliance_scope: tuple[str, ...], *, api: str) -> tuple[str, ...]:
    """Extract v3.1 script ids from one case's compliance scope.

    Args:
        compliance_scope: Traceability entries attached to a catalogue case.
        api: Open Banking API family.

    Returns:
        Ordered legacy script ids represented by the case.
    """
    script_ids: list[str] = []
    if api == "ais":
        for scope in compliance_scope:
            if not scope.startswith("legacy-fcs-v3.1-ids:"):
                continue
            raw_ids = scope.removeprefix("legacy-fcs-v3.1-ids:")
            if raw_ids != "none":
                script_ids.extend(raw_ids.split(","))
        return tuple(script_ids)

    manifest_name = {
        "pis": "ob_3.1_payment_fca.json",
        "cbpii": "ob_3.1_cbpii_fca.json",
        "vrp": "ob_3.1_variable_recurring_payments.json",
    }[api]
    marker = f"{manifest_name}#"
    for scope in compliance_scope:
        if marker in scope:
            script_ids.append(scope.split(marker, maxsplit=1)[1].split("(", maxsplit=1)[0])
    return tuple(script_ids)


def _normalized_operation_path(path: str) -> str:
    """Normalize path variables while preserving every literal path segment.

    Args:
        path: Legacy or catalogue operation path.

    Returns:
        Path with all variable syntaxes represented by ``{value}``.
    """
    normalized = re.sub(r"\$\{[^}]+\}", "{value}", path)
    normalized = re.sub(r"\{[^}]+\}", "{value}", normalized)
    return re.sub(r"\$[A-Za-z0-9_-]+", "{value}", normalized)


def _case_assertion_ids(test_case: CatalogueTestCase) -> frozenset[str]:
    """Extract legacy assertion ids represented by one catalogue case.

    Args:
        test_case: Catalogue case to inspect.

    Returns:
        Legacy assertion identifiers found in scope and executable rule
        provenance.
    """
    assertion_ids: set[str] = set()
    scope_prefixes = {
        "legacy-fcs-assertions:": ",",
        "legacy-fcs-assertion:": ",",
        "legacy_asserts:": "|",
        "legacy-assert:": "|",
    }
    for scope in test_case.compliance_scope:
        for prefix, separator in scope_prefixes.items():
            if scope.startswith(prefix):
                assertion_ids.update(scope.removeprefix(prefix).split(separator))
    for assertion in test_case.assertions:
        raw_ids = assertion.rule.get("legacyAssertionIds")
        if isinstance(raw_ids, list):
            assertion_ids.update(str(value) for value in raw_ids)
        raw_id = assertion.rule.get("legacyAssertionId")
        if isinstance(raw_id, str):
            assertion_ids.add(raw_id)
    return frozenset(assertion_ids)


@pytest.mark.unit
def test_v311_parity_contract_pins_sources_and_complete_inventory() -> None:
    """Pin source hashes and complete legacy manifest inventory counts."""
    contract = _parity_contract()
    legacy_source = cast("JsonObject", contract["legacySource"])
    assert legacy_source["release"] == "v1.10.0"
    assert legacy_source["commit"] == _LEGACY_COMMIT
    source_hashes = cast("JsonObject", legacy_source["sourceHashes"])
    for filename, expected_digest in source_hashes.items():
        assert _sha256(_STANDARDS_ROOT / "legacy" / filename) == expected_digest

    manifests = cast("list[JsonObject]", contract["manifests"])
    assert [manifest["api"] for manifest in manifests] == ["ais", "pis", "cbpii", "vrp"]
    for manifest in manifests:
        api = str(manifest["api"])
        assert (
            manifest["rowCount"],
            manifest["uniqueScriptIdCount"],
            manifest["schemaCheckCount"],
            manifest["signatureValidationCount"],
        ) == _EXPECTED_INVENTORY[api]
        rows = cast("list[JsonObject]", manifest["rows"])
        assert [row["rowIndex"] for row in rows] == list(range(len(rows)))
        assert len({str(row["rowKey"]) for row in rows}) == len(rows)

    cbpii = next(manifest for manifest in manifests if manifest["api"] == "cbpii")
    cbpii_rows = cast("list[JsonObject]", cbpii["rows"])
    assert [row["scriptId"] for row in cbpii_rows].count("OB-301-CBPII-000009") == 2
    assert contract["corrections"] == [
        {
            "id": "v311-ais-product-path-pluralization",
            "originalBehavior": "GET /open-banking/v3.1/aisp/product",
            "rationale": (
                "The singular legacy path is not present in the pinned v3.1.11 Account Information "
                "OpenAPI document; the standards endpoint is /products."
            ),
            "regressionTest": "tests/test_v311_parity.py::test_v311_catalogues_match_legacy_operations_and_flags",
            "replacementBehavior": "GET /open-banking/v3.1/aisp/products",
            "sourceRow": "ob_3.1_accounts_transactions_fca.json#63:OB-301-PRO-103403",
        }
    ]


@pytest.mark.unit
def test_v311_openapi_sources_match_pinned_hashes_and_load() -> None:
    """Pin all four normative OpenAPI snapshots and loader identifiers."""
    sources = cast(
        "JsonObject",
        json.loads((_STANDARDS_ROOT / "sources.json").read_text(encoding="utf-8")),
    )
    openapi_sources = [
        source
        for source in cast("list[JsonObject]", sources["sources"])
        if not str(source["file"]).startswith("legacy/")
    ]
    assert len(openapi_sources) == 4
    for source in openapi_sources:
        assert source["ref"] == "v3.1.11"
        assert _sha256(_STANDARDS_ROOT / str(source["file"])) == source["sha256"]

    for document in (
        "ob-read-write-v3.1.11-account-info-openapi",
        "ob-read-write-v3.1.11-payment-initiation-openapi",
        "ob-read-write-v3.1.11-confirmation-funds-openapi",
        "ob-read-write-v3.1.11-vrp-openapi",
    ):
        assert "paths" in _load_bundled_document(document)


@pytest.mark.unit
def test_v311_boundary_is_exact_and_uses_fapi1_advanced() -> None:
    """Expose only exact v3.1.11 with the FAPI 1 Advanced profile."""
    boundaries = supported_plan_document_boundaries(supported_catalogues())
    versions = [
        boundary.version
        for boundary in boundaries
        if boundary.scheme == "open-banking-uk" and boundary.specification == "read-write"
    ]
    assert "3.1.11" in versions
    assert "3.1" not in versions
    assert derived_security_profile_for_boundary("open-banking-uk", "read-write", "3.1.11") == "fapi1-advanced"
    assert {catalogue.key for catalogue in _V311_CATALOGUES.values()} == {
        CatalogueKey("open-banking", "v3.1", api) for api in _V311_CATALOGUES
    }


@pytest.mark.unit
def test_v311_builder_scope_uses_only_v31_endpoints() -> None:
    """Expose every supported API family through v3.1 builder scope."""
    hierarchy = catalogue_scope_hierarchy(
        PlanDocumentBoundary("open-banking-uk", "read-write", "3.1.11"),
        selected_resource_group_ids=(
            "account-and-transaction",
            "payment-initiation",
            "confirmation-of-funds",
            "variable-recurring-payments",
        ),
    )
    assert {group.id for group in hierarchy.resource_groups} == {
        "account-and-transaction",
        "payment-initiation",
        "confirmation-of-funds",
        "variable-recurring-payments",
    }
    endpoint_paths = {endpoint.path for group in hierarchy.resource_groups for endpoint in group.endpoints}
    assert endpoint_paths
    assert all("/open-banking/v3.1/" in path for path in endpoint_paths)


@pytest.mark.unit
def test_v311_catalogues_cover_every_selected_legacy_row_without_v4_leakage() -> None:
    """Match all selected v1.10.0 rows and reject v4 paths or provenance."""
    manifests = {
        str(manifest["api"]): manifest for manifest in cast("list[JsonObject]", _parity_contract()["manifests"])
    }
    for api, catalogue in _V311_CATALOGUES.items():
        rows = cast("list[JsonObject]", manifests[api]["rows"])
        expected_ids = [str(row["scriptId"]) for row in rows if row["includedFor3.1.11"] is True]
        assert Counter(_catalogue_script_ids(api)) == Counter(expected_ids)
        assert all(
            "/v4.0/" not in endpoint.path
            for capability in catalogue.capabilities
            for endpoint in capability.endpoint_refs
        )
        assert all(
            "/v4.0/" not in request.path for test_case in catalogue.test_cases for request in test_case.request_steps
        )
        assert all("ob_4.0_" not in scope for test_case in catalogue.test_cases for scope in test_case.compliance_scope)
        assert all("V4" not in scope for test_case in catalogue.test_cases for scope in test_case.compliance_scope)
        assert all(
            "v4.0" not in str(assertion.rule.get("document", ""))
            for test_case in catalogue.test_cases
            for assertion in test_case.assertions
        )


@pytest.mark.unit
def test_v311_catalogues_match_legacy_operations_and_flags() -> None:
    """Match every selected legacy method, URI, schema flag, and signature flag."""
    contract = _parity_contract()
    corrections = {
        str(correction["sourceRow"]): str(correction["replacementBehavior"])
        for correction in cast("list[JsonObject]", contract["corrections"])
    }
    prefixes = {
        "ais": "/open-banking/v3.1/aisp",
        "pis": "/open-banking/v3.1/pisp",
        "cbpii": "/open-banking/v3.1/cbpii",
        "vrp": "/open-banking/v3.1/pisp",
    }
    for manifest in cast("list[JsonObject]", contract["manifests"]):
        api = str(manifest["api"])
        cases_by_script: dict[str, list[CatalogueTestCase]] = {}
        for test_case in _V311_CATALOGUES[api].test_cases:
            for script_id in _case_script_ids(test_case.compliance_scope, api=api):
                cases_by_script.setdefault(script_id, []).append(test_case)

        for row in cast("list[JsonObject]", manifest["rows"]):
            if row["includedFor3.1.11"] is not True:
                continue
            normalized_row = cast("JsonObject", row["normalizedRow"])
            expected_method = str(normalized_row["method"])
            expected_path = f"{prefixes[api]}{normalized_row['uri']}"
            correction = corrections.get(str(row["rowKey"]))
            if correction is not None:
                expected_method, expected_path = correction.split(" ", maxsplit=1)
            candidates = cases_by_script[str(row["scriptId"])]
            matching_cases = [
                test_case
                for test_case in candidates
                if test_case.request_steps
                and test_case.request_steps[0].method == expected_method
                and _normalized_operation_path(test_case.request_steps[0].path)
                == _normalized_operation_path(expected_path)
            ]
            assert matching_cases, row["rowKey"]
            legacy_assertion = next(
                assertion
                for test_case in matching_cases
                for assertion in test_case.assertions
                if assertion.kind == "legacy_fcs" and assertion.rule.get("rowKey") == row["rowKey"]
            )
            expected_assertion_ids = {
                str(assertion_id)
                for field in ("asserts", "asserts_one_of", "asserts_last_if_all")
                for assertion_id in cast("list[JsonValue]", normalized_row.get(field, []))
            }
            represented_assertion_ids = set().union(*(_case_assertion_ids(test_case) for test_case in matching_cases))
            assert expected_assertion_ids.issubset(represented_assertion_ids), row["rowKey"]
            all_count = len(cast("list[JsonValue]", normalized_row.get("asserts", [])))
            one_of_count = len(cast("list[JsonValue]", normalized_row.get("asserts_one_of", [])))
            resolved_assertions = cast("list[JsonObject]", row["resolvedAssertions"])
            resolved_expectations = [assertion["expect"] for assertion in resolved_assertions]
            assert legacy_assertion.rule["allOf"] == resolved_expectations[:all_count]
            assert legacy_assertion.rule["oneOf"] == resolved_expectations[all_count : all_count + one_of_count]
            assert legacy_assertion.rule["lastIfAll"] == resolved_expectations[all_count + one_of_count :]
            assert any(test_case.response_signature_required for test_case in matching_cases) is bool(
                normalized_row.get("validateSignature")
            )
            response_statuses = {
                int(status)
                for assertion in resolved_assertions
                if isinstance(assertion.get("expect"), dict)
                and isinstance(status := cast("JsonObject", assertion["expect"]).get("status-code"), int)
            }
            has_schema_assertion = any(
                assertion.kind == "response_schema"
                or isinstance(schema_refs := assertion.rule.get("schemaRefs"), dict)
                and (
                    not response_statuses
                    or any(str(response_status) in schema_refs for response_status in response_statuses)
                )
                for test_case in matching_cases
                for assertion in test_case.assertions
            )
            expects_json_schema = bool(normalized_row.get("schemaCheck")) and (
                not response_statuses or any(status != 204 for status in response_statuses)
            )
            assert has_schema_assertion is expects_json_schema, row["rowKey"]


@pytest.mark.unit
def test_v311_ais_compiles_every_legacy_row_without_optional_capability_gates() -> None:
    """Compile all 96 AIS rows from endpoint selection alone."""
    endpoint_refs = dict.fromkeys(
        endpoint_ref
        for test_case in AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE.test_cases
        for endpoint_ref in test_case.applicability.endpoint_refs
    )
    compiled = compile_test_plan(
        AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=tuple(
                ImplementedEndpoint(
                    method=endpoint_ref.method,
                    path=endpoint_ref.path,
                    resource_group="AIS",
                )
                for endpoint_ref in endpoint_refs
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://resource.example.com",
                "consentedAccountId": "account-123",
            },
            specification_version="3.1.11",
        ),
    )
    compiled_ids = tuple(
        script_id
        for test_case in compiled.test_cases
        for script_id in _case_script_ids(test_case.compliance_scope, api="ais")
    )
    ais_manifest = next(
        manifest for manifest in cast("list[JsonObject]", _parity_contract()["manifests"]) if manifest["api"] == "ais"
    )
    expected_ids = tuple(
        str(row["scriptId"])
        for row in cast("list[JsonObject]", ais_manifest["rows"])
        if row["includedFor3.1.11"] is True
    )

    assert Counter(compiled_ids) == Counter(expected_ids)
    assert len(compiled_ids) == 96


@pytest.mark.unit
def test_v311_ais_replays_distinct_legacy_transaction_queries(tmp_path: Path) -> None:
    """Execute all account-transaction variants with their pinned query values.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "consentedAccountId": "account-123",
    }
    compiled = compile_test_plan(
        AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="GET",
                    path="/open-banking/v3.1/aisp/accounts/{AccountId}/transactions",
                    resource_group="AIS",
                ),
            ),
            runtime_inputs=runtime_inputs,
            specification_version="3.1.11",
        ),
    )
    manifest = _compiled_plan_to_manifest(
        compiled,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )
    steps = {
        step.id: step
        for step in manifest.steps
        if isinstance(step, ManifestStep) and step.id.startswith("ais-at-account-transactions-")
    }

    assert {
        "ais-at-account-transactions-200-request",
        "ais-at-account-transactions-detail-200-request",
        "ais-at-account-transactions-detail-iso8601-seconds-200-request",
        "ais-at-account-transactions-detail-iso8601-milliseconds-200-request",
    }.issubset(steps)
    assert urlsplit(steps["ais-at-account-transactions-200-request"].request.url).query == ""
    assert urlsplit(steps["ais-at-account-transactions-detail-200-request"].request.url).query == ""
    assert parse_qsl(
        urlsplit(steps["ais-at-account-transactions-detail-iso8601-seconds-200-request"].request.url).query
    ) == [
        ("fromBookingDateTime", "2020-04-23T15:47:00"),
        ("toBookingDateTime", "2020-04-23T15:48:00"),
    ]
    assert parse_qsl(
        urlsplit(steps["ais-at-account-transactions-detail-iso8601-milliseconds-200-request"].request.url).query
    ) == [
        ("fromBookingDateTime", "2020-04-23T15:47:00.999"),
        ("toBookingDateTime", "2020-04-23T15:48:00.999"),
    ]


@pytest.mark.unit
def test_v311_ais_replays_legacy_statement_queries() -> None:
    """Attach each pinned statement-date variant to its own request."""
    expected_queries = {
        "ais-at-legacy-statement-sta-105900": {"fromStatementDateTime": "2016-01-01T10:40:00"},
        "ais-at-legacy-statement-sta-106000": {"fromStatementDateTime": "20160101T104012.345Z"},
        "ais-at-legacy-statement-sta-106100": {"fromStatementDateTime": "2016-01-01T10:40:12.34567Z"},
        "ais-at-legacy-statement-sta-106200": {"fromStatementDateTime": "2016-01-01T10:40:12.34567+01"},
        "ais-at-legacy-statement-sta-106300": {"fromStatementDateTime": "20160101T104012.567+01"},
    }
    cases = {test_case.test_case_id: test_case for test_case in AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE.test_cases}

    for test_case_id, expected_query in expected_queries.items():
        assert dict(cases[test_case_id].request_steps[0].query_parameters) == expected_query


@pytest.mark.unit
def test_v311_cbpii_replays_legacy_generated_request_values(tmp_path: Path) -> None:
    """Resolve CBPII date macros and invalid consent id exactly as legacy FCS.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "debtorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "debtorAccountIdentification": "70000170000002",
        "debtorAccountName": "Model Bank Account",
    }
    compiled = compile_test_plan(
        CBPII_V31_FCS_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=CBPII_V31_FCS_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/open-banking/v3.1/cbpii/funds-confirmation-consents",
                    resource_group="CBPII",
                    capability_ids=("cbpii.funds-confirmation-consents.expiration-date-time-formats",),
                ),
                ImplementedEndpoint(
                    method="DELETE",
                    path="/open-banking/v3.1/cbpii/funds-confirmation-consents/{consentId}",
                    resource_group="CBPII",
                ),
            ),
            runtime_inputs=runtime_inputs,
            specification_version="3.1.11",
        ),
    )
    before = datetime.now(UTC)
    manifest = _compiled_plan_to_manifest(
        compiled,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )
    after = datetime.now(UTC)
    steps = {step.id: step for step in manifest.steps if isinstance(step, ManifestStep)}

    midnight_step_ids = {
        "cbpii-consent-create-core-request",
        "cbpii-consent-create-invalid-account-name-request",
        "cbpii-consent-create-invalid-account-identification-request",
        "cbpii-consent-create-invalid-scheme-name-request",
    }
    expected_dates = {(before + timedelta(days=1)).date(), (after + timedelta(days=1)).date()}
    for step_id in midnight_step_ids:
        body = steps[step_id].request.body
        assert isinstance(body, JsonBody)
        data = cast("JsonObject", body.value)["Data"]
        assert isinstance(data, dict)
        value = data["ExpirationDateTime"]
        assert isinstance(value, str)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.date() in expected_dates
        assert parsed.time() == datetime.min.time()
        assert value.endswith("T00:00:00Z")

    expected_patterns = {
        "cbpii-consent-create-expiration-milliseconds-z-request": (
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
            "Z",
        ),
        "cbpii-consent-create-expiration-milliseconds-offset-request": (
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00",
            "+00:00",
        ),
        "cbpii-consent-create-expiration-seconds-z-request": (
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            "Z",
        ),
        "cbpii-consent-create-expiration-seconds-offset-request": (
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00",
            "+00:00",
        ),
    }
    lower_bound = before + timedelta(days=1, seconds=-1)
    upper_bound = after + timedelta(days=1, seconds=1)
    for step_id, (pattern, suffix) in expected_patterns.items():
        body = steps[step_id].request.body
        assert isinstance(body, JsonBody)
        data = cast("JsonObject", body.value)["Data"]
        assert isinstance(data, dict)
        value = data["ExpirationDateTime"]
        assert isinstance(value, str)
        assert re.fullmatch(pattern, value)
        assert value.endswith(suffix)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert lower_bound <= parsed <= upper_bound

    invalid_delete = steps["cbpii-consent-delete-invalid-id-request"]
    assert invalid_delete.request.url == (
        "https://resource.example.com/open-banking/v3.1/cbpii/funds-confirmation-consents/42"
    )


@pytest.mark.unit
def test_v311_canonical_plan_round_trips_and_compiles_version_correct_paths() -> None:
    """Round-trip the public plan contract and compile v3.1-only execution."""
    raw_plan = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "3.1.11",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        },
        "resourceGroups": [
            {
                "id": "AIS",
                "endpoints": [{"method": "GET", "path": "/open-banking/v3.1/aisp/accounts"}],
            }
        ],
        "businessTestData": {},
        "metadata": {},
    }
    document = cast("PlanDocumentV2", parse_test_plan_document(raw_plan))
    exported = plan_document_to_json_object(document)
    reparsed = parse_test_plan_document(exported)
    compiled = compile_test_plan_document(reparsed, supported_catalogues())

    assert exported["specification"] == raw_plan["specification"]
    assert compiled.catalogue_key == CatalogueKey("open-banking-uk", "3.1.11", "read-write")
    assert compiled.traceability.provenance is not None
    assert compiled.traceability.provenance.commit == _LEGACY_COMMIT
    assert all("/v4.0/" not in request.path for test_case in compiled.test_cases for request in test_case.request_steps)
    consent_step = next(
        request
        for test_case in compiled.test_cases
        for request in test_case.request_steps
        if request.step_id == "ais-at-setup-consent-request"
    )
    assert consent_step.detached_jws_profile == "legacy-b64-false"

    rendered_result = build_smoke_check_result(
        [],
        started_at=datetime.now(UTC),
        compiled_plan=compiled,
    ).to_json_object()
    catalogue_evidence = cast("JsonObject", rendered_result["catalogue"])
    provenance = cast("JsonObject", catalogue_evidence["provenance"])
    assert catalogue_evidence["version"] == "3.1.11"
    assert provenance["release"] == "v1.10.0"
    assert provenance["commit"] == _LEGACY_COMMIT


@pytest.mark.unit
def test_v311_rejects_a_conflicting_security_profile() -> None:
    """Reject a participant profile that conflicts with the v3.1.11 boundary."""
    raw_plan = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "3.1.11",
            "profile": "FAPI2",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        },
        "resourceGroups": [
            {
                "id": "AIS",
                "endpoints": [{"method": "GET", "path": "/open-banking/v3.1/aisp/accounts"}],
            }
        ],
        "businessTestData": {},
        "metadata": {},
    }
    with pytest.raises(
        CatalogueError,
        match=r"profile must be one of: FAPI1_ADVANCED for OBL_READ_WRITE 3\.1\.11",
    ):
        parse_test_plan_document(raw_plan)


@pytest.mark.unit
def test_v311_pis_manifest_uses_explicit_signing_profile(tmp_path: Path) -> None:
    """Compile v3.1 PIS requests with explicit signing metadata and paths.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
    }
    compiled = compile_test_plan(
        PIS_V31_PAYMENT_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=PIS_V31_PAYMENT_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/open-banking/v3.1/pisp/domestic-payments",
                    resource_group="DomesticPayments",
                ),
            ),
            runtime_inputs=runtime_inputs,
            specification_version="3.1.11",
        ),
    )
    manifest = _compiled_plan_to_manifest(
        compiled,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )

    request_steps = [step for step in manifest.steps if isinstance(step, ManifestStep)]
    assert request_steps
    assert all("/v4.0/" not in step.request.url for step in request_steps)
    signed_steps = [step for step in request_steps if step.request.detached_jws is not None]
    assert signed_steps
    assert all(
        step.request.detached_jws
        == DetachedJwsPolicy(
            source="fapi-signing",
            profile="ob-v3.1.4+",
        )
        for step in signed_steps
    )
    consent_step = next(step for step in request_steps if step.id == "pis-v31-domestic-payment-consent-create-request")
    assert (
        evaluate_assertion(
            consent_step.assertions[0],
            status_code=418,
            headers={},
            body={},
        ).passed
        is False
    )


@pytest.mark.unit
def test_v311_pis_keeps_one_request_case_per_legacy_row() -> None:
    """Execute each selected PIS ledger row through a distinct request case."""
    row_cases = [
        test_case
        for test_case in PIS_V31_PAYMENT_CATALOGUE.test_cases
        if _case_script_ids(test_case.compliance_scope, api="pis")
    ]
    script_ids_by_case = {
        test_case.test_case_id: _case_script_ids(test_case.compliance_scope, api="pis") for test_case in row_cases
    }

    assert len(row_cases) == 32
    assert all(len(script_ids) == 1 for script_ids in script_ids_by_case.values())
    assert script_ids_by_case["pis-v31-domestic-payment-consent-create-without-authorisation"] == ("OB-301-DOP-100100",)
    assert script_ids_by_case["pis-v31-domestic-payment-consent-create"] == ("OB-301-DOP-100300",)
    cases = {test_case.test_case_id: test_case for test_case in row_cases}
    assert (
        cases["pis-v31-domestic-payment-consent-create-without-authorisation"].request_steps[0].psu_authorization
        is None
    )
    assert cases["pis-v31-domestic-payment-consent-create"].request_steps[0].psu_authorization is not None
    assert cases["pis-v31-domestic-payment-consent-create-without-authorisation"].response_signature_required is True
    assert cases["pis-v31-domestic-payment-consent-create"].response_signature_required is True


@pytest.mark.unit
def test_v311_pis_replays_legacy_request_constants_and_date_macros(tmp_path: Path) -> None:
    """Replay fixed PIS identifiers and next-day midnight date formats.

    Args:
        tmp_path: Pytest temporary directory used as the runtime-input base.
    """
    cases = {test_case.test_case_id: test_case for test_case in PIS_V31_PAYMENT_CATALOGUE.test_cases}
    expected_end_to_end_identifications = {
        "pis-v31-domestic-payment-consent-create-without-authorisation": "e2e-domestic-pay",
        "pis-v31-domestic-payment-consent-create": "e2e-domestic-pay",
        "pis-v31-domestic-payment-consent-create-without-financial-id": "e2e-domestic-pay",
        "pis-v31-domestic-payment-create": "e2e-domestic-pay",
        "pis-v31-domestic-scheduled-payment-consent-create": "e2e-domestic-sched-pay",
        "pis-v31-domestic-scheduled-payment-consent-create-with-offset-datetime": ("e2e-domestic-sched-pay-v1"),
        "pis-v31-domestic-scheduled-payment-consent-create-with-utc-datetime": ("e2e-domestic-sched-pay-v2"),
        "pis-v31-domestic-scheduled-payment-consent-create-and-authorise": "e2e-domestic-sched-pay",
        "pis-v31-domestic-scheduled-payment-create": "e2e-domestic-sched-pay",
        "pis-v31-international-payment-consent-create": "e2e-internat-pay",
        "pis-v31-international-payment-create": "e2e-internat-pay",
        "pis-v31-international-scheduled-payment-consent-create": "e2e-internat-sched-pay",
        "pis-v31-international-scheduled-payment-create": "e2e-internat-sched-pay",
    }
    for test_case_id, expected_value in expected_end_to_end_identifications.items():
        body = cases[test_case_id].request_steps[0].body_template
        assert isinstance(body, dict)
        data = body["Data"]
        assert isinstance(data, dict)
        initiation = data["Initiation"]
        assert isinstance(initiation, dict)
        assert initiation["EndToEndIdentification"] == expected_value

    submission_case_ids = {
        "pis-v31-domestic-payment-create",
        "pis-v31-domestic-scheduled-payment-create",
        "pis-v31-international-payment-create",
        "pis-v31-international-scheduled-payment-create",
    }
    for test_case_id in submission_case_ids:
        body_template = str(cases[test_case_id].request_steps[0].body_template)
        assert ".response.body.Data.Initiation." in body_template
        assert ".request.body.Data.Initiation." not in body_template

    runtime_inputs = {
        "resourceBaseUrl": "https://resource.example.com",
        "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
        "pisCreditorAccountIdentification": "70000170000002",
        "pisCreditorAccountName": "Domestic creditor",
        "pisInstructedAmountAmount": "1.00",
        "pisInstructedAmountCurrency": "GBP",
        "pisRequestedExecutionDateTime": "2026-12-01T00:00:00+00:00",
    }
    compiled = compile_test_plan(
        PIS_V31_PAYMENT_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=PIS_V31_PAYMENT_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/open-banking/v3.1/pisp/domestic-scheduled-payment-consents",
                    resource_group="DomesticScheduledPayments",
                ),
            ),
            runtime_inputs=runtime_inputs,
            specification_version="3.1.11",
        ),
    )
    before = datetime.now(UTC)
    manifest = _compiled_plan_to_manifest(
        compiled,
        runtime_inputs=runtime_inputs,
        runtime_input_base_dir=tmp_path,
        runtime_config=RuntimeConfig(discovery_url="https://auth.example.com/.well-known/openid-configuration"),
    )
    after = datetime.now(UTC)
    steps = {step.id: step for step in manifest.steps if isinstance(step, ManifestStep)}
    expected_dates = {(before + timedelta(days=1)).date(), (after + timedelta(days=1)).date()}
    expected_suffixes = {
        "pis-v31-domestic-scheduled-payment-consent-create-with-offset-datetime-request": "+00:00",
        "pis-v31-domestic-scheduled-payment-consent-create-with-utc-datetime-request": "Z",
    }
    for step_id, expected_suffix in expected_suffixes.items():
        manifest_body = steps[step_id].request.body
        assert isinstance(manifest_body, JsonBody)
        data = cast("JsonObject", manifest_body.value)["Data"]
        assert isinstance(data, dict)
        initiation = data["Initiation"]
        assert isinstance(initiation, dict)
        value = initiation["RequestedExecutionDateTime"]
        assert isinstance(value, str)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.date() in expected_dates
        assert parsed.time() == datetime.min.time()
        assert value.endswith(expected_suffix)


@pytest.mark.unit
def test_v311_pis_standing_order_bodies_use_v31_shape() -> None:
    """Keep v4 mandate wrappers out of v3.1 standing-order requests."""
    standing_order_cases = [
        test_case
        for test_case in PIS_V31_PAYMENT_CATALOGUE.test_cases
        if "standing-order" in test_case.test_case_id and test_case.request_steps[0].body_template is not None
    ]
    assert standing_order_cases
    for test_case in standing_order_cases:
        body = test_case.request_steps[0].body_template
        assert isinstance(body, dict)
        data = body["Data"]
        assert isinstance(data, dict)
        initiation = data["Initiation"]
        assert isinstance(initiation, dict)
        assert "MandateRelatedInformation" not in initiation
        assert isinstance(initiation["Frequency"], str)
        requirement_ids = {requirement.input_id for requirement in test_case.runtime_input_requirements}
        if initiation["Frequency"] != "foobar":
            assert "pisStandingOrderFrequencyV31" in requirement_ids
        assert "pisStandingOrderFrequencyType" not in requirement_ids
        assert "pisStandingOrderFrequencyPointInTime" not in requirement_ids

    invalid_consent = next(
        test_case
        for test_case in standing_order_cases
        if test_case.test_case_id == "pis-v31-domestic-standing-order-consent-reject-invalid-frequency"
    )
    invalid_body = invalid_consent.request_steps[0].body_template
    assert isinstance(invalid_body, dict)
    invalid_data = invalid_body["Data"]
    assert isinstance(invalid_data, dict)
    invalid_initiation = invalid_data["Initiation"]
    assert isinstance(invalid_initiation, dict)
    assert invalid_initiation["NumberOfPayments"] == "string"
    assert invalid_initiation["FinalPaymentDateTime"] == "2027-01-30T14:34:33.083Z"


@pytest.mark.unit
def test_v311_builder_uses_scalar_standing_order_frequency() -> None:
    """Derive the v3.1 frequency runtime value and its version-specific field."""
    document = cast(
        "PlanDocumentV2",
        parse_test_plan_document(
            {
                "schemaVersion": "1.0",
                "specification": {
                    "family": "OBL_READ_WRITE",
                    "version": "3.1.11",
                    "profile": "FAPI1_ADVANCED",
                },
                "executionMode": "development",
                "securityEnvironment": {
                    "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                    "resourceBaseUrl": "https://resource.example.com",
                },
                "resourceGroups": [
                    {
                        "id": "PIS",
                        "endpoints": [
                            {
                                "method": "POST",
                                "path": "/open-banking/v3.1/pisp/domestic-standing-order-consents",
                            }
                        ],
                    }
                ],
                "businessTestData": {
                    "pis": {
                        "standingOrderFrequencyV31": "IntrvlWkDay:01:03",
                    }
                },
                "metadata": {},
            }
        ),
    )
    visibility = config_visibility_for_plan_document(document)

    assert document.runtime_inputs["pisStandingOrderFrequencyV31"] == "IntrvlWkDay:01:03"
    assert visibility.pis_v311_standing_order_frequency_required is True
    assert visibility.pis_standing_order_frequency_required is False


@pytest.mark.unit
def test_v311_pis_scheduled_submission_uses_its_legacy_consent_flow() -> None:
    """Bind row 101101 to the consent and authorization created by row 101000."""
    cases = {test_case.test_case_id: test_case for test_case in PIS_V31_PAYMENT_CATALOGUE.test_cases}
    consent = cases["pis-v31-domestic-scheduled-payment-consent-create-and-authorise"]
    consent_read = cases["pis-v31-domestic-scheduled-payment-consent-read-after-authorisation"]
    submission = cases["pis-v31-domestic-scheduled-payment-create"]

    assert submission.dependencies == (consent_read.test_case_id,)
    assert submission.request_steps[0].required_psu_authorization_step_id == consent.request_steps[0].step_id
    assert (
        submission.request_steps[0].required_token_id == "pis-domestic-scheduled-payment-legacy-access"  # noqa: S105 - semantic token id
    )
    assert consent.request_steps[0].step_id in str(submission.request_steps[0].body_template)
    assert "pis-v31-domestic-scheduled-payment-read" not in cases


@pytest.mark.unit
def test_v311_vrp_authorization_produces_the_required_token() -> None:
    """Keep v3.1 VRP consent authorization and dependent token ids aligned."""
    compiled = compile_test_plan(
        VRP_V31_LEGACY_FCS_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=VRP_V31_LEGACY_FCS_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=(
                ImplementedEndpoint(
                    method="POST",
                    path="/open-banking/v3.1/pisp/domestic-vrp-consents",
                    resource_group="DomesticVRP",
                ),
                ImplementedEndpoint(
                    method="POST",
                    path="/open-banking/v3.1/pisp/domestic-vrp-consents/{consentId}/funds-confirmation",
                    resource_group="DomesticVRP",
                    capability_ids=("vrp.funds-confirmation",),
                ),
            ),
            runtime_inputs={
                "resourceBaseUrl": "https://resource.example.com",
                "vrpCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
                "vrpCreditorAccountIdentification": "70000170000002",
                "vrpCreditorAccountName": "VRP creditor",
                "vrpInstructedAmountAmount": "1.00",
                "vrpInstructedAmountCurrency": "GBP",
                "vrpValidFromDateTime": "2026-08-27T00:00:00+00:00",
                "vrpValidToDateTime": "2026-09-27T00:00:00+00:00",
            },
            specification_version="3.1.11",
        ),
    )
    consent_step = next(
        request
        for test_case in compiled.test_cases
        for request in test_case.request_steps
        if request.step_id == "vrp-consent-create-awaiting-authorisation-v31-3111-request"
    )
    funds_step = next(
        request
        for test_case in compiled.test_cases
        for request in test_case.request_steps
        if request.step_id == "vrp-consent-funds-confirmation-request"
    )
    inline_steps = compiled_plan_synthetic_inline_steps(compiled, consent_step)
    produced_token_ids = {step.produces_token_id for step in inline_steps if isinstance(step, ManifestStep)}

    assert funds_step.required_token_id == (
        "vrp-consent-create-awaiting-authorisation-v31-3111-psu-payment-access"  # noqa: S105 - semantic token id
    )
    assert funds_step.required_token_id in produced_token_ids


@pytest.mark.unit
def test_every_non_setup_v311_case_maps_to_a_selected_parity_row() -> None:
    """Prevent execution cases that are not present in the pinned v1.10.0 rows."""
    for api, catalogue in _V311_CATALOGUES.items():
        for test_case in catalogue.test_cases:
            if test_case.role == "setup" or any(
                scope.startswith("legacy-fcs-precondition:") for scope in test_case.compliance_scope
            ):
                continue
            assert _case_script_ids(test_case.compliance_scope, api=api), test_case.test_case_id
