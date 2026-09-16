"""Compile-boundary materialisation for immutable execution manifests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from conformance.catalogue import CatalogueRequestStep, CompiledTestPlan
from conformance.configuration_contracts.diagnostics import ConfigurationContractError
from conformance.configuration_contracts.loader import (
    execution_manifest_id as _v1_execution_manifest_id,
)
from conformance.configuration_contracts.loader import (
    execution_manifest_to_document as _v1_execution_manifest_to_document,
)
from conformance.configuration_contracts.loader import (
    parse_execution_manifest as _parse_v1_execution_manifest,
)
from conformance.configuration_contracts.models import (
    DetachedJwsOmittedClaim,
    DetachedJwsProfile,
    ExecutionDetachedJws,
    ExecutionManifest,
    ExecutionManifestHeader,
    ExecutionManifestRequest,
    ExecutionPsuAuthorization,
    ExecutionResponseSignature,
    ExecutionTokenEndpointAuth,
    GeneratedHeaderValue,
    GeneratedValueStrategy,
    RequestBaseUrlSource,
    ResponseSignatureSource,
    StableId,
    TokenEndpointAuthSource,
)
from conformance.configuration_contracts.suite_release_artifacts import SuiteReleaseArtifactResolver
from conformance.json_types import JsonObject, JsonValue
from conformance.results import ResultTraceabilitySource

if TYPE_CHECKING:
    from conformance.configuration_contracts.v2_models import ExecutionManifest as V2ExecutionManifest

type SupportedExecutionManifest = ExecutionManifest | V2ExecutionManifest


class ResolvedPlanAdapterError(ValueError):
    """Raised when a generated manifest cannot use the current execution path."""


class LegacyExecutionEngine(StrEnum):
    """Existing runtime selected behind the execution-manifest boundary."""

    READ_WRITE = "read-write"
    DCR = "dcr"


@dataclass(frozen=True, slots=True)
class PreparedExecutionManifest:
    """Immutable manifest plus non-work runtime environment and provenance."""

    manifest: SupportedExecutionManifest
    engine: LegacyExecutionEngine
    runtime_inputs: Mapping[str, JsonValue]
    runtime_input_base_dir: Path
    artifact_resolver: SuiteReleaseArtifactResolver
    result_traceability: ResultTraceabilitySource | None = None


_AIS_CONSENT_CREATE_STEP_ID = "ais-at-setup-consent-request"
_AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID = "ais-account-access-token"  # noqa: S105 - semantic token ID
_PIS_MISSING_SIGNATURE_STEP_ID = "pis-v4-domestic-consents-missing-signature"
_CLIENT_CREDENTIALS_TOKEN_SCOPE = {
    "ais-client-credentials": "accounts",
    "cbpii-client-credentials": "fundsconfirmations",
    "pis-payment-access": "payments",
    "vrp-payment-access": "payments",
}
_CBPII_CONSENT_STEP_ID = "cbpii-consent-create-core-request"
_CBPII_AUTHORIZED_RESOURCE_STEP_IDS = {
    "cbpii-consent-get-authorised-request",
    "cbpii-funds-confirmation-create-request",
}
_VRP_CONSENT_STEP_IDS = {
    "vrp-consent-create-awaiting-authorisation-v31-pre-3111-request",
    "vrp-consent-create-awaiting-authorisation-v31-3111-request",
    "vrp-consent-create-awaiting-authorisation-v4-request",
    "cvrp-consent-create-awaiting-authorisation-v4-request",
}


def _execution_manifest_to_document(manifest: ExecutionManifest) -> JsonObject:
    """Dispatch serialization without making the v1 compatibility module authoritative."""
    if manifest.schema_version == "2.0":
        from conformance.configuration_contracts.v2_loader import (
            execution_manifest_to_document,
        )

        return execution_manifest_to_document(manifest)  # type: ignore[arg-type]  # version-dispatched model
    return _v1_execution_manifest_to_document(manifest)


def _execution_manifest_id(manifest: ExecutionManifest) -> StableId:
    """Dispatch content addressing by the manifest's declared contract."""
    if manifest.schema_version == "2.0":
        from conformance.configuration_contracts.v2_loader import execution_manifest_id

        return execution_manifest_id(manifest)  # type: ignore[arg-type]  # version-dispatched model
    return _v1_execution_manifest_id(manifest)


def _parse_execution_manifest(document: JsonObject) -> ExecutionManifest:
    """Dispatch parsing by schemaVersion while retaining v1 deletion-only code."""
    if document.get("schemaVersion") == "2.0":
        from conformance.configuration_contracts.v2_loader import parse_execution_manifest

        return parse_execution_manifest(document)  # type: ignore[return-value]  # version-dispatched model
    return _parse_v1_execution_manifest(document)


def materialize_execution_manifest_requests(
    manifest: ExecutionManifest,
    compiled_plan: CompiledTestPlan,
    *,
    observation_ids: Mapping[str, str],
) -> ExecutionManifest:
    """Copy complete compiler-owned request instructions into the manifest.

    This compatibility materialiser is deliberately confined to launch
    preparation. The returned manifest is re-addressed after complete request,
    OAuth, PSU, JWS, output, and evidence instructions have been copied. The
    runner never receives the compiled plan.
    """
    request_entries = {
        request.step_id: (test_case, request)
        for test_case in compiled_plan.test_cases
        for request in test_case.request_steps
    }
    runtime_step_id_by_legacy_id = {
        observation_id: StableId(manifest_step_id) for manifest_step_id, observation_id in observation_ids.items()
    }
    psu_authorization_id_by_legacy_step_id = {
        request.step_id: StableId(request.psu_authorization.authorization_step_id)
        for _test_case, request in request_entries.values()
        if request.psu_authorization is not None
    }
    selected_request_ids = set(request_entries)
    required_token_ids = {
        request.required_token_id
        for _test_case, request in request_entries.values()
        if request.required_token_id is not None
    }
    materialized_steps = []
    is_dcr = compiled_plan.catalogue_key.api in {"dcr", "dynamic-client-registration"}
    for step in manifest.steps:
        if is_dcr:
            base_url_source = (
                RequestBaseUrlSource.DCR_REGISTRATION
                if step.request.method.value == "POST"
                else RequestBaseUrlSource.DCR_MANAGEMENT
            )
            materialized_steps.append(
                replace(
                    step,
                    request=replace(
                        step.request,
                        base_url_source=base_url_source,
                        invalidate_produced_authorization_token=(
                            any(
                                modification.generator == "unknown-client-id"
                                for modification in step.request.modifications
                            )
                            and any(output.source == "authorization-access-token" for output in step.outputs)
                        ),
                    ),
                )
            )
            continue
        observation_id = observation_ids.get(str(step.id))
        if observation_id is None:
            raise ResolvedPlanAdapterError(f"Manifest step {step.id!s} has no materialised request")
        entry = request_entries.get(observation_id)
        if entry is None:
            raise ResolvedPlanAdapterError(f"Manifest step {step.id!s} maps to unavailable request {observation_id}")
        test_case, request_step = entry
        materialized_steps.append(
            replace(
                step,
                request=_materialized_request(
                    step.request,
                    request_step,
                    runtime_step_id_by_legacy_id=runtime_step_id_by_legacy_id,
                    psu_authorization_id_by_legacy_step_id=psu_authorization_id_by_legacy_step_id,
                    synthetic_psu_authorization=_synthetic_psu_authorization(
                        request_step,
                        selected_request_ids=selected_request_ids,
                        required_token_ids=required_token_ids,
                    ),
                    invalidate_produced_authorization_token=False,
                    response_signature_required=test_case.response_signature_required,
                ),
            )
        )
    provisional = replace(manifest, steps=tuple(materialized_steps))
    materialized = replace(provisional, id=_execution_manifest_id(provisional))
    try:
        return _parse_execution_manifest(_execution_manifest_to_document(materialized))
    except ConfigurationContractError as error:
        raise ResolvedPlanAdapterError("Materialised execution manifest is invalid") from error


def _materialized_request(
    request: ExecutionManifestRequest,
    request_step: CatalogueRequestStep,
    *,
    runtime_step_id_by_legacy_id: Mapping[str, StableId],
    psu_authorization_id_by_legacy_step_id: Mapping[str, StableId],
    synthetic_psu_authorization: ExecutionPsuAuthorization | None,
    invalidate_produced_authorization_token: bool,
    response_signature_required: bool,
) -> ExecutionManifestRequest:
    query_templates = {
        name: _rewrite_step_placeholders(value, runtime_step_id_by_legacy_id)
        for name, value in request_step.query_parameters.items()
    }
    body_template = _rewrite_json_placeholders(
        request_step.body_template,
        runtime_step_id_by_legacy_id,
    )
    headers = tuple(
        ExecutionManifestHeader(
            name=header.name,
            runtime_input_ref=header.input_id,
            generated_value=(None if header.generated_value is None else GeneratedHeaderValue(header.generated_value)),
        )
        for header in request_step.headers
    )
    psu_authorization = request_step.psu_authorization
    return replace(
        request,
        path=_rewrite_step_placeholders(request_step.path, runtime_step_id_by_legacy_id),
        base_url_source=_request_base_url_source(request_step),
        query_templates=query_templates,
        header_templates=headers,
        json_body_template=body_template,
        runtime_input_refs=request_step.runtime_input_refs,
        generated_values={
            name: GeneratedValueStrategy(strategy) for name, strategy in request_step.generated_values.items()
        },
        required_token_id=(
            None if request_step.required_token_id is None else StableId(request_step.required_token_id)
        ),
        required_token_scope=(
            None
            if request_step.required_token_id is None
            else _CLIENT_CREDENTIALS_TOKEN_SCOPE.get(request_step.required_token_id)
        ),
        produced_token_id=(
            None if request_step.produced_token_id is None else StableId(request_step.produced_token_id)
        ),
        invalidate_produced_authorization_token=invalidate_produced_authorization_token,
        detached_jws=_detached_jws_instruction(request_step),
        token_endpoint_auth=(
            ExecutionTokenEndpointAuth(source=TokenEndpointAuthSource.FAPI_SIGNING)
            if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID
            else None
        ),
        response_signature=(
            ExecutionResponseSignature(source=ResponseSignatureSource.DISCOVERY_JWKS)
            if response_signature_required
            else None
        ),
        psu_authorization=(
            synthetic_psu_authorization
            if psu_authorization is None
            else ExecutionPsuAuthorization(
                authorization_step_id=StableId(psu_authorization.authorization_step_id),
                authorization_step_name=psu_authorization.authorization_step_name,
                token_step_id=StableId(psu_authorization.token_step_id),
                token_id=StableId(psu_authorization.token_id),
                flow_label=psu_authorization.flow_label,
            )
        ),
        required_psu_authorization_step_id=(
            None
            if request_step.required_psu_authorization_step_id is None
            else psu_authorization_id_by_legacy_step_id.get(
                request_step.required_psu_authorization_step_id,
                runtime_step_id_by_legacy_id.get(
                    request_step.required_psu_authorization_step_id,
                    StableId(request_step.required_psu_authorization_step_id),
                ),
            )
        ),
    )


def _synthetic_psu_authorization(
    request_step: CatalogueRequestStep,
    *,
    selected_request_ids: set[str],
    required_token_ids: set[str],
) -> ExecutionPsuAuthorization | None:
    if request_step.step_id == _CBPII_CONSENT_STEP_ID and selected_request_ids.intersection(
        _CBPII_AUTHORIZED_RESOURCE_STEP_IDS
    ):
        return ExecutionPsuAuthorization(
            authorization_step_id=StableId("setup-cbpii-consent-authorisation"),
            authorization_step_name="Authorise CBPII funds-confirmation consent",
            token_step_id=StableId("setup-token-cbpii-funds-confirmation"),
            token_id=StableId("cbpii-funds-confirmation"),
            flow_label="CBPII funds confirmation",
        )
    if request_step.step_id in _VRP_CONSENT_STEP_IDS:
        token_id = f"{request_step.step_id.removesuffix('-request')}-psu-payment-access"
        if token_id in required_token_ids:
            base_id = request_step.step_id.removesuffix("-request")
            return ExecutionPsuAuthorization(
                authorization_step_id=StableId(f"{base_id}-authorisation"),
                authorization_step_name="Authorise VRP consent",
                token_step_id=StableId(f"{base_id}-psu-payment-token"),
                token_id=StableId(token_id),
                flow_label="VRP consent",
            )
    return None


def _request_base_url_source(request_step: CatalogueRequestStep) -> RequestBaseUrlSource:
    if request_step.path == "/.well-known/openid-configuration":
        return RequestBaseUrlSource.DISCOVERY
    if request_step.step_id == _AIS_ACCOUNT_ACCESS_TOKEN_STEP_ID:
        return RequestBaseUrlSource.TOKEN
    return RequestBaseUrlSource.RESOURCE


def _detached_jws_instruction(request_step: CatalogueRequestStep) -> ExecutionDetachedJws | None:
    profile = request_step.detached_jws_profile
    if profile is None:
        if request_step.step_id == _AIS_CONSENT_CREATE_STEP_ID:
            profile = "legacy-b64-false"
        elif (
            request_step.method in {"POST", "PUT", "PATCH"}
            and request_step.path.startswith("/open-banking/v4.0/pisp/")
            and request_step.step_id.startswith("pis-v4-")
            and request_step.step_id != _PIS_MISSING_SIGNATURE_STEP_ID
        ):
            profile = "ob-v3.1.4+"
        elif (
            request_step.method in {"POST", "PUT", "PATCH"}
            and request_step.step_id.startswith(("vrp-", "cvrp-"))
            and request_step.body_template is not None
        ):
            profile = "ob-v3.1.4+" if request_step.path.startswith("/open-banking/v4.0/") else "legacy-b64-false"
    if profile is None:
        return None
    return ExecutionDetachedJws(
        profile=DetachedJwsProfile(profile),
        omitted_claims=tuple(DetachedJwsOmittedClaim(claim) for claim in request_step.detached_jws_omit_claims),
    )


def _rewrite_step_placeholders(value: str, step_ids: Mapping[str, StableId]) -> str:
    rewritten = value
    for legacy_id, manifest_id in step_ids.items():
        rewritten = rewritten.replace(f"${{steps.{legacy_id}.", f"${{steps.{manifest_id!s}.")
    return rewritten


def _rewrite_json_placeholders(
    value: JsonValue | None,
    step_ids: Mapping[str, StableId],
) -> JsonValue | None:
    if isinstance(value, str):
        return _rewrite_step_placeholders(value, step_ids)
    if isinstance(value, list | tuple):
        return [_rewrite_json_placeholders(item, step_ids) for item in value]
    if isinstance(value, Mapping):
        return {key: _rewrite_json_placeholders(item, step_ids) for key, item in value.items()}
    return deepcopy(value)


def validate_execution_manifest_compatibility(prepared: PreparedExecutionManifest) -> None:
    """Reject incomplete or unsupported runner instructions before execution."""
    manifest = prepared.manifest
    try:
        _parse_execution_manifest(_execution_manifest_to_document(cast(ExecutionManifest, manifest)))
    except ConfigurationContractError as error:
        raise ResolvedPlanAdapterError("Prepared execution manifest is invalid") from error
    if prepared.engine not in {LegacyExecutionEngine.READ_WRITE, LegacyExecutionEngine.DCR}:
        raise ResolvedPlanAdapterError("Unsupported execution-manifest compatibility engine")
    if prepared.result_traceability is None:
        raise ResolvedPlanAdapterError("Execution manifests require result traceability")
    for step in manifest.steps:
        if step.request.base_url_source is None:
            raise ResolvedPlanAdapterError(f"Manifest request {step.id!s} has no baseUrlSource")
    if prepared.artifact_resolver.manifest != manifest:
        raise ResolvedPlanAdapterError("Artifact resolver was not preflighted for this execution manifest")
