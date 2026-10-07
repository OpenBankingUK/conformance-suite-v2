"""Loopback component tests for the CLI's built-in PSU callback HTTPS listener.

The listener stands in for the Django ``/callback/`` view during CLI runs, so
these tests exercise real TLS sockets on loopback (justified: socket, HTTP
framing, and TLS behaviour are under test) and assert the same security
properties: one-shot state correlation, a uniform rejection page, the hybrid
fragment bridge, and no listener outside the callback path.
"""

from __future__ import annotations

import ssl
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from conformance.api.auth_session_store import AuthSessionStore
from conformance.cli_callback import CallbackListenerError, callback_bind_address, psu_callback_listener

pytestmark = pytest.mark.component

_REDIRECT_URI = "https://127.0.0.1:8443/callback/"


@pytest.fixture
def listener(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, AuthSessionStore]]:
    """Run a listener on an ephemeral loopback port with an ephemeral certificate.

    Args:
        monkeypatch: Used to hide any container TLS material on the host.

    Yields:
        Base URL of the listener and the session store it resolves.
    """
    monkeypatch.setattr("conformance.cli_callback.CONTAINER_TLS_DIRECTORIES", (Path("/nonexistent"),))
    store = AuthSessionStore()
    with psu_callback_listener(_REDIRECT_URI, session_store=store, listen="127.0.0.1:0") as (host, port):
        yield f"https://{host}:{port}", store


def _client() -> httpx.Client:
    """Return an HTTPS client that accepts the listener's self-signed certificate."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return httpx.Client(verify=context)


def test_listener_captures_code_for_registered_state(listener: tuple[str, AuthSessionStore]) -> None:
    base_url, store = listener
    session = store.register("run-1")

    with _client() as client:
        response = client.get(f"{base_url}/callback/", params={"state": session.state, "code": "abc"})
        replay = client.get(f"{base_url}/callback", params={"state": session.state, "code": "abc"})

    resolved = store.get("run-1", session.state)
    assert response.status_code == 200
    assert "Authorization code received" in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert resolved is not None
    assert resolved.status == "captured"
    assert resolved.code == "abc"
    assert replay.status_code == 400
    assert "Invalid or expired callback." in replay.text


def test_listener_captures_oauth_error(listener: tuple[str, AuthSessionStore]) -> None:
    base_url, store = listener
    session = store.register("run-1")

    with _client() as client:
        response = client.get(
            f"{base_url}/callback/",
            params={"state": session.state, "error": "access_denied", "error_description": "<b>no</b>"},
        )

    resolved = store.get("run-1", session.state)
    assert response.status_code == 200
    assert "<b>" not in response.text
    assert resolved is not None
    assert resolved.status == "error"
    assert resolved.error == "access_denied"


def test_listener_rejects_unknown_state_uniformly(listener: tuple[str, AuthSessionStore]) -> None:
    base_url, _store = listener

    with _client() as client:
        unknown = client.get(f"{base_url}/callback/", params={"state": "x" * 32, "code": "abc"})
        no_code = client.get(f"{base_url}/callback/", params={"state": "x" * 32})

    assert unknown.status_code == no_code.status_code == 400
    assert unknown.text == no_code.text


def test_listener_serves_fragment_bridge_without_query(listener: tuple[str, AuthSessionStore]) -> None:
    base_url, _store = listener

    with _client() as client:
        response = client.get(f"{base_url}/callback/")

    assert response.status_code == 200
    assert "window.location.hash" in response.text
    assert "id_token" not in response.text


def test_listener_only_serves_callback_path(listener: tuple[str, AuthSessionStore]) -> None:
    base_url, _store = listener

    with _client() as client:
        response = client.get(f"{base_url}/admin/")
        post = client.post(f"{base_url}/callback/", data={"state": "s"})

    assert response.status_code == 404
    assert post.status_code == 501


def test_listener_reports_bind_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("conformance.cli_callback.CONTAINER_TLS_DIRECTORIES", (Path("/nonexistent"),))
    store = AuthSessionStore()
    with psu_callback_listener(_REDIRECT_URI, session_store=store, listen="127.0.0.1:0") as (_host, port):
        with pytest.raises(CallbackListenerError, match="--callback-listen"):
            with psu_callback_listener(_REDIRECT_URI, session_store=store, listen=f"127.0.0.1:{port}"):
                pass


@pytest.mark.parametrize(
    ("redirect_uri", "listen", "expected"),
    [
        ("https://127.0.0.1:8443/callback/", None, ("127.0.0.1", 8443, "/callback/")),
        ("https://localhost/cb", None, ("127.0.0.1", 443, "/cb")),
        ("https://0.0.0.0:8443/conformancesuite/callback", None, ("0.0.0.0", 8443, "/conformancesuite/callback")),  # noqa: S104 - expected bind address for 0.0.0.0 redirect URIs.
        ("https://tpp.example.com/cb", "127.0.0.1:9443", ("127.0.0.1", 9443, "/cb")),
    ],
)
def test_bind_address_derivation(
    monkeypatch: pytest.MonkeyPatch,
    redirect_uri: str,
    listen: str | None,
    expected: tuple[str, int, str],
) -> None:
    monkeypatch.setattr("conformance.cli_callback._running_in_container", lambda: False)

    assert callback_bind_address(redirect_uri, listen=listen) == expected


@pytest.mark.parametrize(
    ("redirect_uri", "listen"),
    [("http://127.0.0.1/cb", None), ("https://127.0.0.1/cb", "no-port"), ("https://127.0.0.1/cb", "h:99999")],
)
def test_bind_address_rejects_invalid_input(redirect_uri: str, listen: str | None) -> None:
    with pytest.raises(CallbackListenerError):
        callback_bind_address(redirect_uri, listen=listen)
