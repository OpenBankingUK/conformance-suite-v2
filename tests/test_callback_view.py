"""Integration tests for the public PSU authorization callback view."""

from __future__ import annotations

import json

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import run_store


@pytest.fixture(autouse=True)
def _reset_stores() -> None:
    """Reset the global stores between tests to avoid cross-contamination."""
    run_store.reset()
    auth_session_store.reset()


def _registered_state() -> tuple[str, str]:
    """Create a run and register an auth session, returning ``(run_id, state)``.

    Returns:
        A tuple of the new run's id and the server-generated state token.
    """
    record = run_store.create_run()
    session = auth_session_store.register(record.run_id)
    return record.run_id, session.state


@pytest.mark.integration
class TestCallbackView:
    def test_get_with_code_captures_session_and_renders_success(self) -> None:
        run_id, state = _registered_state()
        client = Client()

        response = client.get("/callback/", {"state": state, "code": "auth-code-xyz"})

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "Authorization code received" in body
        # Raw values must never appear in the rendered HTML.
        assert state not in body
        assert "auth-code-xyz" not in body

        session = auth_session_store.get(run_id, state)
        assert session is not None
        assert session.status == "captured"
        assert session.code == "auth-code-xyz"

    def test_get_with_error_captures_session_and_renders_error(self) -> None:
        run_id, state = _registered_state()
        client = Client()

        response = client.get(
            "/callback/",
            {
                "state": state,
                "error": "access_denied",
                "error_description": "psu cancelled",
            },
        )

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "Authorization failed" in body
        assert "access_denied" in body
        # Free-text description must not be reflected.
        assert "psu cancelled" not in body

        session = auth_session_store.get(run_id, state)
        assert session is not None
        assert session.status == "error"
        assert session.error == "access_denied"
        assert session.error_description == "psu cancelled"

    def test_unknown_state_returns_generic_400(self) -> None:
        client = Client()

        response = client.get("/callback/", {"state": "x" * 32, "code": "irrelevant"})

        assert response.status_code == 400
        body = response.content.decode("utf-8")
        assert "Invalid or expired callback" in body
        # The rejected state value must not be echoed back.
        assert "x" * 32 not in body

    def test_already_resolved_state_returns_generic_400(self) -> None:
        _run_id, state = _registered_state()
        client = Client()
        client.get("/callback/", {"state": state, "code": "first"})

        response = client.get("/callback/", {"state": state, "code": "second"})

        assert response.status_code == 400
        body = response.content.decode("utf-8")
        assert "Invalid or expired callback" in body

    def test_missing_state_returns_generic_400(self) -> None:
        client = Client()

        response = client.get("/callback/", {"code": "irrelevant"})

        assert response.status_code == 400
        assert "Invalid or expired callback" in response.content.decode("utf-8")

    def test_missing_code_and_error_returns_generic_400(self) -> None:
        _run_id, state = _registered_state()
        client = Client()

        response = client.get("/callback/", {"state": state})

        assert response.status_code == 400
        # Session must remain in awaiting state — no spurious capture.
        session = auth_session_store.get(_run_id, state)
        assert session is not None
        assert session.status == "awaiting"

    def test_post_is_rejected(self) -> None:
        client = Client()

        response = client.post("/callback/", data={"state": "x" * 32, "code": "y"})

        assert response.status_code == 405

    def test_non_loopback_caller_is_allowed(self) -> None:
        run_id, state = _registered_state()
        # The callback is intentionally NOT loopback-guarded — see
        # callback_views module docstring for the security rationale.
        client = Client(REMOTE_ADDR="10.0.0.5")

        response = client.get("/callback/", {"state": state, "code": "auth-code"})

        assert response.status_code == 200
        session = auth_session_store.get(run_id, state)
        assert session is not None
        assert session.status == "captured"

    def test_callback_emits_masked_event_into_run_log(self) -> None:
        run_id, state = _registered_state()
        client = Client()

        client.get("/callback/", {"state": state, "code": "auth-code-xyz"})

        record = run_store.get_run(run_id)
        assert record is not None
        assert record.execution_logger is not None
        events = record.execution_logger.events()
        callback_events = [e for e in events if e.type == "auth-callback-received"]
        assert len(callback_events) == 1
        payload = callback_events[0].payload
        assert payload["state"] == state
        # The raw code must be masked in the NDJSON log.
        assert payload["code"] != "auth-code-xyz"
        # And not present anywhere in the serialised bytes.
        ndjson = record.execution_logger.to_ndjson_bytes()
        assert b"auth-code-xyz" not in ndjson
        # Sanity-check NDJSON is well-formed JSON-per-line.
        for line in ndjson.splitlines():
            json.loads(line)

    def test_error_callback_emits_event_with_error_description(self) -> None:
        # The ASPSP-reported-error path must include ``error_description``
        # (snake_case, matching the rest of the execution-log event
        # taxonomy) in the ``auth-callback-received`` payload so log
        # consumers retain the full RFC 6749 §4.1.2.1 diagnostic.
        run_id, state = _registered_state()
        client = Client()

        client.get(
            "/callback/",
            {
                "state": state,
                "error": "access_denied",
                "error_description": "psu cancelled",
            },
        )

        record = run_store.get_run(run_id)
        assert record is not None
        assert record.execution_logger is not None
        callback_events = [e for e in record.execution_logger.events() if e.type == "auth-callback-received"]
        assert len(callback_events) == 1
        payload = callback_events[0].payload
        assert payload["state"] == state
        assert payload["error"] == "access_denied"
        assert payload["error_description"] == "psu cancelled"
        assert "code" not in payload

    def test_callback_for_unknown_run_still_returns_200(self) -> None:
        # Register a session, then drop the run record so the logger lookup
        # in the view returns None. The session itself survives and is
        # still captured; the view must not crash on the missing logger.
        record = run_store.create_run()
        session = auth_session_store.register(record.run_id)
        run_id = record.run_id
        # Reset the run store directly (bypassing the autouse fixture which
        # would also wipe the auth session we want to keep).
        run_store._runs.clear()  # noqa: SLF001 — test-only setup
        run_store._active_run_id = None  # noqa: SLF001 — test-only setup
        client = Client()

        response = client.get("/callback/", {"state": session.state, "code": "x"})

        assert response.status_code == 200
        captured = auth_session_store.get(run_id, session.state)
        assert captured is not None
        assert captured.status == "captured"

    def test_error_redirect_with_stray_code_emits_only_error_payload(self) -> None:
        # A malformed/hostile redirect that carries both ``error`` and
        # ``code`` must resolve to an ``error`` session (the view branches
        # on ``error`` first) AND the emitted execution-log payload must
        # reflect the stored outcome — no stray ``code`` field that could
        # be unmasked under a future developer-mode toggle.
        run_id, state = _registered_state()
        client = Client()

        client.get(
            "/callback/",
            {
                "state": state,
                "error": "access_denied",
                "code": "should-not-be-logged",
                "error_description": "psu cancelled",
            },
        )

        record = run_store.get_run(run_id)
        assert record is not None
        assert record.execution_logger is not None
        callback_events = [e for e in record.execution_logger.events() if e.type == "auth-callback-received"]
        assert len(callback_events) == 1
        payload = callback_events[0].payload
        assert payload["state"] == state
        assert payload["error"] == "access_denied"
        assert payload["error_description"] == "psu cancelled"
        assert "code" not in payload
        # Belt-and-braces: the stray code value must not appear in the
        # serialised NDJSON either.
        ndjson = record.execution_logger.to_ndjson_bytes()
        assert b"should-not-be-logged" not in ndjson

    def test_success_redirect_with_stray_error_description_is_not_logged(self) -> None:
        # The success path must not carry ``error_description`` into the
        # log payload just because the redirect query included it — the
        # payload shape is driven by the resolved session, not by raw
        # query parameters.
        run_id, state = _registered_state()
        client = Client()

        client.get(
            "/callback/",
            {
                "state": state,
                "code": "auth-code-xyz",
                "error_description": "leftover from a previous attempt",
            },
        )

        record = run_store.get_run(run_id)
        assert record is not None
        assert record.execution_logger is not None
        callback_events = [e for e in record.execution_logger.events() if e.type == "auth-callback-received"]
        assert len(callback_events) == 1
        payload = callback_events[0].payload
        assert payload["state"] == state
        assert "code" in payload
        assert "error" not in payload
        assert "error_description" not in payload
