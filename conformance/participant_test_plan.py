"""Participant-facing test-plan contract embedded in config JSON documents.

The participant contract is a single config JSON document with a top-level
``testPlan`` object.  The test plan stores endpoint-first intent: target
coordinates, the catalogue hash captured when the plan was built, selected
resource groups, selected endpoint operations, and plan-level test data.

The current execution engine still consumes the internal
:class:`conformance.run_plan_v2.RunPlanV2` shape.  This module keeps that
legacy wire format out of participant inputs while providing a narrow adapter
until the runtime planner can consume :class:`ParticipantTestPlan` directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from conformance.json_types import JsonValue
from conformance.run_plan_v2 import (
    EndpointSelection,
    RunPlanV2,
    RunPlanV2ParseError,
    RunPlanV2TargetCoordinates,
    parse_run_plan_v2,
)


@dataclass(frozen=True)
class ParticipantTestPlanTargetCoordinates:
    """Target identity coordinates stored inside a participant ``testPlan``.

    Attributes:
        standard: Top-level conformance standard, such as ``"obl"``.
        specification: Specification under the standard, such as
            ``"read-write"`` or ``"dynamic-client-registration"``.
        security_profile: Security profile selected for the test plan.
        specification_version: Standards version string for the selected
            specification.
        catalogue_hash: SHA-256 catalogue hash captured when the plan was
            authored.
    """

    standard: str
    specification: str
    security_profile: str
    specification_version: str
    catalogue_hash: str


@dataclass(frozen=True)
class ParticipantEndpointSelection:
    """Participant coverage choice for one endpoint operation.

    Attributes:
        endpoint_id: Stable catalogue endpoint identifier.
        operation: HTTP or scenario operation name.
        selected: Whether the participant selected the endpoint for execution.
        field_values: Per-endpoint field values entered in the builder.
    """

    endpoint_id: str
    operation: str
    selected: bool
    field_values: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ParticipantTestPlan:
    """Participant-owned endpoint-first test-plan intent.

    Attributes:
        target: Target identity coordinates including the catalogue hash.
        resource_groups: Ordered resource-group identifiers selected for the
            run. Empty for targets without resource groups.
        endpoint_selections: Ordered per-endpoint coverage choices.
        test_data: Plan-level test-data overrides.
    """

    target: ParticipantTestPlanTargetCoordinates
    resource_groups: tuple[str, ...]
    endpoint_selections: tuple[ParticipantEndpointSelection, ...]
    test_data: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class ParticipantTestPlanParseError(ValueError):
    """Raised when a raw JSON value cannot be parsed as ``testPlan``."""


def parse_participant_test_plan(raw: JsonValue) -> ParticipantTestPlan:
    """Parse and validate a participant ``testPlan`` JSON object.

    Args:
        raw: Parsed JSON value supplied as the top-level ``testPlan`` object.

    Returns:
        Validated participant test-plan model.

    Raises:
        ParticipantTestPlanParseError: If the value is not an object, contains
            the retired ``schemaVersion`` discriminator, or fails structural
            validation.
    """
    if not isinstance(raw, dict):
        raise ParticipantTestPlanParseError("testPlan must be a JSON object")
    if "schemaVersion" in raw:
        raise ParticipantTestPlanParseError(
            "testPlan must not include schemaVersion; rebuild/export the one-file config JSON."
        )

    raw_for_internal_parser: dict[str, JsonValue] = {"schemaVersion": "2", **raw}
    try:
        internal_plan = parse_run_plan_v2(raw_for_internal_parser)
    except RunPlanV2ParseError as error:
        message = str(error).replace("RunPlanV2", "testPlan")
        raise ParticipantTestPlanParseError(message) from error
    return participant_test_plan_from_run_plan_v2(internal_plan)


def serialise_participant_test_plan(plan: ParticipantTestPlan) -> dict[str, JsonValue]:
    """Serialise a participant test plan to JSON-compatible camelCase keys.

    Args:
        plan: Participant test-plan model to serialise.

    Returns:
        A JSON-compatible dictionary suitable for the top-level ``testPlan``
        config field.
    """
    result: dict[str, JsonValue] = {
        "target": {
            "standard": plan.target.standard,
            "specification": plan.target.specification,
            "securityProfile": plan.target.security_profile,
            "specificationVersion": plan.target.specification_version,
            "catalogueHash": plan.target.catalogue_hash,
        },
    }
    if plan.resource_groups:
        result["resourceGroups"] = list(plan.resource_groups)
    if plan.endpoint_selections:
        selections: list[JsonValue] = []
        for selection in plan.endpoint_selections:
            entry: dict[str, JsonValue] = {
                "endpointId": selection.endpoint_id,
                "operation": selection.operation,
                "selected": selection.selected,
            }
            if selection.field_values:
                entry["fieldValues"] = dict(selection.field_values)
            selections.append(entry)
        result["endpointSelections"] = selections
    if plan.test_data:
        result["testData"] = dict(plan.test_data)
    return result


def participant_test_plan_from_run_plan_v2(plan: RunPlanV2) -> ParticipantTestPlan:
    """Convert the current internal execution plan into participant intent.

    Args:
        plan: Internal execution-planner intent to convert.

    Returns:
        Participant-facing test-plan model with no schema discriminator.
    """
    return ParticipantTestPlan(
        target=ParticipantTestPlanTargetCoordinates(
            standard=plan.target.standard,
            specification=plan.target.specification,
            security_profile=plan.target.security_profile,
            specification_version=plan.target.specification_version,
            catalogue_hash=plan.target.catalogue_hash,
        ),
        resource_groups=plan.resource_groups,
        endpoint_selections=tuple(
            ParticipantEndpointSelection(
                endpoint_id=selection.endpoint_id,
                operation=selection.operation,
                selected=selection.selected,
                field_values=MappingProxyType(dict(selection.field_values)),
            )
            for selection in plan.endpoint_selections
        ),
        test_data=MappingProxyType(dict(plan.test_data)),
    )


def run_plan_v2_from_participant_test_plan(plan: ParticipantTestPlan) -> RunPlanV2:
    """Adapt participant ``testPlan`` intent to the current runtime planner.

    Args:
        plan: Participant test-plan model from the config document.

    Returns:
        Internal :class:`conformance.run_plan_v2.RunPlanV2` used by the
        catalogue planner.
    """
    return RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard=plan.target.standard,
            specification=plan.target.specification,
            security_profile=plan.target.security_profile,
            specification_version=plan.target.specification_version,
            catalogue_hash=plan.target.catalogue_hash,
        ),
        resource_groups=plan.resource_groups,
        endpoint_selections=tuple(
            EndpointSelection(
                endpoint_id=selection.endpoint_id,
                operation=selection.operation,
                selected=selection.selected,
                field_values=MappingProxyType(dict(selection.field_values)),
            )
            for selection in plan.endpoint_selections
        ),
        test_data=MappingProxyType(dict(plan.test_data)),
    )
