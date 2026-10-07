"""Builder step order, step-bar state, and safe ``next`` resolution."""

from __future__ import annotations

import pytest

from conformance.api.builder_draft_store import BuilderDraft
from conformance.api.builder_steps import (
    DIRECT_ENDPOINT_FLOW,
    RESOURCE_GROUP_FLOW,
    builder_flow,
    first_blocking_step,
    following_step,
    navigates_backward,
    previous_step,
    resolve_next,
    step_bar,
)

pytestmark = pytest.mark.unit


def _read_write_draft() -> BuilderDraft:
    return BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="read-write", version="4.0.1"
    )


def _dcr_draft() -> BuilderDraft:
    return BuilderDraft.create().with_catalogue_boundary(
        scheme="open-banking-uk", specification="dynamic-client-registration", version="3.4"
    )


def test_flow_order_follows_the_specification() -> None:
    assert builder_flow(BuilderDraft.create()) == RESOURCE_GROUP_FLOW
    assert [step.step_id for step in builder_flow(_read_write_draft())] == [
        "catalogue",
        "scope",
        "security",
        "config",
        "review",
    ]
    assert builder_flow(_dcr_draft()) == DIRECT_ENDPOINT_FLOW
    assert [step.step_id for step in DIRECT_ENDPOINT_FLOW] == ["catalogue", "scope", "discovery", "security", "review"]


def test_every_step_is_locked_until_a_specification_is_selected() -> None:
    items = step_bar(BuilderDraft.create(), "catalogue")

    assert [(item.step_id, item.state) for item in items] == [
        ("catalogue", "current"),
        ("scope", "locked"),
        ("security", "locked"),
        ("config", "locked"),
        ("review", "locked"),
    ]
    assert not any(item.clickable for item in items)


def test_selected_specification_opens_every_step_with_its_saved_status() -> None:
    items = step_bar(
        _read_write_draft(),
        "security",
        {"catalogue": "complete", "scope": "attention", "security": "complete"},
    )

    assert [(item.step_id, item.state) for item in items] == [
        ("catalogue", "complete"),
        ("scope", "attention"),
        ("security", "current"),
        ("config", "not_started"),
        ("review", "not_started"),
    ]
    assert [item.number for item in items] == [1, 2, 3, 4, 5]
    assert [item.clickable for item in items] == [True, True, False, True, True]


def test_first_blocking_step_only_gates_on_the_specification() -> None:
    assert first_blocking_step(BuilderDraft.create(), "catalogue") is None
    assert first_blocking_step(BuilderDraft.create(), "review") is not None
    for target in ("security", "scope", "config", "review"):
        assert first_blocking_step(_read_write_draft(), target) is None


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
    assert resolve_next(raw_value, _read_write_draft()) is None


def test_resolve_next_rejects_locked_steps_and_steps_outside_the_flow() -> None:
    assert resolve_next("review", BuilderDraft.create()) is None
    assert resolve_next("config", _dcr_draft()) is None
    assert resolve_next("discovery", _read_write_draft()) is None
    resolved = resolve_next("review", _read_write_draft())
    assert resolved is not None
    assert resolved.url_name == "builder-review"


def test_navigates_backward_compares_flow_positions() -> None:
    draft = _read_write_draft()
    flow = {step.step_id: step for step in builder_flow(draft)}

    assert navigates_backward(draft, "security", flow["scope"])
    assert not navigates_backward(draft, "scope", flow["security"])
    assert not navigates_backward(draft, "scope", flow["scope"])
    assert not navigates_backward(draft, "config", flow["review"])
    dcr_flow = {step.step_id: step for step in builder_flow(_dcr_draft())}
    assert not navigates_backward(_dcr_draft(), "config", dcr_flow["discovery"])


def test_invalid_field_values_round_trip_through_the_session() -> None:
    draft = BuilderDraft.create().with_invalid_field_values("discovery", {"discovery_url": "not a url"})

    session_object = draft.to_session_object()
    decoded = BuilderDraft.from_session_object(session_object)

    assert decoded is not None
    assert decoded.invalid_field_values == {"discovery": {"discovery_url": "not a url"}}
    assert decoded.with_invalid_field_values("discovery", {}).invalid_field_values == {}


def test_saved_steps_round_trip_through_the_session() -> None:
    draft = BuilderDraft.create().with_steps_saved("scope").with_steps_saved("config", "scope")

    decoded = BuilderDraft.from_session_object(draft.to_session_object())

    assert decoded is not None
    assert decoded.saved_steps == ("scope", "config")


def test_sessions_without_saved_steps_decode_as_nothing_saved() -> None:
    session_object = dict(BuilderDraft.create().to_session_object())
    del session_object["savedSteps"]

    decoded = BuilderDraft.from_session_object(session_object)

    assert decoded is not None
    assert decoded.saved_steps == ()


@pytest.mark.parametrize(
    "raw_value",
    [None, "x", ["discovery"], {"unknown": {"a": "b"}}, {"discovery": "x"}, {"discovery": {"a": 1}}],
)
def test_malformed_invalid_field_values_decode_as_nothing_retained(raw_value: object) -> None:
    session_object = dict(BuilderDraft.create().to_session_object())
    if raw_value is not None:
        session_object["invalidFieldValues"] = raw_value  # type: ignore[assignment]  # deliberately malformed session JSON
    session_object["completedSteps"] = ["catalogue"]  # legacy key from older sessions is ignored

    decoded = BuilderDraft.from_session_object(session_object)

    assert decoded is not None
    assert decoded.invalid_field_values == {}
