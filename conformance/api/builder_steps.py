"""Ordered guided-builder steps, step-bar state, and safe step navigation.

The builder wizard has two flow shapes. Specifications that use Read/Write
resource groups collect the security environment before scope and business
data; direct-endpoint specifications such as Open Banking DCR 3.4 choose
endpoints straight after the specification. This module is the single place
that defines those orders, decides which steps a participant may jump to, and
maps a submitted ``next`` value to an internal route so it can never become an
open redirect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conformance.api.builder_draft_store import BUILDER_STEP_IDS, BuilderDraft, BuilderStepId
from conformance.api.builder_wizard import boundary_requires_resource_groups
from conformance.catalogue import PlanDocumentBoundary

type BuilderNavigationTarget = BuilderStepId | Literal["review"]
"""Builder page the step bar can navigate to: a saved step or the review page."""

type StepState = Literal["current", "complete", "available", "locked"]
"""Step-bar presentation state for one builder step."""

BACK_NEXT_VALUE = "back"
"""``next`` form value submitted by a step's Back button."""


@dataclass(frozen=True)
class BuilderStepDefinition:
    """One page in the guided builder flow.

    Attributes:
        step_id: Stable id submitted as the ``next`` form value.
        label: Participant-facing step-bar label.
        url_name: Django URL name of the step page; takes a ``draft_id``.
    """

    step_id: BuilderNavigationTarget
    label: str
    url_name: str


@dataclass(frozen=True)
class StepBarItem:
    """Rendered step-bar entry.

    Attributes:
        number: One-based position in the current flow.
        step_id: Step id submitted as the ``next`` form value.
        label: Participant-facing label.
        state: Presentation state for the current page.
    """

    number: int
    step_id: BuilderNavigationTarget
    label: str
    state: StepState

    @property
    def clickable(self) -> bool:
        """Return whether the participant may jump to this step.

        Returns:
            True for completed or available steps other than the current page.
        """
        return self.state in {"complete", "available"}


_CATALOGUE = BuilderStepDefinition("catalogue", "Specification", "builder-catalogue-boundary")
_DISCOVERY = BuilderStepDefinition("discovery", "Discovery", "builder-discovery-config")
_SECURITY = BuilderStepDefinition("security", "Security", "builder-security-config")
_SCOPE = BuilderStepDefinition("scope", "Scope", "builder-scope")
_CONFIG = BuilderStepDefinition("config", "Business data", "builder-config")
_REVIEW = BuilderStepDefinition("review", "Review", "builder-review")

RESOURCE_GROUP_FLOW: tuple[BuilderStepDefinition, ...] = (_CATALOGUE, _DISCOVERY, _SECURITY, _SCOPE, _CONFIG, _REVIEW)
"""Step order for Read/Write-style specifications that use resource groups."""

DIRECT_ENDPOINT_FLOW: tuple[BuilderStepDefinition, ...] = (_CATALOGUE, _SCOPE, _DISCOVERY, _SECURITY, _REVIEW)
"""Step order for direct-endpoint specifications such as Open Banking DCR 3.4."""


def _draft_boundary(draft: BuilderDraft) -> PlanDocumentBoundary | None:
    """Return the draft's selected catalogue boundary.

    Args:
        draft: Builder draft.

    Returns:
        Boundary, or ``None`` before the specification step is saved.
    """
    if draft.scheme is None or draft.specification is None or draft.version is None:
        return None
    return PlanDocumentBoundary(scheme=draft.scheme, specification=draft.specification, version=draft.version)


def builder_flow(draft: BuilderDraft) -> tuple[BuilderStepDefinition, ...]:
    """Return the ordered builder pages for a draft's current specification.

    Args:
        draft: Builder draft.

    Returns:
        Ordered steps ending with review. Drafts without a specification use the
        resource-group flow until one is chosen.
    """
    boundary = _draft_boundary(draft)
    if boundary is None or boundary_requires_resource_groups(boundary):
        return RESOURCE_GROUP_FLOW
    return DIRECT_ENDPOINT_FLOW


def first_blocking_step(draft: BuilderDraft, target: BuilderNavigationTarget) -> BuilderStepDefinition | None:
    """Return the earliest incomplete step that must be saved before ``target``.

    Args:
        draft: Builder draft.
        target: Page the participant is trying to open.

    Returns:
        First incomplete earlier step, or ``None`` when ``target`` may be opened.
        A target outside the current flow is blocked by the first incomplete
        step in the flow, if any.
    """
    flow = builder_flow(draft)
    for step in flow:
        if step.step_id == target:
            return None
        if step.step_id != "review" and step.step_id not in draft.completed_steps:
            return step
    return None


def step_is_available(draft: BuilderDraft, target: BuilderNavigationTarget) -> bool:
    """Return whether a builder page may be opened for a draft.

    Args:
        draft: Builder draft.
        target: Page the participant is trying to open.

    Returns:
        True when ``target`` is in the draft's flow and every earlier step is
        complete.
    """
    return any(step.step_id == target for step in builder_flow(draft)) and first_blocking_step(draft, target) is None


def step_bar(draft: BuilderDraft, current: BuilderNavigationTarget) -> tuple[StepBarItem, ...]:
    """Return step-bar entries for a builder page.

    Args:
        draft: Builder draft as currently saved.
        current: Page being rendered.

    Returns:
        One entry per page in the draft's flow.
    """
    items: list[StepBarItem] = []
    for number, step in enumerate(builder_flow(draft), start=1):
        state: StepState
        if step.step_id == current:
            state = "current"
        elif not step_is_available(draft, step.step_id):
            state = "locked"
        elif step.step_id in draft.completed_steps:
            state = "complete"
        else:
            state = "available"
        items.append(StepBarItem(number=number, step_id=step.step_id, label=step.label, state=state))
    return tuple(items)


def previous_step(draft: BuilderDraft, current: BuilderNavigationTarget) -> BuilderStepDefinition | None:
    """Return the page before ``current`` in the draft's flow.

    Args:
        draft: Builder draft.
        current: Page being rendered.

    Returns:
        Previous step, or ``None`` for the first step or a page outside the flow.
    """
    flow = builder_flow(draft)
    for index, step in enumerate(flow):
        if step.step_id == current:
            return flow[index - 1] if index > 0 else None
    return None


def following_step(draft: BuilderDraft, current: BuilderNavigationTarget) -> BuilderStepDefinition:
    """Return the page after ``current`` in the draft's flow.

    Args:
        draft: Builder draft, normally as just saved.
        current: Page that was submitted.

    Returns:
        Next step, or review when ``current`` is last or outside the flow.
    """
    flow = builder_flow(draft)
    for index, step in enumerate(flow[:-1]):
        if step.step_id == current:
            return flow[index + 1]
    return _REVIEW


def resolve_next(raw_value: str | None, draft: BuilderDraft) -> BuilderStepDefinition | None:
    """Map a submitted ``next`` value to an allowed builder page.

    Only fixed step ids are accepted, never URLs or URL names, so ``next`` can
    never redirect outside the builder.

    Args:
        raw_value: Submitted ``next`` value.
        draft: Builder draft as just saved.

    Returns:
        The requested step when it is in the draft's flow and available,
        otherwise ``None``.
    """
    if not raw_value:
        return None
    for step in builder_flow(draft):
        if step.step_id == raw_value:
            return step if step_is_available(draft, step.step_id) else None
    return None


def completed_steps_after_catalogue_save(previous: BuilderDraft, updated: BuilderDraft) -> tuple[BuilderStepId, ...]:
    """Return completed steps after the specification step is saved.

    Changing the scheme or specification (or anything that changes the flow
    shape) invalidates every later step because their forms depend on it. A
    version-only change keeps later steps unless pruning unavailable choices
    changed the saved scope, in which case scope and business data must be
    revisited.

    Args:
        previous: Draft before the save.
        updated: Draft after the save, including any pruned scope.

    Returns:
        Completed step ids for the updated draft.
    """
    if (previous.scheme, previous.specification) != (updated.scheme, updated.specification) or builder_flow(
        previous
    ) != builder_flow(updated):
        return ("catalogue",)
    invalidated: set[BuilderStepId] = set()
    if (previous.resource_group_ids, previous.endpoint_ids, dict(previous.endpoint_capability_ids)) != (
        updated.resource_group_ids,
        updated.endpoint_ids,
        dict(updated.endpoint_capability_ids),
    ):
        invalidated = {"scope", "config"}
    return tuple(
        step
        for step in BUILDER_STEP_IDS
        if step == "catalogue" or (step in previous.completed_steps and step not in invalidated)
    )
