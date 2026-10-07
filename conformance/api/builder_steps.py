"""Ordered guided-builder steps, step-bar state, and safe step navigation.

The builder wizard has two flow shapes. Specifications that use Read/Write
resource groups choose scope first, because the selected tests decide which
connection and security values are required to run, then collect connection
and security (including OpenID discovery) and business data; direct-endpoint
specifications such as Open Banking DCR 3.4 choose endpoints straight after
the specification and keep discovery as its own step. This module is the single place
that defines those orders, decides which steps a participant may jump to, and
maps a submitted ``next`` value to an internal route so it can never become an
open redirect.

Navigation has a single gate: a supported specification must be selected
before any later step opens, because every later page depends on it. After
that the participant may move freely between steps; leaving a page always
saves what was entered, and completeness is reported per step and enforced
only at review and export.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from conformance.api.builder_draft_store import BuilderDraft, BuilderStepId
from conformance.api.builder_wizard import boundary_requires_resource_groups, catalogue_boundary_continue_blocker
from conformance.catalogue import PlanDocumentBoundary

type BuilderNavigationTarget = BuilderStepId | Literal["review"]
"""Builder page the step bar can navigate to: a saved step or the review page."""

type StepProgress = Literal["complete", "attention", "not_started"]
"""Saved-data status of one builder step: no issues, needs attention, or empty."""

type StepState = Literal["current", "complete", "attention", "not_started", "locked"]
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
            True for every unlocked step other than the current page.
        """
        return self.state not in {"current", "locked"}


_CATALOGUE = BuilderStepDefinition("catalogue", "Specification", "builder-catalogue-boundary")
_DISCOVERY = BuilderStepDefinition("discovery", "Discovery", "builder-discovery-config")
_SECURITY = BuilderStepDefinition("security", "Security", "builder-security-config")
_CONNECTION_SECURITY = BuilderStepDefinition("security", "Connection & security", "builder-security-config")
_SCOPE = BuilderStepDefinition("scope", "Scope", "builder-scope")
_CONFIG = BuilderStepDefinition("config", "Business data", "builder-config")
_REVIEW = BuilderStepDefinition("review", "Review", "builder-review")

RESOURCE_GROUP_FLOW: tuple[BuilderStepDefinition, ...] = (_CATALOGUE, _SCOPE, _CONNECTION_SECURITY, _CONFIG, _REVIEW)
"""Step order for Read/Write-style specifications that use resource groups.

OpenID discovery is part of the connection and security page in this flow.
"""

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


def navigates_backward(draft: BuilderDraft, current: BuilderNavigationTarget, target: BuilderStepDefinition) -> bool:
    """Return whether ``target`` comes before ``current`` in the draft's flow.

    Args:
        draft: Builder draft.
        current: Page that was submitted.
        target: Page the participant is going to.

    Returns:
        True for Back or a step-bar jump to an earlier step.
    """
    order = [step.step_id for step in builder_flow(draft)]
    if current not in order or target.step_id not in order:
        return False
    return order.index(target.step_id) < order.index(current)


def specification_selected(draft: BuilderDraft) -> bool:
    """Return whether the draft has a supported specification selected.

    Args:
        draft: Builder draft.

    Returns:
        True when the saved boundary is supported for building a plan.
    """
    boundary = _draft_boundary(draft)
    return boundary is not None and catalogue_boundary_continue_blocker(boundary) is None


def first_blocking_step(draft: BuilderDraft, target: BuilderNavigationTarget) -> BuilderStepDefinition | None:
    """Return the step that must be saved before ``target`` can be opened.

    The specification is the only prerequisite: every other page depends on
    it, while incomplete later pages never block navigation.

    Args:
        draft: Builder draft.
        target: Page the participant is trying to open.

    Returns:
        The specification step when ``target`` is a later page and no
        supported specification is selected, otherwise ``None``.
    """
    if target == "catalogue" or specification_selected(draft):
        return None
    return _CATALOGUE


def step_is_available(draft: BuilderDraft, target: BuilderNavigationTarget) -> bool:
    """Return whether a builder page may be opened for a draft.

    Args:
        draft: Builder draft.
        target: Page the participant is trying to open.

    Returns:
        True when ``target`` is in the draft's flow and not blocked by the
        specification gate.
    """
    return any(step.step_id == target for step in builder_flow(draft)) and first_blocking_step(draft, target) is None


def step_bar(
    draft: BuilderDraft,
    current: BuilderNavigationTarget,
    progress: Mapping[BuilderNavigationTarget, StepProgress] | None = None,
) -> tuple[StepBarItem, ...]:
    """Return step-bar entries for a builder page.

    Args:
        draft: Builder draft as currently saved.
        current: Page being rendered.
        progress: Saved-data status per step; steps without an entry are shown
            as not started.

    Returns:
        One entry per page in the draft's flow.
    """
    statuses = progress or {}
    items: list[StepBarItem] = []
    for number, step in enumerate(builder_flow(draft), start=1):
        state: StepState
        if step.step_id == current:
            state = "current"
        elif not step_is_available(draft, step.step_id):
            state = "locked"
        else:
            state = statuses.get(step.step_id, "not_started")
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
