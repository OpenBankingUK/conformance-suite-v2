"""Build the v1 execution schedule from manifest metadata and test-plan selection.

Window 1 introduces additive manifest metadata (``phase`` and ``group``)
without changing the public executor API. This module derives a deterministic
schedule shape that later orchestration slices can consume directly:

1. selected setup steps (manifest order), then
2. selected execution steps partitioned into ordered groups.
"""

from __future__ import annotations

from dataclasses import dataclass

from conformance.manifest import Manifest, V1Step
from conformance.test_plan import TestPlan


@dataclass(frozen=True)
class ExecutionGroup:
    """Ordered selected steps for one execution group.

    Attributes:
        group_id: Manifest-authored execution group identifier.
        steps: Selected steps in this group, preserving manifest order.
    """

    group_id: str
    steps: tuple[V1Step, ...]


@dataclass(frozen=True)
class ExecutionSchedule:
    """Resolved schedule for a v1 manifest run.

    Attributes:
        setup_steps: Selected steps whose ``phase`` is ``"setup"``, in
            manifest order.
        execution_groups: Selected execution-phase steps partitioned by group.
            Group order is deterministic and follows first appearance in
            manifest order.
    """

    setup_steps: tuple[V1Step, ...]
    execution_groups: tuple[ExecutionGroup, ...]


def build_execution_schedule(manifest: Manifest, plan: TestPlan) -> ExecutionSchedule:
    """Derive selected setup steps and ordered execution groups.

    Args:
        manifest: Parsed manifest that provides ordered step metadata.
        plan: Selected/deselected step plan for this run.

    Returns:
        A deterministic schedule for v1 manifests. For v0 manifests,
        returns an empty schedule because v0 has no phase/group metadata.
    """
    if manifest.schema_version != "v1":
        return ExecutionSchedule(setup_steps=(), execution_groups=())

    selected_step_ids = set(plan.selected_step_ids())
    setup_steps: list[V1Step] = []
    grouped_steps: dict[str, list[V1Step]] = {}
    group_order: list[str] = []

    for step in manifest.steps:
        if step.id not in selected_step_ids:
            continue
        if step.phase == "setup":
            setup_steps.append(step)
            continue
        if step.group not in grouped_steps:
            grouped_steps[step.group] = []
            group_order.append(step.group)
        grouped_steps[step.group].append(step)

    execution_groups = tuple(
        ExecutionGroup(group_id=group_id, steps=tuple(grouped_steps[group_id])) for group_id in group_order
    )
    return ExecutionSchedule(setup_steps=tuple(setup_steps), execution_groups=execution_groups)
