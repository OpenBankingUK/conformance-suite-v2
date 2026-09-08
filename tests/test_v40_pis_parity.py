"""Regression guards for Open Banking Read/Write v4 PIS strict parity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import cast

import pytest

from conformance.assertions import evaluate_assertion
from conformance.catalogue import CatalogueTestCase, ImplementedEndpoint, TestPlanSpec, compile_test_plan
from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE
from conformance.executor import _catalogue_assertion_to_manifest_assertion
from conformance.json_types import JsonObject

_STANDARDS_ROOT = Path(__file__).resolve().parents[1] / "conformance" / "standards" / "ob_read_write" / "v4_0"
"""Repository path containing pinned v4 standards and legacy sources."""

_LEGACY_ROOT = _STANDARDS_ROOT / "legacy"
"""Repository path containing pinned legacy FCS v4 PIS inputs."""

_LEGACY_COMMIT = "1908789b52e26e0a79fc5a565e411f771006c3ce"
"""Pinned legacy FCS v1.10.0 source commit."""

_EXPECTED_HASHES = {
    "assertions.json": "c74b52a40917f310601c10bef86d78cb5f93b1a8b0b530fc07a0e8f5d2c9319a",
    "data.json": "7ecd1b801ad9dd38bbb74540c0aea6036cdda8883b0c1236341dc9b7daac7a10",
    "ob_4.0_payment_fca.json": "fabd4080b6d79affe7b6ac390597c6bca546c5d462ad27e4989581cc863dca4a",
}
"""SHA-256 digests for immutable legacy PIS source files."""

_RUNTIME_INPUTS: dict[str, str] = {
    "resourceBaseUrl": "https://rs.example.com",
    "pisCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
    "pisCreditorAccountIdentification": "70000170000002",
    "pisCreditorAccountName": "Creditor",
    "pisInternationalCreditorAccountSchemeName": "UK.OBIE.SortCodeAccountNumber",
    "pisInternationalCreditorAccountIdentification": "70000170000003",
    "pisInternationalCreditorAccountName": "International creditor",
    "pisInstructedAmountAmount": "1.00",
    "pisInstructedAmountCurrency": "GBP",
    "pisCurrencyOfTransfer": "USD",
    "pisRequestedExecutionDateTime": "2026-12-01T00:00:00+00:00",
    "pisFirstPaymentDateTime": "2026-12-01T00:00:00+00:00",
    "pisStandingOrderFrequencyType": "WEEK",
    "pisStandingOrderFrequencyPointInTime": "03",
}
"""Complete non-sensitive runtime inputs for full v4 PIS compilation."""


def _legacy_rows() -> list[JsonObject]:
    """Load the pinned v4 PIS manifest rows.

    Returns:
        Legacy manifest rows in execution order.
    """
    document = cast(
        "JsonObject",
        json.loads((_LEGACY_ROOT / "ob_4.0_payment_fca.json").read_text(encoding="utf-8")),
    )
    return cast("list[JsonObject]", document["scripts"])


def _legacy_assertions() -> JsonObject:
    """Load the pinned legacy assertion definitions.

    Returns:
        Assertion definitions keyed by legacy assertion id.
    """
    document = cast(
        "JsonObject",
        json.loads((_LEGACY_ROOT / "assertions.json").read_text(encoding="utf-8")),
    )
    return cast("JsonObject", document["references"])


def _script_id(test_case: CatalogueTestCase) -> str:
    """Return the single v4 legacy script represented by a strict case.

    Args:
        test_case: Strict v4 PIS catalogue case.

    Returns:
        Legacy script identifier.
    """
    prefix = "legacy-fcs-script:ob_4.0_payment_fca.json#"
    script_ids = [scope.removeprefix(prefix) for scope in test_case.compliance_scope if scope.startswith(prefix)]
    assert len(script_ids) == 1
    return script_ids[0]


def _case_by_script() -> dict[str, CatalogueTestCase]:
    """Index strict v4 PIS cases by legacy script id.

    Returns:
        Catalogue cases keyed by their unique legacy script id.
    """
    return {_script_id(test_case): test_case for test_case in PIS_PAYMENT_CATALOGUE.test_cases}


def _normalized_operation_path(path: str) -> str:
    """Normalize legacy and runtime path variables.

    Args:
        path: Legacy or executable request path.

    Returns:
        Path with every variable syntax represented by ``{value}``.
    """
    normalized = re.sub(r"\$\{[^}]+\}", "{value}", path)
    normalized = re.sub(r"\{[^}]+\}", "{value}", normalized)
    return re.sub(r"\$[A-Za-z0-9_-]+", "{value}", normalized)


def _assertion_ids(row: JsonObject, field: str) -> list[str]:
    """Read one ordered legacy assertion-id group.

    Args:
        row: Legacy PIS manifest row.
        field: Assertion group field name.

    Returns:
        Assertion identifiers in source order.
    """
    return cast("list[str]", row.get(field, []))


def _initiation(test_case: CatalogueTestCase) -> JsonObject:
    """Return a case's request ``Data.Initiation`` object.

    Args:
        test_case: PIS case with a JSON request body.

    Returns:
        Initiation object from the request template.
    """
    body = test_case.request_steps[0].body_template
    assert isinstance(body, dict)
    data = body["Data"]
    assert isinstance(data, dict)
    return cast("JsonObject", data["Initiation"])


@pytest.mark.unit
def test_v40_pis_sources_are_pinned_to_legacy_release() -> None:
    """Pin source hashes and immutable catalogue provenance."""
    for filename, expected_digest in _EXPECTED_HASHES.items():
        digest = hashlib.sha256((_LEGACY_ROOT / filename).read_bytes()).hexdigest()
        assert digest == expected_digest

    provenance = PIS_PAYMENT_CATALOGUE.provenance
    assert provenance is not None
    assert provenance.release == "v1.10.0"
    assert provenance.commit == _LEGACY_COMMIT
    assert provenance.source_paths == (
        "manifests/ob_4.0_payment_fca.json",
        "manifests/assertions.json",
        "manifests/data.json",
        "OpenBankingUK/read-write-api-specs@v4.0-Update-5/dist/openapi/payment-initiation-openapi.json",
    )


@pytest.mark.unit
def test_v40_pis_catalogue_executes_every_legacy_row_exactly_once() -> None:
    """Match every legacy row's operation, assertions, schema, and signature flag."""
    rows = _legacy_rows()
    definitions = _legacy_assertions()
    cases = PIS_PAYMENT_CATALOGUE.test_cases

    assert len(rows) == 29
    assert len({str(row["id"]) for row in rows}) == 29
    assert sum(row.get("schemaCheck") is True for row in rows) == 28
    assert sum(row.get("validateSignature") is True for row in rows) == 20
    assert len(cases) == len(rows)
    assert [_script_id(test_case) for test_case in cases] == [row["id"] for row in rows]

    for row_index, (row, test_case) in enumerate(zip(rows, cases, strict=True)):
        request_step = test_case.request_steps[0]
        assert request_step.method == str(row["method"]).upper()
        assert _normalized_operation_path(request_step.path) == _normalized_operation_path(
            f"/open-banking/v4.0/pisp{row['uri']}"
        )
        assert test_case.response_signature_required is (row.get("validateSignature") is True)
        assert len(test_case.assertions) == 1
        assertion = test_case.assertions[0]
        assert assertion.kind == "legacy_fcs"
        assert assertion.rule["rowKey"] == f"ob_4.0_payment_fca.json#{row_index}:{row['id']}"

        all_ids = _assertion_ids(row, "asserts")
        one_of_ids = _assertion_ids(row, "asserts_one_of")
        last_if_all_ids = _assertion_ids(row, "asserts_last_if_all")
        assert assertion.rule["allOf"] == [
            cast("JsonObject", definitions[assertion_id])["expect"] for assertion_id in all_ids
        ]
        assert assertion.rule["oneOf"] == [
            cast("JsonObject", definitions[assertion_id])["expect"] for assertion_id in one_of_ids
        ]
        assert assertion.rule["lastIfAll"] == [
            cast("JsonObject", definitions[assertion_id])["expect"] for assertion_id in last_if_all_ids
        ]
        if row.get("schemaCheck") is True:
            assert assertion.rule["schemaDocument"] == "ob-read-write-v4.0-payment-initiation-openapi"
            statuses = {
                status
                for assertion_id in (*all_ids, *one_of_ids, *last_if_all_ids)
                if isinstance(
                    status := cast("JsonObject", cast("JsonObject", definitions[assertion_id])["expect"]).get(
                        "status-code"
                    ),
                    int,
                )
            }
            assert set(cast("JsonObject", assertion.rule["schemaRefs"])) == {
                str(status) for status in statuses if status != 204
            }
        else:
            assert "schemaDocument" not in assertion.rule
            assert "schemaRefs" not in assertion.rule


@pytest.mark.unit
def test_v40_pis_full_selection_compiles_all_strict_parity_rows() -> None:
    """Compile all 29 rows and all 20 legacy response-signature checks."""
    endpoint_refs = tuple(
        dict.fromkeys(
            endpoint_ref
            for test_case in PIS_PAYMENT_CATALOGUE.test_cases
            for endpoint_ref in test_case.applicability.endpoint_refs
        )
    )
    capability_ids_by_endpoint = {
        endpoint_ref: tuple(
            capability.capability_id
            for capability in PIS_PAYMENT_CATALOGUE.capabilities
            if endpoint_ref in capability.endpoint_refs
        )
        for endpoint_ref in endpoint_refs
    }
    plan = compile_test_plan(
        PIS_PAYMENT_CATALOGUE,
        TestPlanSpec(
            schema_version="v1",
            catalogue_key=PIS_PAYMENT_CATALOGUE.key,
            security_profile="fapi1-advanced",
            implemented_endpoints=tuple(
                ImplementedEndpoint(
                    method=endpoint_ref.method,
                    path=endpoint_ref.path,
                    resource_group="PIS",
                    capability_ids=capability_ids_by_endpoint[endpoint_ref],
                )
                for endpoint_ref in endpoint_refs
            ),
            runtime_inputs=_RUNTIME_INPUTS,
            specification_version="4.0.1",
        ),
    )

    assert len(plan.test_cases) == 29
    assert sum(test_case.response_signature_required for test_case in plan.test_cases) == 20
    assert (
        sum(assertion.kind == "legacy_fcs" for test_case in plan.test_cases for assertion in test_case.assertions) == 29
    )
    assert [_script_id(test_case) for test_case in plan.test_cases] == [row["id"] for row in _legacy_rows()]


@pytest.mark.unit
def test_v40_pis_negative_signature_rows_execute_exact_error_contracts() -> None:
    """Enforce v4 error-code assertions for malformed and missing detached JWS."""
    cases = _case_by_script()
    missing_claim = cases["OB-400-DOP-100110"]
    missing_signature = cases["OB-316-DOP-100310"]

    assert missing_claim.request_steps[0].detached_jws_omit_claims == ("iss",)
    assert missing_claim.assertions[0].rule["oneOf"] == [
        {
            "matches": [
                {
                    "JSON": 'Errors.#[ErrorCode="U016"].ErrorCode',
                    "Value": "U016",
                    "detail": "Expected a specific error code for invalid claim in signature error.",
                }
            ]
        },
        {
            "matches": [
                {
                    "JSON": 'Errors.#[ErrorCode="U017"].ErrorCode',
                    "Value": "U017",
                    "detail": "Expected a specific error code for missing claim in signature error.",
                }
            ]
        },
        {
            "matches": [
                {
                    "JSON": 'Errors.#[ErrorCode="U018"].ErrorCode',
                    "Value": "U018",
                    "detail": "Expected a specific error code for malformed signature error.",
                }
            ]
        },
    ]
    missing_signature_all = cast("list[JsonObject]", missing_signature.assertions[0].rule["allOf"])
    assert missing_signature_all[1] == {
        "matches": [
            {
                "JSON": 'Errors.#[ErrorCode="U019"].ErrorCode',
                "Value": "U019",
                "detail": "Expected a specific error code for missing signature.",
            }
        ]
    }


@pytest.mark.unit
def test_v40_pis_missing_iss_rejects_generic_unexpected_error() -> None:
    """Fail the missing-iss row unless Ozone returns a permitted v4 code."""
    catalogue_assertion = _case_by_script()["OB-400-DOP-100110"].assertions[0]
    manifest_assertion = _catalogue_assertion_to_manifest_assertion(
        catalogue_assertion,
        runtime_inputs={},
        generated_header_values={},
    )

    unexpected_error = evaluate_assertion(
        manifest_assertion,
        status_code=400,
        body={
            "Code": "UK.OBIE.UnexpectedError",
            "Errors": [{"ErrorCode": "UK.OBIE.UnexpectedError"}],
        },
    )
    missing_claim = evaluate_assertion(
        manifest_assertion,
        status_code=400,
        body={
            "Code": "U017",
            "Errors": [{"ErrorCode": "U017"}],
        },
    )

    assert unexpected_error.passed is False
    assert missing_claim.passed is True


@pytest.mark.unit
def test_v40_pis_replays_fixed_identifiers_dates_and_distinct_consent_flows() -> None:
    """Preserve legacy request constants, date macros, and PSU flow boundaries."""
    cases = _case_by_script()
    expected_end_to_end_ids = {
        "OB-400-DOP-100100": "e2e-domestic-pay",
        "OB-400-DOP-100300": "e2e-domestic-pay",
        "OB-400-DOP-100600": "e2e-domestic-pay",
        "OB-400-DOP-100800": "e2e-domestic-sched-pay",
        "OB-400-DOP-100810": "e2e-domestic-sched-pay-v1",
        "OB-400-DOP-100820": "e2e-domestic-sched-pay-v2",
        "OB-400-DOP-101000": "e2e-domestic-sched-pay",
        "OB-400-DOP-101101": "e2e-domestic-sched-pay",
        "OB-400-DOP-101600": "e2e-internat-pay",
        "OB-400-DOP-101800": "e2e-internat-pay",
        "OB-400-DOP-102000": "e2e-internat-sched-pay",
        "OB-400-DOP-102200": "e2e-internat-sched-pay",
    }
    for script_id, expected_value in expected_end_to_end_ids.items():
        test_case = cases[script_id]
        assert _initiation(test_case)["EndToEndIdentification"] == expected_value
        assert "endToEndIdentification" not in test_case.request_steps[0].generated_values

    offset_step = cases["OB-400-DOP-100810"].request_steps[0]
    utc_step = cases["OB-400-DOP-100820"].request_steps[0]
    assert offset_step.generated_values["requestedExecutionDateTime"] == "next-day-date-offset"
    assert utc_step.generated_values["requestedExecutionDateTime"] == "next-day-date-utc"

    standalone_domestic = cases["OB-400-DOP-100100"].request_steps[0]
    authorising_domestic = cases["OB-400-DOP-100300"].request_steps[0]
    first_scheduled = cases["OB-400-DOP-100800"].request_steps[0]
    second_scheduled = cases["OB-400-DOP-101000"].request_steps[0]
    assert standalone_domestic.psu_authorization is None
    assert authorising_domestic.psu_authorization is not None
    assert first_scheduled.psu_authorization is not None
    assert second_scheduled.psu_authorization is not None
    assert first_scheduled.psu_authorization.authorization_step_id != (
        second_scheduled.psu_authorization.authorization_step_id
    )

    second_scheduled_read = cases["OB-400-DOP-101100"]
    second_scheduled_submission = cases["OB-400-DOP-101101"]
    assert second_scheduled_read.dependencies == ("pis-v4-domestic-scheduled-payment-consent-create-and-authorise",)
    assert second_scheduled_submission.dependencies == (
        "pis-v4-domestic-scheduled-payment-consent-read-after-authorisation",
    )
    assert "pis-v4-domestic-scheduled-payment-consent-create-and-authorise-request" in (
        second_scheduled_read.request_steps[0].path
    )
    assert "pis-v4-domestic-scheduled-payment-consent-create-and-authorise-request" in json.dumps(
        second_scheduled_submission.request_steps[0].body_template
    )


@pytest.mark.unit
def test_v40_pis_replays_distinct_invalid_standing_order_payloads() -> None:
    """Keep the two legacy v4 invalid-frequency request shapes distinct."""
    cases = _case_by_script()
    invalid_submission = _initiation(cases["OB-400-DOP-101400"])
    invalid_consent = _initiation(cases["OB-400-DOP-101503"])

    submission_mandate = cast("JsonObject", invalid_submission["MandateRelatedInformation"])
    consent_mandate = cast("JsonObject", invalid_consent["MandateRelatedInformation"])
    assert submission_mandate["Frequency"] == {"Type": "foobar"}
    assert consent_mandate["Frequency"] == {
        "Type": "WEEK",
        "CountPerPeriod": 1,
        "PointInTime": "03",
    }
