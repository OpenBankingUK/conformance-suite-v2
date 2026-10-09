"""Loopback component tests for the CLI's built-in PSU callback HTTPS listener.

The listener stands in for the Django ``/callback/`` view during CLI runs, so
these tests exercise real TLS sockets on loopback (justified: socket, HTTP
framing, and TLS behaviour are under test) and assert the same security
properties: one-shot state correlation, a uniform rejection page, the hybrid
fragment bridge, and no listener outside the callback path.
"""

from __future__ import annotations

import signal
import socket
import ssl
import threading
from collections.abc import Iterator
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.template import Context, Engine

from conformance import FEEDBACK_EMAIL
from conformance.api.auth_session_store import AuthSessionStore
from conformance.cli_callback import CallbackListenerError, callback_bind_address, psu_callback_listener
from conformance.cli_cancellation import CliCancelled

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


@pytest.mark.parametrize("outcome", ["success", "error", "fragment", "unknown", "malformed", "not-found", "too-long"])
def test_listener_shows_beta_notice_on_every_rendered_outcome(
    listener: tuple[str, AuthSessionStore], outcome: str
) -> None:
    base_url, store = listener
    session = store.register("run-beta")
    targets = {
        "success": ("/callback/", {"state": session.state, "code": "private-code"}),
        "error": ("/callback/", {"state": session.state, "error": "access_denied"}),
        "fragment": ("/callback/", {}),
        "unknown": ("/callback/", {"state": "unknown", "code": "private-code"}),
        "malformed": ("/callback/", {"state": session.state}),
        "not-found": ("/elsewhere", {}),
        "too-long": ("/callback/", {"state": "x" * 17000}),
    }
    path, params = targets[outcome]
    with _client() as client:
        response = client.get(base_url + path, params=params)

    assert response.text.count("This tool is currently in beta as part of the MVP release.") == 1
    assert "Features and behaviour may change." in response.text
    assert 'aria-label="Beta release notice"' in response.text
    template_dir = Path(__file__).parents[3] / "conformance" / "api" / "templates"
    banner = Engine(dirs=[str(template_dir)]).get_template("conformance/partials/beta_notice.html")
    ui_banner = banner.render(Context({"feedback_page": True}, use_l10n=False, use_tz=False))
    assert ui_banner.split("Features and behaviour may change.")[0] in response.text
    assert "background: #fff7d6" in response.text
    assert "Give beta feedback by email" in response.text
    assert "No email app? Copy an email template" in response.text
    assert f"To: {FEEDBACK_EMAIL}" in response.text
    assert 'id="cli-feedback-template" readonly' in response.text
    mailto = unescape(response.text.split('href="', 1)[1].split('"', 1)[0])
    parsed = urlsplit(mailto)
    assert parsed.scheme == "mailto"
    assert parsed.path == FEEDBACK_EMAIL
    fields = parse_qs(parsed.query)
    assert "CLI feedback" in fields["subject"][0]
    assert "Steps to reproduce:" in fields["body"][0]
    assert "Expected behaviour:" in fields["body"][0]
    assert "Actual behaviour:" in fields["body"][0]
    assert session.state not in response.text
    assert "access_denied" not in mailto
    assert response.text.index("<aside") < response.text.index("<h1>")
    assert "private-code" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_listener_reports_bind_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("conformance.cli_callback.CONTAINER_TLS_DIRECTORIES", (Path("/nonexistent"),))
    store = AuthSessionStore()
    with psu_callback_listener(_REDIRECT_URI, session_store=store, listen="127.0.0.1:0") as (_host, port):
        with pytest.raises(CallbackListenerError, match="--callback-listen"):
            with psu_callback_listener(_REDIRECT_URI, session_store=store, listen=f"127.0.0.1:{port}"):
                pass


def test_listener_starts_and_receives_callback_without_reverse_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_lookup(_host: str = "") -> str:
        pytest.fail("Callback listener startup must not perform a reverse DNS lookup")

    monkeypatch.setattr("socket.getfqdn", unexpected_lookup)
    monkeypatch.setattr("conformance.cli_callback.CONTAINER_TLS_DIRECTORIES", (Path("/nonexistent"),))
    store = AuthSessionStore()
    session = store.register("run-no-dns")

    with psu_callback_listener(_REDIRECT_URI, session_store=store, listen="127.0.0.1:0") as (host, port):
        assert host == "127.0.0.1"
        assert port > 0
        with _client() as client:
            response = client.get(f"https://{host}:{port}/callback/", params={"state": session.state, "code": "abc"})

    assert response.status_code == 200
    resolved = store.get("run-no-dns", session.state)
    assert resolved is not None
    assert resolved.code == "abc"


@pytest.mark.parametrize("start_thread", [False, True])
def test_listener_releases_socket_and_thread_if_startup_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, start_thread: bool
) -> None:
    from conformance import cli_callback

    original_bind = cli_callback._CallbackServer.server_bind
    original_start = threading.Thread.start
    ports: list[int] = []
    threads: list[threading.Thread] = []

    def record_bind(server: cli_callback._CallbackServer) -> None:
        original_bind(server)
        ports.append(server.server_port)

    def cancel_start(thread: threading.Thread) -> None:
        threads.append(thread)
        if start_thread:
            original_start(thread)
        raise CliCancelled(signal.SIGTERM)

    monkeypatch.setattr(cli_callback._CallbackServer, "server_bind", record_bind)
    monkeypatch.setattr(threading.Thread, "start", cancel_start)
    monkeypatch.setattr(cli_callback, "CONTAINER_TLS_DIRECTORIES", (Path("/nonexistent"),))
    with pytest.raises(CliCancelled):
        with psu_callback_listener(_REDIRECT_URI, session_store=AuthSessionStore(), listen="127.0.0.1:0"):
            pytest.fail("Interrupted startup must not yield a listener")
    assert len(ports) == 1
    assert all(not thread.is_alive() for thread in threads)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", ports[0]))


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
