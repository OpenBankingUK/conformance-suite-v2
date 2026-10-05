"""Builder step order, step-bar state, and safe ``next`` resolution."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conformance.api.builder_draft_store import BUILDER_STEP_IDS, BuilderDraft
from conformance.api.builder_steps import (
    DIRECT_ENDPOINT_FLOW,
    RESOURCE_GROUP_FLOW,
    builder_flow,
    completed_steps_after_catalogue_save,
    first_blocking_step,
    following_step,
    previous_step,
    resolve_next,
    step_bar,
)

pytestmark = pytest.mark.unit


def _read_write_draft(*completed: str) -> BuilderDraft:
    draft = BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="read-write", version="4.0.1"
    )
    return replace(draft, completed_steps=tuple(step for step in BUILDER_STEP_IDS if step in completed))


def _dcr_draft(*completed: str) -> BuilderDraft:
    draft = BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="dynamic-client-registration", version="3.4"
    )
    return replace(draft, completed_steps=tuple(step for step in BUILDER_STEP_IDS if step in completed))


def test_flow_order_follows_the_specification() -> None:
    assert builder_flow(BuilderDraft.create()) == RESOURCE_GROUP_FLOW
    assert [step.step_id for step in builder_flow(_read_write_draft())] == [
        "catalogue",
        "discovery",
        "security",
        "scope",
        "config",
        "review",
    ]
    assert builder_flow(_dcr_draft()) == DIRECT_ENDPOINT_FLOW
    assert [step.step_id for step in DIRECT_ENDPOINT_FLOW] == ["catalogue", "scope", "discovery", "security", "review"]


def test_step_bar_unlocks_steps_whose_predecessors_are_complete() -> None:
    items = step_bar(_read_write_draft("catalogue", "discovery"), "security")

    assert [(item.step_id, item.state) for item in items] == [
        ("catalogue", "complete"),
        ("discovery", "complete"),
        ("security", "current"),
        ("scope", "locked"),
        ("config", "locked"),
        ("review", "locked"),
    ]
    assert [item.number for item in items] == [1, 2, 3, 4, 5, 6]
    assert [item.clickable for item in items] == [True, True, False, False, False, False]


def test_step_bar_marks_the_next_unsaved_step_available() -> None:
    items = step_bar(_read_write_draft("catalogue", "discovery"), "catalogue")

    assert [(item.step_id, item.state) for item in items][:4] == [
        ("catalogue", "current"),
        ("discovery", "complete"),
        ("security", "available"),
        ("scope", "locked"),
    ]


def test_fully_completed_draft_can_jump_anywhere_including_review() -> None:
    items = step_bar(_dcr_draft(*BUILDER_STEP_IDS), "catalogue")

    assert [(item.step_id, item.clickable) for item in items] == [
        ("catalogue", False),
        ("scope", True),
        ("discovery", True),
        ("security", True),
        ("review", True),
    ]


def test_first_blocking_step_reports_the_earliest_incomplete_step() -> None:
    draft = _read_write_draft("catalogue", "security")

    assert first_blocking_step(draft, "catalogue") is None
    assert first_blocking_step(draft, "discovery") is None
    blocker = first_blocking_step(draft, "review")
    assert blocker is not None
    assert blocker.url_name == "builder-discovery-config"


def test_previous_and_following_steps_use_the_draft_flow() -> None:
    draft = _dcr_draft()

    previous = previous_step(draft, "discovery")
    assert previous is not None
    assert previous.step_id == "scope"
    assert previous_step(draft, "catalogue") is None
    assert previous_step(draft, "config") is None
    assert following_step(draft, "catalogue").step_id == "scope"
    assert following_step(draft, "security").step_id == "review"
    assert following_step(draft, "config").step_id == "review"


@pytest.mark.parametrize(
    "raw_value",
    [None, "", "https://evil.example/", "//evil.example", "builder-review", "/builder/x/review/", "Review", "back"],
)
def test_resolve_next_rejects_values_outside_the_step_allow_list(raw_value: str | None) -> None:
    assert resolve_next(raw_value, _read_write_draft(*BUILDER_STEP_IDS)) is None


def test_resolve_next_rejects_locked_steps_and_steps_outside_the_flow() -> None:
    assert resolve_next("review", _read_write_draft("catalogue")) is None
    assert resolve_next("config", _dcr_draft(*BUILDER_STEP_IDS)) is None
    resolved = resolve_next("review", _read_write_draft(*BUILDER_STEP_IDS))
    assert resolved is not None
    assert resolved.url_name == "builder-review"


def test_changing_specification_invalidates_every_later_step() -> None:
    previous = _read_write_draft(*BUILDER_STEP_IDS)
    updated = previous.with_catalogue_boundary(
        scheme="open-banking-uk", specification="dynamic-client-registration", version="3.4"
    )

    assert completed_steps_after_catalogue_save(previous, updated) == ("catalogue",)
    assert completed_steps_after_catalogue_save(BuilderDraft.create(), previous) == ("catalogue",)


def test_version_change_keeps_later_steps_unless_scope_was_pruned() -> None:
    previous = _read_write_draft(*BUILDER_STEP_IDS).with_scope_selection(
        resource_group_ids=("AIS",), endpoint_ids=(), endpoint_capability_ids={}
    )
    updated = previous.with_catalogue_boundary(scheme="open-banking-uk", specification="read-write", version="4.0.0")

    assert completed_steps_after_catalogue_save(previous, updated) == BUILDER_STEP_IDS
    pruned = updated.with_scope_selection(resource_group_ids=(), endpoint_ids=(), endpoint_capability_ids={})
    assert completed_steps_after_catalogue_save(previous, pruned) == ("catalogue", "discovery", "security")


def test_completed_steps_round_trip_through_the_session() -> None:
    draft = BuilderDraft.create().with_completed_step("security").with_completed_step("catalogue")
    assert draft.completed_steps == ("catalogue", "security")

    session_object = draft.to_session_object()
    assert session_object["completedSteps"] == ["catalogue", "security"]
    decoded = BuilderDraft.from_session_object(session_object)
    assert decoded is not None
    assert decoded.completed_steps == ("catalogue", "security")


@pytest.mark.parametrize("raw_value", [None, "catalogue", ["catalogue", 1], ["unknown"]])
def test_legacy_or_malformed_completed_steps_decode_as_nothing_complete(raw_value: object) -> None:
    session_object = dict(BuilderDraft.create().to_session_object())
    if raw_value is None:
        session_object.pop("completedSteps")
    else:
        session_object["completedSteps"] = raw_value  # type: ignore[assignment]  # deliberately malformed session JSON

    decoded = BuilderDraft.from_session_object(session_object)

    assert decoded is not None
    assert decoded.completed_steps == ()
