"""Presenter helpers for read-only generated test-plan review pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from conformance.catalogue import ApplicabilityDecision, CatalogueTestCase, CompiledTestPlan


@dataclass(frozen=True)
class PlanRuntimeRequirement:
    """Runtime requirement summary shown in the generated plan review.

    Attributes:
        input_id: Stable runtime input identifier.
        label: Human-readable catalogue prompt label.
        required: Whether the generated case requires the input.
        sensitive: Whether values for this input are secret-bearing.
    """

    input_id: str
    label: str
    required: bool
    sensitive: bool


@dataclass(frozen=True)
class PlanTestCaseRow:
    """Read-only generated test-case review row.

    Attributes:
        id: Catalogue test-case identifier.
        name: Human-readable test-case name.
        role: Execution/compliance role.
        phase: High-level execution phase derived from the compiled case role.
        source: Human-readable reason this case was generated.
        source_detail: Additional endpoint, capability, or dependency context.
        mandatory: Whether the generated case is mandatory for certification.
        dependencies: Other generated case ids this case depends on.
        request_count: Number of request steps generated for the case.
        assertion_count: Number of locked assertions attached to the case.
        runtime_requirements: Runtime inputs consumed by the generated case.
        request_step_ids: Request-step identifiers owned by the case.
        assertion_summaries: Assertion summaries owned by the case.
        compliance_scope: Traceability labels for standards and legacy coverage.
    """

    id: str
    name: str
    role: str
    phase: str
    source: str
    source_detail: str
    mandatory: bool
    dependencies: tuple[str, ...]
    request_count: int
    assertion_count: int
    runtime_requirements: tuple[PlanRuntimeRequirement, ...]
    request_step_ids: tuple[str, ...]
    assertion_summaries: tuple[str, ...]
    compliance_scope: tuple[str, ...]


def compiled_plan_rows(compiled_plan: CompiledTestPlan) -> tuple[PlanTestCaseRow, ...]:
    """Build read-only generated test rows for an already compiled plan.

    Args:
        compiled_plan: Compiled catalogue plan to render.

    Returns:
        Template-ready generated plan rows in execution order.
    """
    decisions = {decision.test_case_id: decision for decision in compiled_plan.traceability.applicability_decisions}
    selected_capabilities = _selected_capability_labels_by_id(compiled_plan)
    rows: list[PlanTestCaseRow] = []
    for test_case in compiled_plan.test_cases:
        source, source_detail = _test_case_source(
            test_case,
            decisions[test_case.test_case_id],
            selected_capabilities,
        )
        rows.append(
            PlanTestCaseRow(
                id=test_case.test_case_id,
                name=test_case.name,
                role=test_case.role,
                phase=_test_case_phase(test_case),
                source=source,
                source_detail=source_detail,
                mandatory=test_case.mandatory,
                dependencies=test_case.dependencies,
                request_count=len(test_case.request_steps),
                assertion_count=len(test_case.assertions),
                runtime_requirements=tuple(
                    PlanRuntimeRequirement(
                        input_id=requirement.input_id,
                        label=requirement.label,
                        required=requirement.required,
                        sensitive=requirement.sensitive,
                    )
                    for requirement in test_case.runtime_input_requirements
                    if requirement.source == "plan"
                ),
                request_step_ids=tuple(request_step.step_id for request_step in test_case.request_steps),
                assertion_summaries=tuple(assertion.description for assertion in test_case.assertions),
                compliance_scope=test_case.compliance_scope,
            )
        )
    return tuple(rows)


def _selected_capability_labels_by_id(compiled_plan: CompiledTestPlan) -> dict[str, tuple[str, bool]]:
    """Return selected capability labels and required flags by capability id.

    Args:
        compiled_plan: Compiled plan whose traceability carries capability selections.

    Returns:
        Mapping from capability id to display label and required flag.
    """
    return {
        capability.capability_id: (capability.label, capability.required)
        for capability in compiled_plan.traceability.selected_capabilities
    }


def _test_case_source(
    test_case: CatalogueTestCase,
    decision: ApplicabilityDecision,
    selected_capabilities: Mapping[str, tuple[str, bool]],
) -> tuple[str, str]:
    """Describe why a generated test case appears in the compiled preview.

    Args:
        test_case: Generated catalogue test case.
        decision: Compiler traceability decision for the test case.
        selected_capabilities: Selected capability labels and required flags by id.

    Returns:
        Pair of short source label and detailed source context.
    """
    if decision.dependency_of and decision.reason == "included as dependency":
        return "Automatic dependency", f"Required by {', '.join(decision.dependency_of)}"
    if not test_case.applicability.endpoint_refs:
        if test_case.role in {"setup", "token", "consent"}:
            return "Setup coverage", "Generated automatically for the selected security profile"
        if test_case.role == "security":
            return "Security coverage", "Generated automatically for the selected security profile"
        return "Catalogue coverage", "Generated automatically for the selected security profile"
    if test_case.applicability.required_capability_ids:
        labels = [
            selected_capabilities.get(capability_id, (capability_id, False))
            for capability_id in test_case.applicability.required_capability_ids
        ]
        source = "Required capability" if all(required for _, required in labels) else "Selected capability"
        return source, ", ".join(label for label, _ in labels)
    endpoints = ", ".join(f"{endpoint.method} {endpoint.path}" for endpoint in test_case.applicability.endpoint_refs)
    return "Selected endpoint", endpoints


def _test_case_phase(test_case: CatalogueTestCase) -> str:
    """Return the review phase for a generated catalogue test case.

    Args:
        test_case: Generated catalogue test case.

    Returns:
        ``"setup"`` for setup/security/token cases, otherwise ``"execution"``.
    """
    return "setup" if test_case.role in {"setup", "security", "token"} else "execution"
