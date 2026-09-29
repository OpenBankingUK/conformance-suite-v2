"""Build strict legacy-FCS parity for Open Banking Read/Write v4 PIS."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import cast

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueProvenance,
    CataloguePsuAuthorization,
    CatalogueRequestStep,
    CatalogueTestCase,
    GeneratedRuntimeValue,
    HttpMethod,
    TestCatalogue,
)
from conformance.json_types import JsonObject, JsonValue

_STANDARDS_ROOT = Path(__file__).resolve().parents[1] / "standards" / "ob_read_write" / "v4_0"
"""Directory containing the pinned v4 standards and legacy PIS sources."""

_LEGACY_ROOT = _STANDARDS_ROOT / "legacy"
"""Directory containing immutable legacy FCS v1.10.0 PIS inputs."""

_LEGACY_MANIFEST_PATH = _LEGACY_ROOT / "ob_4.0_payment_fca.json"
"""Pinned legacy v4 PIS manifest."""

_LEGACY_ASSERTIONS_PATH = _LEGACY_ROOT / "assertions.json"
"""Pinned legacy assertion definitions used by the v4 PIS manifest."""

_LEGACY_COMMIT = "1908789b52e26e0a79fc5a565e411f771006c3ce"
"""Immutable legacy FCS v1.10.0 commit used for strict parity."""

_API_PATH_PREFIX = "/open-banking/v4.0/pisp"
"""Versioned PIS resource path prefix."""

_SCHEMA_DOCUMENT = "ob-read-write-v4.0-payment-initiation-openapi"
"""Bundled v4 Payment Initiation OpenAPI document identifier."""

_SPECIFICATION_VERSIONS = ("4.0", "4.0.0", "4.0.1")
"""Participant-facing versions served by the v4 PIS catalogue."""

_CASE_ID_BY_SCRIPT = {
    "OB-400-DOP-100100": "pis-v4-domestic-payment-consent-create-without-authorisation",
    "OB-400-DOP-100300": "pis-v4-domestic-payment-consent-create",
    "OB-400-DOP-101000": "pis-v4-domestic-scheduled-payment-consent-create-and-authorise",
    "OB-400-DOP-101100": "pis-v4-domestic-scheduled-payment-consent-read-after-authorisation",
}
"""Case ids needed to split legacy rows previously grouped in the mixed catalogue."""

_BASE_CASE_ID_BY_SCRIPT = {
    "OB-400-DOP-101100": "pis-v4-domestic-scheduled-payment-consent-read",
}
"""Correct-operation base cases for legacy rows previously attached to the wrong case."""

_DEPENDENCIES_BY_SCRIPT = {
    "OB-400-DOP-100400": ("pis-v4-domestic-payment-consent-create",),
    "OB-400-DOP-100500": ("pis-v4-domestic-payment-consent-read-authorised",),
    "OB-400-DOP-100600": ("pis-v4-domestic-payment-consent-read-authorised",),
    "OB-400-DOP-100700": ("pis-v4-domestic-payment-create",),
    "OB-400-DOP-100900": ("pis-v4-domestic-scheduled-payment-consent-create",),
    "OB-400-DOP-101100": ("pis-v4-domestic-scheduled-payment-consent-create-and-authorise",),
    "OB-400-DOP-101101": ("pis-v4-domestic-scheduled-payment-consent-read-after-authorisation",),
    "OB-400-DOP-101300": ("pis-v4-domestic-standing-order-consent-create",),
    "OB-400-DOP-101400": ("pis-v4-domestic-standing-order-consent-read",),
    "OB-400-DOP-101401": ("pis-v4-domestic-standing-order-consent-read",),
    "OB-400-DOP-101500": ("pis-v4-domestic-standing-order-create",),
    "OB-400-DOP-101700": ("pis-v4-international-payment-consent-create",),
    "OB-400-DOP-101800": ("pis-v4-international-payment-consent-read",),
    "OB-400-DOP-101900": ("pis-v4-international-payment-create",),
    "OB-400-DOP-102100": ("pis-v4-international-scheduled-payment-consent-create",),
    "OB-400-DOP-102200": ("pis-v4-international-scheduled-payment-consent-read",),
    "OB-400-DOP-102300": ("pis-v4-international-scheduled-payment-create",),
}
"""Exact dependency graph implied by legacy captured-value and consent flows."""

_AUTHORIZATION_CONSENT_CASE_BY_SCRIPT = {
    "OB-400-DOP-100400": "pis-v4-domestic-payment-consent-create",
    "OB-400-DOP-100500": "pis-v4-domestic-payment-consent-create",
    "OB-400-DOP-100600": "pis-v4-domestic-payment-consent-create",
    "OB-400-DOP-100700": "pis-v4-domestic-payment-consent-create",
    "OB-400-DOP-100900": "pis-v4-domestic-scheduled-payment-consent-create",
    "OB-400-DOP-101100": "pis-v4-domestic-scheduled-payment-consent-create-and-authorise",
    "OB-400-DOP-101101": "pis-v4-domestic-scheduled-payment-consent-create-and-authorise",
    "OB-400-DOP-101300": "pis-v4-domestic-standing-order-consent-create",
    "OB-400-DOP-101400": "pis-v4-domestic-standing-order-consent-create",
    "OB-400-DOP-101401": "pis-v4-domestic-standing-order-consent-create",
    "OB-400-DOP-101500": "pis-v4-domestic-standing-order-consent-create",
    "OB-400-DOP-101700": "pis-v4-international-payment-consent-create",
    "OB-400-DOP-101800": "pis-v4-international-payment-consent-create",
    "OB-400-DOP-101900": "pis-v4-international-payment-consent-create",
    "OB-400-DOP-102100": "pis-v4-international-scheduled-payment-consent-create",
    "OB-400-DOP-102200": "pis-v4-international-scheduled-payment-consent-create",
    "OB-400-DOP-102300": "pis-v4-international-scheduled-payment-consent-create",
}
"""Consent request whose PSU flow must complete before each dependent row."""

_SCHEDULED_LEGACY_AUTHORIZATION = CataloguePsuAuthorization(
    authorization_step_id="setup-pis-domestic-scheduled-payment-legacy-consent-authorisation",
    authorization_step_name="Authorise legacy domestic scheduled payment consent",
    token_step_id="setup-token-pis-domestic-scheduled-payment-legacy-access",  # noqa: S106 - semantic step id
    token_id="pis-domestic-scheduled-payment-access",  # noqa: S106 - semantic token id
    flow_label="legacy domestic scheduled payment",
)
"""Independent PSU flow required by legacy row OB-400-DOP-101000."""

_SCHEDULED_FLOW_STEP_REPLACEMENTS = (
    (
        "pis-v4-domestic-scheduled-payment-consent-create-request",
        "pis-v4-domestic-scheduled-payment-consent-create-and-authorise-request",
    ),
)
"""Placeholder substitutions for the second legacy scheduled-payment flow."""

type ResponseSchemaRefs = dict[tuple[HttpMethod, str, int], str]
"""Response schema references keyed by method, path, and HTTP status."""


def build_v40_pis_catalogue(
    source: TestCatalogue,
    *,
    response_schema_refs: ResponseSchemaRefs,
) -> TestCatalogue:
    """Create one exact executable v4 PIS case for every pinned legacy row.

    Args:
        source: Mixed v3.1/v4 catalogue supplying request templates and
            applicability metadata.
        response_schema_refs: Bundled OpenAPI schema references by operation
            and response status.

    Returns:
        Dedicated v4 PIS catalogue in pinned legacy manifest order.

    Raises:
        ValueError: If a legacy row cannot be mapped uniquely or the source
            catalogue cannot represent its exact operation and schema contract.
    """
    source_cases_by_id = {test_case.test_case_id: test_case for test_case in source.test_cases}
    strict_cases = tuple(
        _case_for_row(
            row,
            row_index=row_index,
            source=source,
            source_cases_by_id=source_cases_by_id,
            response_schema_refs=response_schema_refs,
        )
        for row_index, row in enumerate(_legacy_rows())
    )
    selected_ids = {test_case.test_case_id for test_case in strict_cases}
    for test_case in strict_cases:
        missing_dependencies = set(test_case.dependencies).difference(selected_ids)
        if missing_dependencies:
            missing = ", ".join(sorted(missing_dependencies))
            raise ValueError(f"v4 PIS case {test_case.test_case_id} has missing dependencies: {missing}")
    return TestCatalogue(
        key=source.key,
        catalogue_version=source.catalogue_version,
        test_cases=strict_cases,
        capabilities=source.capabilities,
        configuration_requirements=source.configuration_requirements,
        runtime_capabilities=source.runtime_capabilities,
        provenance=_catalogue_provenance(),
    )


def _case_for_row(
    row: JsonObject,
    *,
    row_index: int,
    source: TestCatalogue,
    source_cases_by_id: dict[str, CatalogueTestCase],
    response_schema_refs: ResponseSchemaRefs,
) -> CatalogueTestCase:
    """Build one row-specific catalogue case.

    Args:
        row: Raw pinned legacy manifest row.
        row_index: Stable row offset in the pinned manifest.
        source: Mixed source catalogue carrying legacy row provenance.
        source_cases_by_id: Source cases keyed by stable case id.
        response_schema_refs: Bundled response schemas by operation and status.

    Returns:
        Catalogue case with exact request, assertion, schema, and signature
        behavior for the row.

    Raises:
        ValueError: If the row has invalid metadata or cannot be mapped to the
            correct source operation.
    """
    script_id = _required_string(row, "id")
    source_case = _source_case_for_script(source, script_id=script_id)
    base_case = source_cases_by_id.get(_BASE_CASE_ID_BY_SCRIPT.get(script_id, source_case.test_case_id))
    if base_case is None:
        raise ValueError(f"v4 PIS row {script_id} has no operation base case")
    test_case_id = _CASE_ID_BY_SCRIPT.get(script_id, source_case.test_case_id)
    request_step = _request_step_for_row(
        row,
        script_id=script_id,
        test_case_id=test_case_id,
        base_case=base_case,
    )
    expected_method = _required_string(row, "method").upper()
    expected_path = f"{_API_PATH_PREFIX}{_required_string(row, 'uri')}"
    if request_step.method != expected_method or _normalized_operation_path(request_step.path) != (
        _normalized_operation_path(expected_path)
    ):
        raise ValueError(
            f"v4 PIS row {script_id} expected {expected_method} {expected_path}, "
            f"got {request_step.method} {request_step.path}"
        )
    return replace(
        base_case,
        test_case_id=test_case_id,
        name=_required_string(row, "description"),
        compliance_scope=_compliance_scope(row),
        applicability=replace(base_case.applicability, specification_versions=_SPECIFICATION_VERSIONS),
        dependencies=_DEPENDENCIES_BY_SCRIPT.get(script_id, ()),
        request_steps=(request_step,),
        assertions=(
            _legacy_assertion(
                row,
                row_index=row_index,
                request_step=request_step,
                response_schema_refs=response_schema_refs,
            ),
        ),
        response_signature_required=row.get("validateSignature") is True,
    )


def _source_case_for_script(source: TestCatalogue, *, script_id: str) -> CatalogueTestCase:
    """Find the mixed-source case carrying one v4 legacy script.

    Args:
        source: Mixed v3.1/v4 PIS catalogue.
        script_id: Legacy script identifier to locate.

    Returns:
        Unique source case carrying the requested v4 script.

    Raises:
        ValueError: If the script is missing or represented more than once.
    """
    marker = f"legacy-fcs-script:ob_4.0_payment_fca.json#{script_id}"
    candidates = [test_case for test_case in source.test_cases if marker in test_case.compliance_scope]
    if len(candidates) != 1:
        raise ValueError(f"v4 PIS script {script_id} maps to {len(candidates)} source cases")
    return candidates[0]


def _request_step_for_row(
    row: JsonObject,
    *,
    script_id: str,
    test_case_id: str,
    base_case: CatalogueTestCase,
) -> CatalogueRequestStep:
    """Create the exact request step for one legacy row.

    Args:
        row: Raw pinned legacy manifest row.
        script_id: Stable row script identifier.
        test_case_id: Stable generated catalogue case id.
        base_case: Source case carrying the correct operation template.

    Returns:
        Request step with row-specific values, authorization, and placeholders.

    Raises:
        ValueError: If the base case has no request step or a consent row that
            requests authorization lacks authorization metadata.
    """
    if not base_case.request_steps:
        raise ValueError(f"v4 PIS row {script_id} has no source request")
    base_step = base_case.request_steps[0]
    replacements = _SCHEDULED_FLOW_STEP_REPLACEMENTS if script_id in {"OB-400-DOP-101100", "OB-400-DOP-101101"} else ()
    path = cast("str", _replace_json_tokens(base_step.path, replacements))
    body_template = _replace_json_tokens(copy.deepcopy(base_step.body_template), replacements)
    body_template, generated_values = _apply_request_data(
        body_template,
        generated_values=dict(base_step.generated_values),
        row=row,
        script_id=script_id,
    )
    parameters = row.get("parameters", {})
    request_consent = isinstance(parameters, dict) and parameters.get("requestConsent") == "true"
    psu_authorization = base_step.psu_authorization if request_consent else None
    if script_id == "OB-400-DOP-101000":
        psu_authorization = _SCHEDULED_LEGACY_AUTHORIZATION
    if request_consent and psu_authorization is None:
        raise ValueError(f"v4 PIS consent row {script_id} lacks PSU authorization metadata")
    authorization_case_id = _AUTHORIZATION_CONSENT_CASE_BY_SCRIPT.get(script_id)
    return replace(
        base_step,
        step_id=f"{test_case_id}-request",
        name=_required_string(row, "description"),
        path=path,
        body_template=body_template,
        generated_values=MappingProxyType(generated_values),
        psu_authorization=psu_authorization,
        required_psu_authorization_step_id=(
            f"{authorization_case_id}-request" if authorization_case_id is not None else None
        ),
    )


def _apply_request_data(
    body_template: JsonValue | None,
    *,
    generated_values: dict[str, GeneratedRuntimeValue],
    row: JsonObject,
    script_id: str,
) -> tuple[JsonValue | None, dict[str, GeneratedRuntimeValue]]:
    """Apply exact legacy constants and negative payload variants.

    Args:
        body_template: Source request body template.
        generated_values: Generated-value strategies attached to the request.
        row: Raw pinned legacy manifest row.
        script_id: Stable legacy script identifier.

    Returns:
        Updated request body and generated-value strategies.

    Raises:
        ValueError: If a row requiring body mutation has no initiation object.
    """
    parameters = row.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError(f"v4 PIS row {script_id} has invalid parameters")
    end_to_end_identification = parameters.get("endToEndIdentification")
    if isinstance(end_to_end_identification, str) and not end_to_end_identification.startswith("$"):
        body_template = _set_initiation_field(
            body_template,
            script_id=script_id,
            field_name="EndToEndIdentification",
            value=end_to_end_identification,
        )
        generated_values.pop("endToEndIdentification", None)
    date_strategies: dict[str, GeneratedRuntimeValue] = {
        "OB-400-DOP-100810": "next-day-date-offset",
        "OB-400-DOP-100820": "next-day-date-utc",
    }
    if date_strategy := date_strategies.get(script_id):
        generated_values["requestedExecutionDateTime"] = date_strategy
    if script_id == "OB-400-DOP-101400":
        body_template = _set_initiation_field(
            body_template,
            script_id=script_id,
            field_name="MandateRelatedInformation",
            value={
                "FirstPaymentDateTime": "${runtime.pisFirstPaymentDateTime}",
                "Frequency": {"Type": "foobar"},
            },
        )
    if script_id == "OB-400-DOP-101503":
        body_template = _set_initiation_field(
            body_template,
            script_id=script_id,
            field_name="MandateRelatedInformation",
            value={
                "FirstPaymentDateTime": "${runtime.pisFirstPaymentDateTime}",
                "Frequency": {
                    "Type": "WEEK",
                    "CountPerPeriod": 1,
                    "PointInTime": "03",
                },
            },
        )
    return body_template, generated_values


def _set_initiation_field(
    body_template: JsonValue | None,
    *,
    script_id: str,
    field_name: str,
    value: JsonValue,
) -> JsonObject:
    """Replace one field in a PIS request's ``Data.Initiation`` object.

    Args:
        body_template: PIS request body to update.
        script_id: Legacy row identifier used in diagnostics.
        field_name: Initiation field to replace.
        value: JSON value to assign.

    Returns:
        Updated request body without mutating the source template.

    Raises:
        ValueError: If the body does not contain ``Data.Initiation``.
    """
    if not isinstance(body_template, dict):
        raise ValueError(f"v4 PIS row {script_id} is missing its request body")
    data = body_template.get("Data")
    if not isinstance(data, dict):
        raise ValueError(f"v4 PIS row {script_id} is missing Data")
    initiation = data.get("Initiation")
    if not isinstance(initiation, dict):
        raise ValueError(f"v4 PIS row {script_id} is missing Data.Initiation")
    return {
        **body_template,
        "Data": {
            **data,
            "Initiation": {
                **initiation,
                field_name: value,
            },
        },
    }


def _legacy_assertion(
    row: JsonObject,
    *,
    row_index: int,
    request_step: CatalogueRequestStep,
    response_schema_refs: ResponseSchemaRefs,
) -> CatalogueAssertion:
    """Build one exact executable legacy assertion bundle.

    Args:
        row: Raw pinned legacy manifest row.
        row_index: Stable row offset in the pinned manifest.
        request_step: Exact request used to select response schemas.
        response_schema_refs: Bundled response schemas by operation and status.

    Returns:
        Assertion preserving legacy all, one-of, schema, and ordering semantics.

    Raises:
        ValueError: If an assertion reference or required response schema cannot
            be resolved.
    """
    all_ids = _string_list(row, "asserts")
    one_of_ids = _string_list(row, "asserts_one_of")
    last_if_all_ids = _string_list(row, "asserts_last_if_all")
    definitions = _legacy_assertion_definitions()
    resolved: list[JsonObject] = []
    for assertion_id in (*all_ids, *one_of_ids, *last_if_all_ids):
        definition = definitions.get(assertion_id)
        if not isinstance(definition, dict) or not isinstance(definition.get("expect"), dict):
            raise ValueError(f"v4 PIS row {row_index} references unknown assertion {assertion_id}")
        resolved.append(cast("JsonObject", definition["expect"]))
    all_count = len(all_ids)
    one_of_count = len(one_of_ids)
    rule: dict[str, JsonValue] = {
        "rowKey": f"ob_4.0_payment_fca.json#{row_index}:{_required_string(row, 'id')}",
        "allOf": cast("list[JsonValue]", resolved[:all_count]),
        "oneOf": cast("list[JsonValue]", resolved[all_count : all_count + one_of_count]),
        "lastIfAll": cast("list[JsonValue]", resolved[all_count + one_of_count :]),
        "legacyAssertionIds": [*all_ids, *one_of_ids, *last_if_all_ids],
    }
    if row.get("schemaCheck") is True:
        statuses = {status for expectation in resolved if isinstance(status := expectation.get("status-code"), int)}
        schema_refs = _schema_refs_for_request(
            request_step,
            statuses=statuses,
            response_schema_refs=response_schema_refs,
        )
        rule["schemaDocument"] = _SCHEMA_DOCUMENT
        rule["schemaRefs"] = schema_refs
    return CatalogueAssertion(
        assertion_id=f"legacy-fcs-row-{row_index}",
        kind="legacy_fcs",
        description=f"Execute pinned legacy FCS v4 PIS row {row_index}.",
        rule=rule,
    )


def _schema_refs_for_request(
    request_step: CatalogueRequestStep,
    *,
    statuses: set[int],
    response_schema_refs: ResponseSchemaRefs,
) -> dict[str, JsonValue]:
    """Resolve bundled response schemas for one legacy request.

    Args:
        request_step: Exact catalogue request step.
        statuses: HTTP statuses asserted by the legacy row.
        response_schema_refs: Bundled schemas keyed by operation and status.

    Returns:
        Schema references keyed by string-form HTTP status.

    Raises:
        ValueError: If a non-204 asserted response has no bundled schema.
    """
    resolved: dict[str, JsonValue] = {}
    for status in sorted(statuses):
        if status == 204:
            continue
        schema_ref = next(
            (
                reference
                for (method, path, candidate_status), reference in response_schema_refs.items()
                if method == request_step.method
                and candidate_status == status
                and _normalized_operation_path(path) == _normalized_operation_path(request_step.path)
            ),
            None,
        )
        if schema_ref is None:
            raise ValueError(
                f"v4 PIS request {request_step.method} {request_step.path} has no schema for HTTP {status}"
            )
        resolved[str(status)] = schema_ref
    return resolved


def _compliance_scope(row: JsonObject) -> tuple[str, ...]:
    """Build immutable pinned provenance for one v4 PIS row.

    Args:
        row: Raw pinned legacy manifest row.

    Returns:
        Source, script, and assertion provenance labels.
    """
    script_id = _required_string(row, "id")
    return (
        f"legacy-fcs-source:OpenBankingUK/conformance-suite@{_LEGACY_COMMIT}/manifests/ob_4.0_payment_fca.json",
        f"legacy-fcs-script:ob_4.0_payment_fca.json#{script_id}",
        *(
            f"legacy-fcs-assertion:{assertion_id}"
            for field in ("asserts", "asserts_one_of", "asserts_last_if_all")
            for assertion_id in _string_list(row, field)
        ),
    )


def _normalized_operation_path(path: str) -> str:
    """Normalize legacy and runtime path variables for operation matching.

    Args:
        path: Legacy, OpenAPI, or execution-placeholder path.

    Returns:
        Path with each variable syntax represented by a common marker.
    """
    normalized = re.sub(r"\$\{[^}]+\}", "{value}", path)
    normalized = re.sub(r"\{[^}]+\}", "{value}", normalized)
    return re.sub(r"\$[A-Za-z0-9_-]+", "{value}", normalized)


def _replace_json_tokens(
    value: JsonValue | None,
    replacements: tuple[tuple[str, str], ...],
) -> JsonValue | None:
    """Replace literal tokens recursively in a JSON-compatible value.

    Args:
        value: Source value to transform.
        replacements: Ordered old/new literal pairs.

    Returns:
        Value with replacements applied while preserving JSON structure.
    """
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_json_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_json_tokens(item, replacements) for key, item in value.items()}
    return value


def _required_string(value: JsonObject, key: str) -> str:
    """Read one required non-empty string from a JSON object.

    Args:
        value: JSON object containing the field.
        key: Field name to read.

    Returns:
        Required non-empty string.

    Raises:
        ValueError: If the field is missing or not a non-empty string.
    """
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"v4 PIS legacy row field {key} must be a non-empty string")
    return result


def _string_list(value: JsonObject, key: str) -> tuple[str, ...]:
    """Read an optional list of strings from a JSON object.

    Args:
        value: JSON object containing the field.
        key: Field name to read.

    Returns:
        String values in source order.

    Raises:
        ValueError: If the field is present but is not a list of strings.
    """
    result = value.get(key, [])
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ValueError(f"v4 PIS legacy row field {key} must be an array of strings")
    return tuple(cast("list[str]", result))


@cache
def _legacy_rows() -> tuple[JsonObject, ...]:
    """Load the pinned v4 PIS manifest rows.

    Returns:
        Immutable manifest rows in legacy execution order.

    Raises:
        ValueError: If the pinned document does not contain a scripts array.
    """
    document = json.loads(_LEGACY_MANIFEST_PATH.read_text(encoding="utf-8"))
    scripts = document.get("scripts")
    if not isinstance(scripts, list) or any(not isinstance(row, dict) for row in scripts):
        raise ValueError("Pinned v4 PIS manifest must contain a scripts object array")
    return tuple(cast("list[JsonObject]", scripts))


@cache
def _legacy_assertion_definitions() -> JsonObject:
    """Load pinned legacy assertion definitions.

    Returns:
        Assertion definitions keyed by legacy assertion identifier.

    Raises:
        ValueError: If the pinned document does not contain a references object.
    """
    document = json.loads(_LEGACY_ASSERTIONS_PATH.read_text(encoding="utf-8"))
    references = document.get("references")
    if not isinstance(references, dict):
        raise ValueError("Pinned legacy assertions must contain a references object")
    return cast("JsonObject", references)


def _catalogue_provenance() -> CatalogueProvenance:
    """Build immutable strict-parity source provenance.

    Returns:
        Pinned legacy and normative source metadata for result evidence.
    """
    return CatalogueProvenance(
        repository="OpenBankingUK/conformance-suite",
        release="v1.10.0",
        commit=_LEGACY_COMMIT,
        source_paths=(
            "manifests/ob_4.0_payment_fca.json",
            "manifests/assertions.json",
            "manifests/data.json",
            "OpenBankingUK/read-write-api-specs@v4.0-Update-5/dist/openapi/payment-initiation-openapi.json",
        ),
        references=MappingProxyType(
            {
                "legacyRelease": "https://github.com/OpenBankingUK/conformance-suite/releases/tag/v1.10.0",
                "normativeSchemas": ("https://github.com/OpenBankingUK/read-write-api-specs/tree/v4.0-Update-5"),
                "pinnedManifest": ("conformance/standards/ob_read_write/v4_0/legacy/ob_4.0_payment_fca.json"),
            }
        ),
    )


__all__ = ["ResponseSchemaRefs", "build_v40_pis_catalogue"]
