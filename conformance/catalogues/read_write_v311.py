"""Build version-isolated Open Banking Read/Write v3.1.11 catalogues."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import cast

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueDetachedJwsProfile,
    CatalogueKey,
    CatalogueProvenance,
    CataloguePsuAuthorization,
    CatalogueRequestStep,
    CatalogueTestCase,
    EndpointCapability,
    EndpointRef,
    GeneratedRuntimeValue,
    HttpMethod,
    RuntimeInputRequirement,
    TestCatalogue,
)
from conformance.json_types import JsonObject, JsonValue

_STANDARDS_ROOT = Path(__file__).resolve().parents[1] / "standards" / "ob_read_write" / "v3_1_11"
"""Directory containing the pinned v3.1.11 standards and parity artifacts."""

_PARITY_CONTRACT_PATH = _STANDARDS_ROOT / "parity-contract.json"
"""Machine-checkable legacy v1.10.0 parity contract."""

_LEGACY_COMMIT = "1908789b52e26e0a79fc5a565e411f771006c3ce"
"""Immutable legacy FCS commit used for v3.1.11 parity."""

_SCHEMA_FILES = {
    "ais": "account-info-openapi.json",
    "pis": "payment-initiation-openapi.json",
    "cbpii": "confirmation-funds-openapi.json",
    "vrp": "vrp-openapi.json",
}
"""Bundled OpenAPI document filenames keyed by catalogue API."""

_SCHEMA_DOCUMENTS = {
    api: f"ob-read-write-v3.1.11-{filename.removesuffix('.json')}" for api, filename in _SCHEMA_FILES.items()
}
"""Schema-validation document identifiers keyed by catalogue API."""

_API_PATH_PREFIXES = {
    "ais": "/open-banking/v3.1/aisp",
    "pis": "/open-banking/v3.1/pisp",
    "cbpii": "/open-banking/v3.1/cbpii",
    "vrp": "/open-banking/v3.1/pisp",
}
"""Version-correct Open Banking API path prefixes."""

_PIS_ID_REPLACEMENTS = (("pis-v4-", "pis-v31-"),)
"""Case and step identifier replacements for the dedicated v3.1 PIS catalogue."""

_VRP_ID_REPLACEMENTS = (
    ("vrp-consent-create-awaiting-authorisation-v4", "vrp-consent-create-awaiting-authorisation-v31-3111"),
    ("vrp-payment-create-initial-v4", "vrp-payment-create-initial-v31-3111"),
    ("vrp-payment-create-repeated-v4", "vrp-payment-create-repeated-v31-3111"),
)
"""Dependency and placeholder replacements for shared v3.1.11 VRP cases."""

_PIS_V311_STANDING_ORDER_FREQUENCY = RuntimeInputRequirement(
    input_id="pisStandingOrderFrequencyV31",
    input_type="string",
    label="PIS v3.1 standing-order frequency",
    description="v3.1 encoded frequency, for example EvryDay or IntrvlWkDay:01:03.",
)
"""Scalar v3.1 standing-order frequency required by legacy request bodies."""

_AIS_DATE_RANGE_CAPABILITY = "ais.transactions.date-range-filtering"
"""v2-only optional capability that must not suppress pinned legacy AIS rows."""


def build_v311_catalogue(source: TestCatalogue, *, api: str) -> TestCatalogue:
    """Create a v3.1-only catalogue from an existing mixed legacy catalogue.

    Args:
        source: Existing catalogue containing translated v3.1 legacy rows.
        api: Open Banking API family to isolate.

    Returns:
        Dedicated v3.1 catalogue with only v3.1.11-applicable provenance,
        paths, schemas, and cases.

    Raises:
        ValueError: If the API family is unsupported or the transformed
            catalogue contains an unresolved dependency.
    """
    if api not in _SCHEMA_FILES:
        raise ValueError(f"Unsupported v3.1.11 catalogue API: {api}")

    id_replacements = _id_replacements(api)
    selected_cases = tuple(
        _transform_case(test_case, api=api, id_replacements=id_replacements)
        for test_case in source.test_cases
        if _case_applies_to_v311(test_case, api=api)
    )
    capabilities = tuple(_transform_capability(capability, api=api) for capability in source.capabilities)
    if api == "ais":
        selected_cases = _split_ais_transaction_query_variants(selected_cases)
        selected_cases = tuple(_apply_legacy_query_parameters(test_case, api=api) for test_case in selected_cases)
        selected_cases = tuple(_remove_ais_date_range_gate(test_case) for test_case in selected_cases)
    elif api == "pis":
        selected_cases = _split_pis_legacy_operation_variants(selected_cases)
        selected_cases = tuple(_apply_pis_legacy_request_data(test_case) for test_case in selected_cases)
        capabilities = _split_pis_invalid_frequency_capability(capabilities)
    selected_ids = {test_case.test_case_id for test_case in selected_cases}
    for test_case in selected_cases:
        missing_dependencies = set(test_case.dependencies).difference(selected_ids)
        if missing_dependencies:
            missing = ", ".join(sorted(missing_dependencies))
            raise ValueError(f"{api} v3.1.11 case {test_case.test_case_id} has missing dependencies: {missing}")

    return TestCatalogue(
        key=CatalogueKey(standard="open-banking", version="v3.1", api=api),
        catalogue_version=f"{source.catalogue_version}.v3.1.11",
        test_cases=selected_cases,
        capabilities=capabilities,
        configuration_requirements=source.configuration_requirements,
        runtime_capabilities=source.runtime_capabilities,
        provenance=_catalogue_provenance(api),
    )


def _split_ais_transaction_query_variants(
    test_cases: tuple[CatalogueTestCase, ...],
) -> tuple[CatalogueTestCase, ...]:
    """Split grouped account-transaction rows into their exact legacy requests.

    Args:
        test_cases: Initial transformed AIS catalogue cases.

    Returns:
        AIS cases with distinct unfiltered and date-query transaction requests.
    """
    detail_case_id = "ais-at-account-transactions-detail-200"
    detail_case = next(test_case for test_case in test_cases if test_case.test_case_id == detail_case_id)
    variants = tuple(
        _ais_transaction_variant(
            detail_case,
            script_id=script_id,
            test_case_id=test_case_id,
        )
        for script_id, test_case_id in (
            ("OB-301-TRA-105100", detail_case_id),
            ("OB-301-TRA-105110", "ais-at-account-transactions-detail-iso8601-seconds-200"),
            ("OB-301-TRA-105120", "ais-at-account-transactions-detail-iso8601-milliseconds-200"),
        )
    )
    return tuple(
        variant
        for test_case in test_cases
        for variant in (variants if test_case.test_case_id == detail_case_id else (test_case,))
    )


def _ais_transaction_variant(
    source_case: CatalogueTestCase,
    *,
    script_id: str,
    test_case_id: str,
) -> CatalogueTestCase:
    """Create one account-transaction request for a pinned AIS row.

    Args:
        source_case: Grouped detail-permission transaction case.
        script_id: Legacy row script identifier to retain.
        test_case_id: Stable catalogue id for the split case.

    Returns:
        A row-specific case with isolated provenance and assertions.

    Raises:
        ValueError: If the requested legacy row is not represented by the
            source case.
    """
    row = next(
        (
            candidate
            for candidate in _matching_v311_rows(
                source_case,
                request_steps=source_case.request_steps,
                api="ais",
            )
            if candidate["scriptId"] == script_id
        ),
        None,
    )
    if row is None:
        raise ValueError(f"AIS transaction case is missing legacy row {script_id}")
    normalized_row = cast("JsonObject", row["normalizedRow"])
    assertion_ids = _legacy_assertion_ids((row,))
    compliance_scope = tuple(
        f"legacy-fcs-v3.1-ids:{script_id}" if scope.startswith("legacy-fcs-v3.1-ids:") else scope
        for scope in _v311_compliance_scope(
            source_case.compliance_scope,
            allowed_assertion_ids=assertion_ids,
        )
    )
    name = str(normalized_row["description"])
    request_step = replace(
        source_case.request_steps[0],
        step_id=f"{test_case_id}-request",
        name=name,
    )
    return replace(
        source_case,
        test_case_id=test_case_id,
        name=name,
        compliance_scope=compliance_scope,
        request_steps=(request_step,),
        assertions=tuple(
            assertion for assertion in source_case.assertions if assertion.rule.get("rowKey") == row["rowKey"]
        ),
    )


def _apply_legacy_query_parameters(test_case: CatalogueTestCase, *, api: str) -> CatalogueTestCase:
    """Attach exact pinned query parameters to a legacy request case.

    Args:
        test_case: Version-isolated catalogue case.
        api: Open Banking API family owning the case.

    Returns:
        Case whose first request replays the row's ordered query parameters.

    Raises:
        ValueError: If grouped rows require different query strings or a query
            parameter is not string-valued.
    """
    rows = _matching_v311_rows(test_case, request_steps=test_case.request_steps, api=api)
    if not rows:
        return test_case
    queries = [cast("JsonObject", row["normalizedRow"]).get("queryParameters", {}) for row in rows]
    if any(not isinstance(query, dict) for query in queries):
        raise ValueError(f"{api} v3.1.11 case {test_case.test_case_id} has invalid legacy query parameters")
    serialized_queries = {json.dumps(query, sort_keys=True, separators=(",", ":")) for query in queries}
    if len(serialized_queries) != 1:
        raise ValueError(f"{api} v3.1.11 case {test_case.test_case_id} groups distinct legacy query parameters")
    query = cast("JsonObject", queries[0])
    if any(not isinstance(name, str) or not isinstance(value, str) for name, value in query.items()):
        raise ValueError(f"{api} v3.1.11 case {test_case.test_case_id} has non-string legacy query parameters")
    request_step = replace(
        test_case.request_steps[0],
        query_parameters=MappingProxyType(cast("dict[str, str]", dict(query))),
    )
    return replace(test_case, request_steps=(request_step, *test_case.request_steps[1:]))


def _remove_ais_date_range_gate(test_case: CatalogueTestCase) -> CatalogueTestCase:
    """Prevent a v2-only capability from suppressing legacy AIS rows.

    Args:
        test_case: Version-isolated AIS catalogue case.

    Returns:
        Case with the optional date-range capability removed from applicability.
    """
    return replace(
        test_case,
        applicability=replace(
            test_case.applicability,
            required_capability_ids=tuple(
                capability_id
                for capability_id in test_case.applicability.required_capability_ids
                if capability_id != _AIS_DATE_RANGE_CAPABILITY
            ),
        ),
    )


def _v311_runtime_input_requirements(
    test_case: CatalogueTestCase,
    *,
    request_steps: tuple[CatalogueRequestStep, ...],
    api: str,
) -> tuple[RuntimeInputRequirement, ...]:
    """Convert version-sensitive runtime requirements to v3.1 inputs.

    Args:
        test_case: Source catalogue case.
        request_steps: Version-correct request steps for the case.
        api: Open Banking API family owning the case.

    Returns:
        Runtime requirements with the scalar v3.1 standing-order frequency
        replacing the v4 frequency object fields where needed.
    """
    requirements = test_case.runtime_input_requirements
    if api != "pis" or not request_steps:
        return requirements
    body_template = request_steps[0].body_template
    if "${runtime.pisStandingOrderFrequencyV31}" not in str(body_template):
        return tuple(
            requirement
            for requirement in requirements
            if requirement.input_id not in {"pisStandingOrderFrequencyType", "pisStandingOrderFrequencyPointInTime"}
        )
    retained = tuple(
        requirement
        for requirement in requirements
        if requirement.input_id not in {"pisStandingOrderFrequencyType", "pisStandingOrderFrequencyPointInTime"}
    )
    return (*retained, _PIS_V311_STANDING_ORDER_FREQUENCY)


def _v311_body_template(
    request_step: CatalogueRequestStep,
    *,
    api: str,
    path: str,
    body_template: JsonValue,
) -> JsonValue:
    """Convert version-sensitive request bodies to v3.1.11 shapes.

    Args:
        request_step: Source request step used to identify legacy variants.
        api: Open Banking API family owning the request.
        path: Version-correct request path.
        body_template: Request body after identifier replacement.

    Returns:
        v3.1.11 request body, preserving the source value for unaffected
        operations.
    """
    if api != "pis" or "domestic-standing-order" not in path or not isinstance(body_template, dict):
        return body_template
    data = body_template.get("Data")
    if not isinstance(data, dict):
        return body_template
    initiation = data.get("Initiation")
    if not isinstance(initiation, dict):
        return body_template
    mandate = initiation.get("MandateRelatedInformation")
    if not isinstance(mandate, dict):
        return body_template

    v311_initiation = {key: value for key, value in initiation.items() if key != "MandateRelatedInformation"}
    v311_initiation.update(mandate)
    v311_initiation["Frequency"] = (
        "foobar"
        if request_step.step_id.endswith("domestic-standing-order-reject-invalid-frequency-request")
        else "${runtime.pisStandingOrderFrequencyV31}"
    )
    return {
        **body_template,
        "Data": {
            **data,
            "Initiation": v311_initiation,
        },
    }


def _id_replacements(api: str) -> tuple[tuple[str, str], ...]:
    """Return identifier replacements required by one API family.

    Args:
        api: Open Banking API family.

    Returns:
        Ordered old/new identifier replacement pairs.
    """
    if api == "pis":
        return _PIS_ID_REPLACEMENTS
    if api == "vrp":
        return _VRP_ID_REPLACEMENTS
    return ()


def _case_applies_to_v311(test_case: CatalogueTestCase, *, api: str) -> bool:
    """Return whether a mixed-source case represents a v3.1.11 legacy row.

    Args:
        test_case: Source catalogue case to inspect.
        api: Open Banking API family owning the case.

    Returns:
        ``True`` for setup cases without row provenance and cases containing at
        least one legacy row selected by the pinned v1.10.0 version predicate.
    """
    script_ids = _v311_script_ids(test_case, api=api)
    if script_ids:
        return bool(set(script_ids).intersection(_included_script_ids(api)))
    return not _has_versioned_row_provenance(test_case)


def _v311_script_ids(test_case: CatalogueTestCase, *, api: str) -> tuple[str, ...]:
    """Extract v3.1 legacy script identifiers from catalogue traceability.

    Args:
        test_case: Catalogue case whose compliance scope should be inspected.
        api: Open Banking API family owning the case.

    Returns:
        Ordered legacy v3.1 script identifiers, preserving duplicate suffixes
        only in the source scope while normalizing them for ledger lookup.
    """
    if api == "ais":
        for scope in test_case.compliance_scope:
            if scope.startswith("legacy-fcs-v3.1-ids:"):
                value = scope.removeprefix("legacy-fcs-v3.1-ids:")
                return () if value == "none" else tuple(value.split(","))
        return ()

    manifest_name = {
        "pis": "ob_3.1_payment_fca.json",
        "cbpii": "ob_3.1_cbpii_fca.json",
        "vrp": "ob_3.1_variable_recurring_payments.json",
    }[api]
    marker = f"{manifest_name}#"
    script_ids: list[str] = []
    for scope in test_case.compliance_scope:
        if marker not in scope:
            continue
        script_id = scope.split(marker, maxsplit=1)[1].split("(", maxsplit=1)[0]
        script_ids.append(script_id)
    return tuple(script_ids)


def _has_versioned_row_provenance(test_case: CatalogueTestCase) -> bool:
    """Return whether a case names any legacy Read/Write manifest row.

    Args:
        test_case: Catalogue case whose compliance scope should be inspected.

    Returns:
        ``True`` when a scope entry contains a v3.1 or v4.0 row identity.
    """
    return any(
        scope.startswith(("legacy-fcs-v3.1-ids:", "legacy-fcs-v4.0-ids:"))
        or ("#" in scope and ("ob_3.1_" in scope or "ob_4.0_" in scope))
        for scope in test_case.compliance_scope
    )


def _transform_case(
    test_case: CatalogueTestCase,
    *,
    api: str,
    id_replacements: tuple[tuple[str, str], ...],
) -> CatalogueTestCase:
    """Convert one mixed-source catalogue case to its v3.1.11 form.

    Args:
        test_case: Source catalogue case to transform.
        api: Open Banking API family owning the case.
        id_replacements: Identifier substitutions for version-labelled cases.

    Returns:
        Version-correct v3.1.11 catalogue case.
    """
    transformed_steps = tuple(
        _transform_request_step(request_step, api=api, id_replacements=id_replacements)
        for request_step in test_case.request_steps
    )
    transformed_assertions = _transform_assertions(
        test_case,
        request_steps=transformed_steps,
        api=api,
    )
    matching_rows = _matching_v311_rows(test_case, request_steps=transformed_steps, api=api)
    return replace(
        test_case,
        test_case_id=_replace_tokens(test_case.test_case_id, id_replacements),
        compliance_scope=_v311_compliance_scope(
            test_case.compliance_scope,
            allowed_assertion_ids=_legacy_assertion_ids(matching_rows),
        ),
        applicability=replace(
            test_case.applicability,
            endpoint_refs=tuple(
                _transform_endpoint_ref(item, api=api) for item in test_case.applicability.endpoint_refs
            ),
            specification_versions=("3.1.11",),
        ),
        dependencies=tuple(_replace_tokens(item, id_replacements) for item in test_case.dependencies),
        runtime_input_requirements=_v311_runtime_input_requirements(
            test_case,
            request_steps=transformed_steps,
            api=api,
        ),
        request_steps=transformed_steps,
        assertions=transformed_assertions,
        response_signature_required=_case_requires_response_signature(
            test_case,
            request_steps=transformed_steps,
            api=api,
        ),
    )


def _split_pis_legacy_operation_variants(
    test_cases: tuple[CatalogueTestCase, ...],
) -> tuple[CatalogueTestCase, ...]:
    """Split v3.1 PIS rows that the historical mixed catalogue grouped wrongly.

    Args:
        test_cases: Initial transformed PIS catalogue cases.

    Returns:
        PIS cases with scheduled-consent and standing-order-consent rows mapped
        to their exact legacy operations.
    """
    cases_by_id = {test_case.test_case_id: test_case for test_case in test_cases}
    domestic_payment_consent = cases_by_id["pis-v31-domestic-payment-consent-create"]
    scheduled_consent_create = cases_by_id["pis-v31-domestic-scheduled-payment-consent-create"]
    scheduled_consent_read = cases_by_id["pis-v31-domestic-scheduled-payment-consent-read"]
    scheduled_payment_create = cases_by_id["pis-v31-domestic-scheduled-payment-create"]
    scheduled_payment_read = cases_by_id["pis-v31-domestic-scheduled-payment-read"]
    standing_order_consent_create = cases_by_id["pis-v31-domestic-standing-order-consent-create"]
    invalid_standing_order = cases_by_id["pis-v31-domestic-standing-order-reject-invalid-frequency"]

    domestic_payment_without_authorisation = _retarget_pis_case(
        base_case=domestic_payment_consent,
        source_case=domestic_payment_consent,
        script_id="OB-301-DOP-100100",
        test_case_id="pis-v31-domestic-payment-consent-create-without-authorisation",
        name="Create domestic payment consent without PSU authorisation",
        dependencies=(),
    )
    domestic_payment_with_authorisation = _retarget_pis_case(
        base_case=domestic_payment_consent,
        source_case=domestic_payment_consent,
        script_id="OB-301-DOP-100300",
        test_case_id=domestic_payment_consent.test_case_id,
        name=domestic_payment_consent.name,
        dependencies=domestic_payment_consent.dependencies,
        psu_authorization=domestic_payment_consent.request_steps[0].psu_authorization,
    )
    legacy_scheduled_create = _retarget_pis_case(
        base_case=scheduled_consent_create,
        source_case=scheduled_payment_create,
        script_id="OB-301-DOP-101000",
        test_case_id="pis-v31-domestic-scheduled-payment-consent-create-and-authorise",
        name="Create and authorise domestic scheduled payment consent",
        dependencies=(),
        psu_authorization=CataloguePsuAuthorization(
            authorization_step_id="setup-pis-domestic-scheduled-payment-legacy-consent-authorisation",
            authorization_step_name="Authorise legacy domestic scheduled payment consent",
            token_step_id="setup-token-pis-domestic-scheduled-payment-legacy-access",  # noqa: S106 - semantic step id
            token_id="pis-domestic-scheduled-payment-legacy-access",  # noqa: S106 - semantic token id
            flow_label="legacy domestic scheduled payment",
        ),
    )
    legacy_scheduled_read = _retarget_pis_case(
        base_case=scheduled_consent_read,
        source_case=scheduled_payment_read,
        script_id="OB-301-DOP-101100",
        test_case_id="pis-v31-domestic-scheduled-payment-consent-read-after-authorisation",
        name="Read authorised domestic scheduled payment consent",
        dependencies=(legacy_scheduled_create.test_case_id,),
        path_replacements=(
            (
                "pis-v31-domestic-scheduled-payment-consent-create-request",
                legacy_scheduled_create.request_steps[0].step_id,
            ),
        ),
        required_psu_authorization_step_id=legacy_scheduled_create.request_steps[0].step_id,
    )
    invalid_consent = _retarget_pis_case(
        base_case=standing_order_consent_create,
        source_case=invalid_standing_order,
        script_id="OB-301-DOP-1015003",
        test_case_id="pis-v31-domestic-standing-order-consent-reject-invalid-frequency",
        name="Reject domestic standing-order consent with conflicting final-payment fields",
        dependencies=(),
        body_template=_pis_invalid_standing_order_consent_body(invalid_standing_order),
        required_capability_ids=invalid_standing_order.applicability.required_capability_ids,
    )
    scheduled_submission = _rewire_pis_scheduled_submission(
        _drop_pis_script(scheduled_payment_create, "OB-301-DOP-101000"),
        consent_case=legacy_scheduled_create,
        consent_read_case=legacy_scheduled_read,
    )

    replacement_cases = {
        domestic_payment_consent.test_case_id: (
            domestic_payment_without_authorisation,
            domestic_payment_with_authorisation,
        ),
        scheduled_payment_create.test_case_id: (
            legacy_scheduled_create,
            legacy_scheduled_read,
            scheduled_submission,
        ),
        scheduled_payment_read.test_case_id: (),
        invalid_standing_order.test_case_id: (
            _drop_pis_script(invalid_standing_order, "OB-301-DOP-1015003"),
            invalid_consent,
        ),
    }
    return tuple(
        replacement
        for test_case in test_cases
        for replacement in replacement_cases.get(test_case.test_case_id, (test_case,))
    )


def _rewire_pis_scheduled_submission(
    test_case: CatalogueTestCase,
    *,
    consent_case: CatalogueTestCase,
    consent_read_case: CatalogueTestCase,
) -> CatalogueTestCase:
    """Bind the v3.1 scheduled submission to its legacy request-consent flow.

    Args:
        test_case: Scheduled payment submission case for row 101101.
        consent_case: Dedicated row 101000 consent-creation case.
        consent_read_case: Dedicated row 101100 consent-read case.

    Returns:
        Submission case using the captured consent and token from row 101000.
    """
    consent_step = consent_case.request_steps[0]
    request_step = test_case.request_steps[0]
    replacements = (
        (
            "pis-v31-domestic-scheduled-payment-consent-create-request",
            consent_step.step_id,
        ),
    )
    return replace(
        test_case,
        dependencies=(consent_read_case.test_case_id,),
        request_steps=(
            replace(
                request_step,
                body_template=_replace_json_tokens(request_step.body_template, replacements),
                required_token_id=(
                    consent_step.psu_authorization.token_id if consent_step.psu_authorization is not None else None
                ),
                required_psu_authorization_step_id=consent_step.step_id,
            ),
        ),
    )


def _apply_pis_legacy_request_data(test_case: CatalogueTestCase) -> CatalogueTestCase:
    """Replay exact legacy PIS request constants and value propagation.

    Args:
        test_case: Version-isolated PIS catalogue case.

    Returns:
        Case with row-specific fixed values, generated date strategy, and
        submission fields copied from the original consent request.

    Raises:
        ValueError: If a request maps to multiple legacy rows after splitting
            or its pinned parameter/body shape is invalid.
    """
    rows = _matching_v311_rows(test_case, request_steps=test_case.request_steps, api="pis")
    if not rows:
        return test_case
    if len(rows) != 1:
        raise ValueError(f"PIS v3.1.11 case {test_case.test_case_id} still groups multiple legacy rows")

    row = rows[0]
    normalized_row = cast("JsonObject", row["normalizedRow"])
    parameters = normalized_row.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError(f"PIS v3.1.11 row {row['rowKey']} has invalid parameters")

    request_step = test_case.request_steps[0]
    body_template = request_step.body_template
    generated_values = dict(request_step.generated_values)
    end_to_end_identification = parameters.get("endToEndIdentification")
    if isinstance(end_to_end_identification, str) and not end_to_end_identification.startswith("$"):
        if not isinstance(body_template, dict):
            raise ValueError(f"PIS v3.1.11 row {row['rowKey']} is missing its request body")
        data = body_template.get("Data")
        if not isinstance(data, dict):
            raise ValueError(f"PIS v3.1.11 row {row['rowKey']} is missing Data")
        initiation = data.get("Initiation")
        if not isinstance(initiation, dict):
            raise ValueError(f"PIS v3.1.11 row {row['rowKey']} is missing Data.Initiation")
        body_template = {
            **body_template,
            "Data": {
                **data,
                "Initiation": {
                    **initiation,
                    "EndToEndIdentification": end_to_end_identification,
                },
            },
        }
        generated_values.pop("endToEndIdentification", None)

    generated_date_strategies: dict[str, GeneratedRuntimeValue] = {
        "OB-301-DOP-100810": "next-day-date-offset",
        "OB-301-DOP-100820": "next-day-date-utc",
    }
    script_id = str(row["scriptId"])
    if date_strategy := generated_date_strategies.get(script_id):
        generated_values["requestedExecutionDateTime"] = date_strategy

    return replace(
        test_case,
        request_steps=(
            replace(
                request_step,
                body_template=body_template,
                generated_values=MappingProxyType(generated_values),
            ),
            *test_case.request_steps[1:],
        ),
    )


def _split_pis_invalid_frequency_capability(
    capabilities: tuple[EndpointCapability, ...],
) -> tuple[EndpointCapability, ...]:
    """Add the v3.1 consent operation to the invalid-frequency capability.

    Args:
        capabilities: Transformed v3.1 PIS capabilities.

    Returns:
        Capabilities with both legacy invalid-frequency operations represented.
    """
    capability_id = "pis.domestic-standing-order.reject-invalid-frequency-combination"
    return tuple(
        replace(
            capability,
            endpoint_refs=(
                *capability.endpoint_refs,
                EndpointRef(
                    method="POST",
                    path="/open-banking/v3.1/pisp/domestic-standing-order-consents",
                ),
            ),
        )
        if capability.capability_id == capability_id
        else capability
        for capability in capabilities
    )


def _retarget_pis_case(
    *,
    base_case: CatalogueTestCase,
    source_case: CatalogueTestCase,
    script_id: str,
    test_case_id: str,
    name: str,
    dependencies: tuple[str, ...],
    path_replacements: tuple[tuple[str, str], ...] = (),
    body_template: JsonValue | None = None,
    required_capability_ids: tuple[str, ...] | None = None,
    psu_authorization: CataloguePsuAuthorization | None = None,
    required_psu_authorization_step_id: str | None = None,
) -> CatalogueTestCase:
    """Retarget one PIS legacy row to its exact operation.

    Args:
        base_case: Case supplying the correct operation and request shape.
        source_case: Case currently carrying the legacy row provenance.
        script_id: Legacy script id to move.
        test_case_id: Stable id for the new case.
        name: Human-readable case name.
        dependencies: Exact case dependencies for the new operation.
        path_replacements: Placeholder substitutions needed by the cloned path.
        body_template: Optional body override; the base body is used otherwise.
        required_capability_ids: Optional capability override.
        psu_authorization: Optional authorization flow emitted after this step.
        required_psu_authorization_step_id: Consent request that must be
            authorized before this request.

    Returns:
        A dedicated v3.1.11 case for the legacy row.
    """
    base_step = base_case.request_steps[0]
    step = replace(
        base_step,
        step_id=f"{test_case_id}-request",
        name=name,
        path=_replace_tokens(base_step.path, path_replacements),
        body_template=base_step.body_template if body_template is None else body_template,
        psu_authorization=psu_authorization,
        required_psu_authorization_step_id=required_psu_authorization_step_id,
    )
    applicability = base_case.applicability
    if required_capability_ids is not None:
        applicability = replace(applicability, required_capability_ids=required_capability_ids)
    case = replace(
        base_case,
        test_case_id=test_case_id,
        name=name,
        compliance_scope=_pis_scope_for_script(source_case, script_id),
        applicability=applicability,
        dependencies=dependencies,
        request_steps=(step,),
        assertions=tuple(assertion for assertion in source_case.assertions if assertion.kind != "response_schema"),
        response_signature_required=False,
    )
    case = replace(
        case,
        assertions=_transform_assertions(case, request_steps=case.request_steps, api="pis"),
    )
    return replace(
        case,
        response_signature_required=_case_requires_response_signature(
            case,
            request_steps=case.request_steps,
            api="pis",
        ),
    )


def _pis_scope_for_script(test_case: CatalogueTestCase, script_id: str) -> tuple[str, ...]:
    """Select source and assertion provenance for one PIS script.

    Args:
        test_case: Case currently carrying the script.
        script_id: Legacy PIS script id to select.

    Returns:
        Compliance scope containing only the selected script row.
    """
    marker = f"ob_3.1_payment_fca.json#{script_id}"
    base_scope = tuple(
        scope for scope in test_case.compliance_scope if "legacy-fcs-source:" in scope or marker in scope
    )
    rows = cast("list[JsonObject]", _parity_manifests()["pis"]["rows"])
    matching_row = next(row for row in rows if row["scriptId"] == script_id)
    normalized_row = cast("JsonObject", matching_row["normalizedRow"])
    assertion_ids = [
        str(assertion_id)
        for field in ("asserts", "asserts_one_of", "asserts_last_if_all")
        for assertion_id in cast("list[JsonValue]", normalized_row.get(field, []))
    ]
    return (
        *base_scope,
        *(f"legacy-fcs-assertion:{assertion_id}" for assertion_id in assertion_ids),
    )


def _drop_pis_script(test_case: CatalogueTestCase, script_id: str) -> CatalogueTestCase:
    """Remove one misplaced PIS script from a grouped case.

    Args:
        test_case: Catalogue case carrying the misplaced script.
        script_id: Legacy script id to remove.

    Returns:
        Case without the selected script provenance and with exact response
        signature behavior for its remaining rows.
    """
    marker = f"ob_3.1_payment_fca.json#{script_id}"
    compliance_scope = tuple(scope for scope in test_case.compliance_scope if marker not in scope)
    case = replace(test_case, compliance_scope=compliance_scope)
    return replace(
        case,
        response_signature_required=_case_requires_response_signature(
            case,
            request_steps=case.request_steps,
            api="pis",
        ),
    )


def _pis_invalid_standing_order_consent_body(source_case: CatalogueTestCase) -> JsonObject:
    """Build the v3.1 invalid standing-order consent body from its legacy case.

    Args:
        source_case: Existing case containing the invalid initiation template.

    Returns:
        Consent request body without the payment-only consent identifier.

    Raises:
        ValueError: If the source case does not contain the expected body shape.
    """
    body = source_case.request_steps[0].body_template
    if not isinstance(body, dict):
        raise ValueError("Invalid standing-order source case is missing a JSON body")
    data = body.get("Data")
    risk = body.get("Risk")
    if not isinstance(data, dict) or "Initiation" not in data:
        raise ValueError("Invalid standing-order source case is missing Data.Initiation")
    return {
        "Data": {
            "Permission": "Create",
            "Initiation": {
                **cast("JsonObject", data["Initiation"]),
                "Frequency": "${runtime.pisStandingOrderFrequencyV31}",
                "NumberOfPayments": "string",
                "FinalPaymentDateTime": "2027-01-30T14:34:33.083Z",
            },
        },
        "Risk": risk if isinstance(risk, dict) else {},
    }


def _transform_request_step(
    request_step: CatalogueRequestStep,
    *,
    api: str,
    id_replacements: tuple[tuple[str, str], ...],
) -> CatalogueRequestStep:
    """Convert one catalogue request step to v3.1 paths and identifiers.

    Args:
        request_step: Source request step.
        api: Open Banking API family owning the step.
        id_replacements: Identifier substitutions for version-labelled cases.

    Returns:
        Version-correct v3.1 request step.
    """
    path = _replace_tokens(_v311_path(request_step.path), id_replacements)
    body_template = _replace_json_tokens(request_step.body_template, id_replacements)
    return replace(
        request_step,
        step_id=_replace_tokens(request_step.step_id, id_replacements),
        path=path,
        body_template=_v311_body_template(
            request_step,
            api=api,
            path=path,
            body_template=body_template,
        ),
        detached_jws_profile=_v311_detached_jws_profile(request_step, api=api),
        required_token_id=(
            _replace_tokens(request_step.required_token_id, id_replacements)
            if request_step.required_token_id is not None
            else None
        ),
        required_psu_authorization_step_id=(
            _replace_tokens(request_step.required_psu_authorization_step_id, id_replacements)
            if request_step.required_psu_authorization_step_id is not None
            else None
        ),
    )


def _v311_detached_jws_profile(
    request_step: CatalogueRequestStep,
    *,
    api: str,
) -> CatalogueDetachedJwsProfile | None:
    """Return explicit detached-JWS metadata for a v3.1.11 request.

    Args:
        request_step: Source request step.
        api: Open Banking API family owning the step.

    Returns:
        The required signing profile, or ``None`` when the request must remain
        unsigned.
    """
    if api == "ais":
        return "legacy-b64-false" if request_step.step_id == "ais-at-setup-consent-request" else None
    if api == "pis":
        if request_step.step_id.endswith("domestic-payment-consent-reject-invalid-signature-request"):
            return None
        if request_step.method in {"POST", "PUT", "PATCH"} and request_step.body_template is not None:
            return "ob-v3.1.4+"
        return None
    if api == "vrp" and request_step.method in {"POST", "PUT", "PATCH"} and request_step.body_template is not None:
        return "ob-v3.1.4+"
    return None


def _transform_assertions(
    test_case: CatalogueTestCase,
    *,
    request_steps: tuple[CatalogueRequestStep, ...],
    api: str,
) -> tuple[CatalogueAssertion, ...]:
    """Build exact v3.1.11 assertions for one transformed case.

    Args:
        test_case: Source catalogue case.
        request_steps: Version-correct request steps for the case.
        api: Open Banking API family owning the case.

    Returns:
        Assertions with v3.1.11 schema documents and any schema checks required
        by the pinned legacy rows.
    """
    matching_rows = _matching_v311_rows(test_case, request_steps=request_steps, api=api)
    if not matching_rows:
        return tuple(
            _transform_schema_assertion(assertion, api=api) if assertion.kind == "response_schema" else assertion
            for assertion in test_case.assertions
        )
    request_step = request_steps[0]
    return tuple(
        _legacy_fcs_assertion(
            row,
            api=api,
            request_step=request_step,
        )
        for row in matching_rows
    )


def _legacy_fcs_assertion(
    row: JsonObject,
    *,
    api: str,
    request_step: CatalogueRequestStep,
) -> CatalogueAssertion:
    """Build one exact executable assertion bundle from a parity row.

    Args:
        row: Matching row from the pinned parity contract.
        api: Open Banking API family owning the row.
        request_step: Version-correct request used to select response schemas.

    Returns:
        Catalogue assertion preserving legacy all, one-of, and conditional
        assertion ordering.
    """
    normalized_row = cast("JsonObject", row["normalizedRow"])
    resolved_entries = cast("list[JsonObject]", row["resolvedAssertions"])
    resolved = [cast("JsonObject", entry["expect"]) for entry in resolved_entries]
    all_count = len(cast("list[JsonValue]", normalized_row.get("asserts", [])))
    one_of_count = len(cast("list[JsonValue]", normalized_row.get("asserts_one_of", [])))
    last_if_all_count = len(cast("list[JsonValue]", normalized_row.get("asserts_last_if_all", [])))
    all_of = resolved[:all_count]
    one_of = resolved[all_count : all_count + one_of_count]
    last_if_all = resolved[all_count + one_of_count : all_count + one_of_count + last_if_all_count]
    rule: dict[str, JsonValue] = {
        "rowKey": row["rowKey"],
        "allOf": cast("list[JsonValue]", all_of),
        "oneOf": cast("list[JsonValue]", one_of),
        "lastIfAll": cast("list[JsonValue]", last_if_all),
        "legacyAssertionIds": [
            *cast("list[JsonValue]", normalized_row.get("asserts", [])),
            *cast("list[JsonValue]", normalized_row.get("asserts_one_of", [])),
            *cast("list[JsonValue]", normalized_row.get("asserts_last_if_all", [])),
        ],
    }
    if normalized_row.get("schemaCheck") is True:
        rule["schemaDocument"] = _SCHEMA_DOCUMENTS[api]
        rule["schemaRefs"] = _response_schema_refs(
            api=api,
            method=request_step.method,
            path=request_step.path,
        )
    return CatalogueAssertion(
        assertion_id=f"legacy-fcs-row-{row['rowIndex']}",
        kind="legacy_fcs",
        description=f"Execute pinned legacy FCS row {row['rowKey']}.",
        rule=rule,
    )


def _legacy_assertion_ids(rows: tuple[JsonObject, ...]) -> frozenset[str]:
    """Return assertion ids referenced by exact matching parity rows.

    Args:
        rows: Matching parity-contract rows.

    Returns:
        De-duplicated legacy assertion identifiers.
    """
    assertion_ids: set[str] = set()
    for row in rows:
        normalized_row = cast("JsonObject", row["normalizedRow"])
        for field in ("asserts", "asserts_one_of", "asserts_last_if_all"):
            values = normalized_row.get(field, [])
            if isinstance(values, list):
                assertion_ids.update(str(value) for value in values)
    return frozenset(assertion_ids)


def _transform_schema_assertion(assertion: CatalogueAssertion, *, api: str) -> CatalogueAssertion:
    """Retarget an existing response-schema assertion to v3.1.11.

    Args:
        assertion: Existing schema assertion.
        api: Open Banking API family owning the assertion.

    Returns:
        Assertion using the pinned v3.1.11 document identifier.
    """
    return replace(assertion, rule={**assertion.rule, "document": _SCHEMA_DOCUMENTS[api]})


def _case_requires_response_signature(
    test_case: CatalogueTestCase,
    *,
    request_steps: tuple[CatalogueRequestStep, ...],
    api: str,
) -> bool:
    """Return exact legacy response-signature behavior for one case.

    Args:
        test_case: Catalogue case carrying legacy row provenance.
        request_steps: Version-correct request steps for the case.
        api: Open Banking API family owning the case.

    Returns:
        ``True`` when a matching selected legacy row enabled signature
        validation.
    """
    return any(
        cast("JsonObject", row["normalizedRow"]).get("validateSignature") is True
        for row in _matching_v311_rows(test_case, request_steps=request_steps, api=api)
    )


def _matching_v311_rows(
    test_case: CatalogueTestCase,
    *,
    request_steps: tuple[CatalogueRequestStep, ...],
    api: str,
) -> tuple[JsonObject, ...]:
    """Return selected parity rows matching a catalogue case operation.

    Args:
        test_case: Catalogue case carrying legacy row provenance.
        request_steps: Version-correct request steps for the case.
        api: Open Banking API family owning the case.

    Returns:
        Included parity rows with matching script id, method, and URI.
    """
    if not request_steps:
        return ()
    request_step = request_steps[0]
    script_ids = set(_v311_script_ids(test_case, api=api))
    rows = cast("list[JsonObject]", _parity_manifests()[api]["rows"])
    return tuple(
        row
        for row in rows
        if row["includedFor3.1.11"] is True
        and row["scriptId"] in script_ids
        and _row_matches_request(row, request_step=request_step, api=api)
    )


def _row_matches_request(
    row: JsonObject,
    *,
    request_step: CatalogueRequestStep,
    api: str,
) -> bool:
    """Return whether a parity row and catalogue request target one operation.

    Args:
        row: Normalized parity-contract row.
        request_step: Version-correct catalogue request step.
        api: Open Banking API family owning the operation.

    Returns:
        ``True`` when method and normalized path match, including the approved
        correction for the legacy singular AIS product path.
    """
    normalized_row = cast("JsonObject", row["normalizedRow"])
    if normalized_row.get("method") != request_step.method:
        return False
    row_path = f"{_API_PATH_PREFIXES[api]}{normalized_row['uri']}"
    if row["rowKey"] == "ob_3.1_accounts_transactions_fca.json#63:OB-301-PRO-103403":
        row_path = "/open-banking/v3.1/aisp/products"
    return _normalized_operation_path(str(row_path)) == _normalized_operation_path(request_step.path)


def _normalized_operation_path(path: str) -> str:
    """Normalize path-template variables for operation matching.

    Args:
        path: Legacy or catalogue operation path.

    Returns:
        Path with legacy, OpenAPI, and runtime-captured variables represented
        by a common marker.
    """
    normalized = re.sub(r"\$\{[^}]+\}", "{value}", path)
    normalized = re.sub(r"\{[^}]+\}", "{value}", normalized)
    return re.sub(r"\$[A-Za-z0-9_-]+", "{value}", normalized)


def _transform_capability(capability: EndpointCapability, *, api: str) -> EndpointCapability:
    """Convert capability endpoint ownership to v3.1 paths.

    Args:
        capability: Source endpoint capability.
        api: Open Banking API family owning the capability.

    Returns:
        Capability with version-correct endpoint references and description.
    """
    return replace(
        capability,
        description=capability.description.replace("/open-banking/v4.0/", "/open-banking/v3.1/"),
        endpoint_refs=tuple(_transform_endpoint_ref(item, api=api) for item in capability.endpoint_refs),
    )


def _transform_endpoint_ref(endpoint_ref: EndpointRef, *, api: str) -> EndpointRef:
    """Convert an endpoint reference to its v3.1 standards path.

    Args:
        endpoint_ref: Source endpoint reference.
        api: Open Banking API family owning the endpoint.

    Returns:
        Endpoint reference under the v3.1 API root.
    """
    path = _v311_path(endpoint_ref.path)
    if api == "vrp" and path.startswith(("/domestic-vrp-consents", "/domestic-vrps")):
        path = f"/open-banking/v3.1/pisp{path}"
    return replace(endpoint_ref, path=path)


def _v311_path(path: str) -> str:
    """Convert a v4 Read/Write path to the corresponding v3.1 path.

    Args:
        path: Source standards or request path.

    Returns:
        Path with only the Open Banking major-version segment changed.
    """
    return path.replace("/open-banking/v4.0/", "/open-banking/v3.1/")


def _v311_compliance_scope(
    scopes: tuple[str, ...],
    *,
    allowed_assertion_ids: frozenset[str],
) -> tuple[str, ...]:
    """Keep only pinned v3.1 provenance in a compliance-scope tuple.

    Args:
        scopes: Mixed legacy compliance-scope entries.
        allowed_assertion_ids: Assertion ids referenced by matching v3.1 rows.

    Returns:
        Scope entries with v4 provenance removed and mutable branch references
        replaced by the pinned legacy commit.
    """
    retained: list[str] = []
    for scope in scopes:
        if "ob_4.0_" in scope or "cVRP_4.0_" in scope or scope.startswith("legacy-fcs-v4.0-ids:"):
            continue
        assertion_scope = _filtered_assertion_scope(scope, allowed_assertion_ids=allowed_assertion_ids)
        if assertion_scope is None:
            continue
        scope = assertion_scope
        retained.append(scope.replace("@develop/", f"@{_LEGACY_COMMIT}/"))
    return tuple(retained)


def _filtered_assertion_scope(scope: str, *, allowed_assertion_ids: frozenset[str]) -> str | None:
    """Filter one mixed assertion-provenance scope to v3.1 ids.

    Args:
        scope: Compliance-scope entry to filter.
        allowed_assertion_ids: Assertion ids referenced by matching v3.1 rows.

    Returns:
        Filtered scope, the unchanged non-assertion scope, or ``None`` when no
        assertion ids remain.
    """
    prefixes = {
        "legacy-fcs-assertions:": ",",
        "legacy-fcs-assertion:": ",",
        "legacy_asserts:": "|",
        "legacy-assert:": "|",
    }
    for prefix, separator in prefixes.items():
        if not scope.startswith(prefix):
            continue
        assertion_ids = [
            assertion_id
            for assertion_id in scope.removeprefix(prefix).split(separator)
            if assertion_id in allowed_assertion_ids
        ]
        if not assertion_ids:
            return None
        return f"{prefix}{separator.join(assertion_ids)}"
    return scope


def _replace_tokens(value: str, replacements: tuple[tuple[str, str], ...]) -> str:
    """Apply ordered literal identifier replacements to a string.

    Args:
        value: Source string.
        replacements: Ordered old/new literal pairs.

    Returns:
        String after all replacements have been applied.
    """
    transformed = value
    for old, new in replacements:
        transformed = transformed.replace(old, new)
    return transformed


def _replace_json_tokens(value: JsonValue, replacements: tuple[tuple[str, str], ...]) -> JsonValue:
    """Apply identifier replacements recursively to JSON string values.

    Args:
        value: JSON-compatible source value.
        replacements: Ordered old/new literal pairs.

    Returns:
        JSON value with replacements applied while preserving array order.
    """
    if isinstance(value, str):
        return _replace_tokens(value, replacements)
    if isinstance(value, list):
        return [_replace_json_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_json_tokens(item, replacements) for key, item in value.items()}
    return value


@cache
def _parity_manifests() -> dict[str, JsonObject]:
    """Load normalized manifest entries from the pinned parity contract.

    Returns:
        Manifest entries keyed by API family.
    """
    contract = json.loads(_PARITY_CONTRACT_PATH.read_text(encoding="utf-8"))
    return {str(item["api"]): cast("JsonObject", item) for item in contract["manifests"]}


@cache
def _included_script_ids(api: str) -> frozenset[str]:
    """Return script ids selected for a v3.1.11 run.

    Args:
        api: Open Banking API family.

    Returns:
        Included legacy script ids from the parity contract.
    """
    rows = cast("list[JsonObject]", _parity_manifests()[api]["rows"])
    return frozenset(str(row["scriptId"]) for row in rows if row["includedFor3.1.11"] is True)


@cache
def _openapi_document(api: str) -> JsonObject:
    """Load a pinned v3.1.11 OpenAPI document.

    Args:
        api: Open Banking API family.

    Returns:
        Parsed OpenAPI document.
    """
    return cast("JsonObject", json.loads((_STANDARDS_ROOT / _SCHEMA_FILES[api]).read_text(encoding="utf-8")))


def _response_schema_ref(*, api: str, method: HttpMethod, path: str, status: int) -> str | None:
    """Resolve a response schema reference for a v3.1.11 operation.

    Args:
        api: Open Banking API family.
        method: HTTP operation method.
        path: Versioned executable request path.
        status: Expected HTTP response status.

    Returns:
        Local component schema reference when the response has a JSON schema,
        otherwise ``None``.
    """
    document = _openapi_document(api)
    operation_path = _match_openapi_path(api=api, request_path=path)
    paths = cast("JsonObject", document["paths"])
    operation = cast("JsonObject", cast("JsonObject", paths[operation_path])[method.lower()])
    responses = cast("JsonObject", operation["responses"])
    response = responses.get(str(status))
    if not isinstance(response, dict):
        return None
    resolved_response = _resolve_local_ref(document, response)
    content = resolved_response.get("content")
    if not isinstance(content, dict):
        return None
    for media_type in ("application/json", "application/json; charset=utf-8", "application/jose+jwe"):
        media = content.get(media_type)
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
            return str(schema["$ref"])
    return None


def _response_schema_refs(*, api: str, method: HttpMethod, path: str) -> JsonObject:
    """Return all JSON response schemas declared for one operation.

    Args:
        api: Open Banking API family.
        method: HTTP operation method.
        path: Versioned executable request path.

    Returns:
        Mapping from HTTP status strings to local component schema references.
    """
    document = _openapi_document(api)
    operation_path = _match_openapi_path(api=api, request_path=path)
    paths = cast("JsonObject", document["paths"])
    operation = cast("JsonObject", cast("JsonObject", paths[operation_path])[method.lower()])
    responses = cast("JsonObject", operation["responses"])
    schema_refs: JsonObject = {}
    for raw_status in responses:
        if not raw_status.isdigit():
            continue
        schema_ref = _response_schema_ref(
            api=api,
            method=method,
            path=path,
            status=int(raw_status),
        )
        if schema_ref is not None:
            schema_refs[raw_status] = schema_ref
    return schema_refs


def _match_openapi_path(*, api: str, request_path: str) -> str:
    """Match an executable path to a path template in a bundled OpenAPI file.

    Args:
        api: Open Banking API family.
        request_path: Versioned request path, possibly containing captured
            execution placeholders.

    Returns:
        Matching OpenAPI path template.

    Raises:
        ValueError: If no unique OpenAPI operation path matches.
    """
    relative_path = request_path.removeprefix(_API_PATH_PREFIXES[api])
    request_segments = relative_path.strip("/").split("/")
    candidates = []
    for candidate in cast("JsonObject", _openapi_document(api)["paths"]):
        candidate_segments = candidate.strip("/").split("/")
        if len(candidate_segments) != len(request_segments):
            continue
        if all(
            source == target or _is_path_variable(source) or _is_path_variable(target)
            for source, target in zip(request_segments, candidate_segments, strict=True)
        ):
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError(f"Expected one v3.1.11 OpenAPI path for {request_path!r}; found {candidates}")
    return candidates[0]


def _is_path_variable(segment: str) -> bool:
    """Return whether a path segment represents a template variable.

    Args:
        segment: One path segment.

    Returns:
        ``True`` for OpenAPI braces or runtime step placeholders.
    """
    return (segment.startswith("{") and segment.endswith("}")) or (
        segment.startswith("${steps.") and segment.endswith("}")
    )


def _resolve_local_ref(document: JsonObject, value: JsonObject) -> JsonObject:
    """Resolve a chain of local OpenAPI references.

    Args:
        document: Root OpenAPI document.
        value: Object that may contain a sole local ``$ref``.

    Returns:
        Referenced object or the original object when it is inline.

    Raises:
        ValueError: If a reference is external or resolves to a non-object.
    """
    current = value
    visited: set[str] = set()
    while set(current) == {"$ref"}:
        reference = current["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/") or reference in visited:
            raise ValueError(f"Unsupported OpenAPI response reference: {reference!r}")
        visited.add(reference)
        resolved: JsonValue = document
        for raw_segment in reference[2:].split("/"):
            if not isinstance(resolved, dict):
                raise ValueError(f"OpenAPI response reference does not resolve to an object: {reference}")
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            resolved = resolved[segment]
        if not isinstance(resolved, dict):
            raise ValueError(f"OpenAPI response reference does not resolve to an object: {reference}")
        current = resolved
    return current


def _catalogue_provenance(api: str) -> CatalogueProvenance:
    """Build pinned v3.1.11 catalogue provenance.

    Args:
        api: Open Banking API family.

    Returns:
        Immutable source provenance for result and certification evidence.
    """
    manifest_file = str(_parity_manifests()[api]["file"])
    return CatalogueProvenance(
        repository="OpenBankingUK/conformance-suite",
        release="v1.10.0",
        commit=_LEGACY_COMMIT,
        source_paths=(
            f"manifests/{manifest_file}",
            "manifests/assertions.json",
            "manifests/data.json",
            f"OpenBankingUK/read-write-api-specs@v3.1.11/dist/openapi/{_SCHEMA_FILES[api]}",
        ),
        references=MappingProxyType(
            {
                "legacyRelease": "https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.10.0",
                "normativeSchemas": "https://github.com/OpenBankingUK/read-write-api-specs/tree/v3.1.11",
                "parityContract": "conformance/standards/ob_read_write/v3_1_11/parity-contract.json",
            }
        ),
    )


__all__ = ["build_v311_catalogue"]
