"""Focused tests for complete immutable execution request templates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import MappingProxyType
from typing import cast

import pytest

from conformance.configuration_contracts import (
    ConfigurationContractError,
    DetachedJwsOmittedClaim,
    DetachedJwsProfile,
    DiagnosticCode,
    ExecutionDetachedJws,
    ExecutionManifestHeader,
    ExecutionPsuAuthorization,
    ExecutionResponseSignature,
    ExecutionTokenEndpointAuth,
    GeneratedHeaderValue,
    GeneratedValueStrategy,
    RequestBaseUrlSource,
    ResponseSignatureSource,
    StableId,
    TokenEndpointAuthSource,
    execution_manifest_id,
    execution_manifest_to_document,
    load_execution_manifest,
    parse_execution_manifest,
)
from conformance.json_types import JsonObject
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts" / "v1" / "execution-manifest.valid.json"


def test_complete_request_templates_round_trip_and_detach_mutable_json() -> None:
    manifest = load_execution_manifest(_MANIFEST_PATH)
    source_body: JsonObject = {
        "Data": {
            "Reference": "${runtime.paymentReference}",
            "InteractionId": "${generated.interactionId}",
        }
    }
    first, second, third, fourth = manifest.steps
    first_request = replace(
        first.request,
        base_url_source=RequestBaseUrlSource.RESOURCE,
        query_templates=MappingProxyType({"page": "${runtime.page}"}),
        header_templates=(
            ExecutionManifestHeader(name="accept", literal_value="application/json"),
            ExecutionManifestHeader(name="x-ob-id", runtime_input_ref="financialId"),
            ExecutionManifestHeader(
                name="x-fapi-interaction-id",
                generated_value=GeneratedHeaderValue.UUID4,
            ),
        ),
        json_body_template=source_body,
        runtime_input_refs=("paymentReference", "page", "financialId"),
        generated_values=MappingProxyType({"interactionId": GeneratedValueStrategy.UUID4_HEX}),
        produced_token_id=StableId("payment-access"),
        detached_jws=ExecutionDetachedJws(
            profile=DetachedJwsProfile.OB_V3_1_4_PLUS,
            omitted_claims=(DetachedJwsOmittedClaim.ISS,),
        ),
        response_signature=ExecutionResponseSignature(source=ResponseSignatureSource.DISCOVERY_JWKS),
        psu_authorization=ExecutionPsuAuthorization(
            authorization_step_id=StableId("payment-authorize"),
            authorization_step_name="Authorize payment",
            token_step_id=StableId("payment-token-exchange"),
            token_id=StableId("payment-psu-access"),
            flow_label="payment",
        ),
    )
    third_request = replace(
        third.request,
        base_url_source=RequestBaseUrlSource.TOKEN,
        json_body_template=None,
        form_body_template=MappingProxyType({"grant_type": "client_credentials", "scope": "payments"}),
        required_token_id=StableId("payment-access"),
        required_psu_authorization_step_id=StableId("payment-authorize"),
        token_endpoint_auth=ExecutionTokenEndpointAuth(source=TokenEndpointAuthSource.FAPI_SIGNING),
    )
    provisional = replace(
        manifest,
        steps=(
            replace(first, request=first_request),
            second,
            replace(third, request=third_request),
            fourth,
        ),
    )
    complete = replace(provisional, id=execution_manifest_id(provisional))

    parsed = parse_execution_manifest(execution_manifest_to_document(complete))

    assert parsed == complete
    source_body["Data"] = {"Reference": "mutated"}
    parsed_body = cast("dict[str, object]", parsed.steps[0].request.json_body_template)
    assert cast("dict[str, object]", parsed_body["Data"])["Reference"] == ("${runtime.paymentReference}")
    with pytest.raises(TypeError):
        parsed_body["Data"] = {}


def test_request_template_mutations_change_content_addressed_manifest_id() -> None:
    manifest = load_execution_manifest(_MANIFEST_PATH)
    first = manifest.steps[0]
    body_one: JsonObject = {"Data": {"Value": "one"}}
    body_two: JsonObject = {"Data": {"Value": "two"}}
    request_one = replace(
        first.request,
        base_url_source=RequestBaseUrlSource.RESOURCE,
        header_templates=(ExecutionManifestHeader(name="x-test", literal_value="one"),),
        json_body_template=body_one,
    )
    request_two = replace(
        request_one,
        header_templates=(ExecutionManifestHeader(name="x-test", literal_value="two"),),
        json_body_template=body_two,
    )
    first_manifest = replace(
        manifest,
        steps=(replace(first, request=request_one), *manifest.steps[1:]),
    )
    second_manifest = replace(
        manifest,
        steps=(replace(first, request=request_two), *manifest.steps[1:]),
    )

    assert execution_manifest_id(first_manifest) != execution_manifest_id(second_manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseUrlSource", "participant-choice"),
        ("generatedValues", {"id": "arbitrary-callback"}),
        (
            "detachedJws",
            {"source": "fapi-signing", "profile": "future-profile"},
        ),
    ],
)
def test_request_template_unsupported_vocabulary_fails_closed(
    field: str,
    value: object,
) -> None:
    document = cast(JsonObject, json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")))
    request = cast(JsonObject, cast(list[JsonObject], document["steps"])[0]["request"])
    request[field] = cast("str | JsonObject", value)
    _reidentify(document)

    with pytest.raises(ConfigurationContractError) as captured:
        parse_execution_manifest(document)

    assert captured.value.diagnostics[0].code is DiagnosticCode.SCHEMA_VALIDATION_FAILED


def test_request_template_rejects_duplicate_headers_and_missing_generated_reference() -> None:
    document = cast(JsonObject, json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")))
    request = cast(JsonObject, cast(list[JsonObject], document["steps"])[0]["request"])
    request["headerTemplates"] = [
        {"name": "X-Test", "literalValue": "${generated.missing}"},
        {"name": "x-test", "literalValue": "duplicate"},
    ]
    _reidentify(document)

    with pytest.raises(ConfigurationContractError) as captured:
        parse_execution_manifest(document)

    assert {diagnostic.code for diagnostic in captured.value.diagnostics} == {
        DiagnosticCode.DUPLICATE_ID,
        DiagnosticCode.REFERENCE_UNRESOLVED,
    }


def _reidentify(document: JsonObject) -> None:
    identity_document = dict(document)
    del identity_document["id"]
    canonical = json.dumps(identity_document, separators=(",", ":"), sort_keys=True)
    document["id"] = f"execution-manifest:{hashlib.sha256(canonical.encode()).hexdigest()}"
