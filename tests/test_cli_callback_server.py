"""Unit tests for the standalone CLI OAuth callback listener helpers."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from conformance.api.auth_session_store import AuthSessionStore
from conformance.cli_callback_server import (
    _capture_callback_query,
    _fragment_bridge_html,
    needs_cli_callback_listener,
)


@pytest.mark.unit
def test_needs_cli_callback_listener_only_for_loopback_manual_psu() -> None:
    """Only loopback manual PSU flows start the standalone listener."""
    assert needs_cli_callback_listener(
        redirect_uri="https://0.0.0.0:8443/conformancesuite/callback",
        has_manual_psu_step=True,
    )
    assert not needs_cli_callback_listener(
        redirect_uri="https://conformance.example.com/callback",
        has_manual_psu_step=True,
    )
    assert not needs_cli_callback_listener(
        redirect_uri="https://0.0.0.0:8443/conformancesuite/callback",
        has_manual_psu_step=False,
    )


@pytest.mark.unit
def test_capture_callback_query_records_authorization_code() -> None:
    """A valid callback query resolves the registered auth session."""
    store = AuthSessionStore()
    session = store.register("run-123")

    status, body = _capture_callback_query(
        auth_session_store=store,
        query={"state": [session.state], "code": ["auth-code-123"], "id_token": ["ignored"]},
    )

    captured = store.get("run-123", session.state)
    assert status == HTTPStatus.OK
    assert "Authorization code received" in body
    assert captured is not None
    assert captured.status == "captured"
    assert captured.code == "auth-code-123"


@pytest.mark.unit
def test_capture_callback_query_records_authorization_error() -> None:
    """A valid OAuth error callback resolves the registered auth session."""
    store = AuthSessionStore()
    session = store.register("run-123")

    status, body = _capture_callback_query(
        auth_session_store=store,
        query={
            "state": [session.state],
            "error": ["access_denied"],
            "error_description": ["PSU cancelled"],
        },
    )

    captured = store.get("run-123", session.state)
    assert status == HTTPStatus.OK
    assert "Authorization failed" in body
    assert captured is not None
    assert captured.status == "error"
    assert captured.error == "access_denied"
    assert captured.error_description == "PSU cancelled"


@pytest.mark.unit
def test_capture_callback_query_rejects_unknown_state() -> None:
    """Unknown states return a generic rejection without disclosing details."""
    store = AuthSessionStore()

    status, body = _capture_callback_query(
        auth_session_store=store,
        query={"state": ["unknown-state"], "code": ["auth-code-123"]},
    )

    assert status == HTTPStatus.BAD_REQUEST
    assert "Invalid or expired callback" in body


@pytest.mark.unit
def test_fragment_bridge_replays_code_without_id_token() -> None:
    """The fragment bridge forwards code/state but deliberately drops id_token."""
    html = _fragment_bridge_html()

    assert 'fragment.get("state")' in html
    assert 'fragment.get("code")' in html
    assert 'query.set("code", code)' in html
    assert "id_token" not in html
