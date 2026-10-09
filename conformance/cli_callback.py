"""Built-in HTTPS listener for manual PSU authorisation callbacks in CLI runs.

The web UI receives ASPSP browser redirects on the Django ``/callback/`` view.
A CLI run has no Django server, so manual-mode PSU authorisation (OAuth 2.0
authorisation-code / FAPI hybrid flow) needs a short-lived HTTPS listener on
the configured ``redirectUri`` so the participant's browser can hand the
``code`` (or OAuth ``error``) back to the waiting executor.

The listener reuses the security model of
:mod:`conformance.api.callback_views`: callbacks are correlated by the
unguessable, one-shot ``state`` registered in
:class:`conformance.api.auth_session_store.AuthSessionStore`; every rejection
returns the same generic page; ``id_token`` fragments are never replayed to
the server; only ``GET`` is served. It binds to loopback by default, and to
all interfaces only when the redirect URI targets ``0.0.0.0`` or the CLI runs
inside the container image (where Docker port publishing controls exposure).
"""

from __future__ import annotations

import html
import ipaddress
import logging
import os
import socket
import ssl
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.template import Context, Engine

from conformance import FEEDBACK_EMAIL
from conformance.api.auth_session_store import (
    AuthSessionAlreadyResolvedError,
    AuthSessionStore,
    UnknownAuthSessionError,
)
from conformance.version import resolve_conformance_tool_version

logger = logging.getLogger(__name__)

CONTAINER_TLS_DIRECTORIES = (Path("/data/tls"), Path("/tmp/conformance-suite-tls"))  # noqa: S108 - container image TLS paths written by docker/entrypoint.py.
"""Locations where the container entrypoint persists its local TLS material."""

_TLS_CERTIFICATE_FILENAME = "localhost-certificate.pem"
_TLS_PRIVATE_KEY_FILENAME = "localhost-private-key.pem"  # noqa: S105 - a filename constant, not a credential value.  # pragma: allowlist secret
_LOOPBACK_HOST = "127.0.0.1"
_ALL_INTERFACES_HOST = "0.0.0.0"  # noqa: S104 - only used for the container / explicit 0.0.0.0 redirect case.
_MAX_QUERY_BYTES = 16384
"""Upper bound on callback request-target size accepted by the listener."""

_FRAGMENT_BRIDGE_SCRIPT = """
(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const state = fragment.get("state");
    const code = fragment.get("code");
    const error = fragment.get("error");
    if (!state || (!code && !error)) {
        return;
    }
    const query = new URLSearchParams();
    query.set("state", state);
    if (error) {
        query.set("error", error);
        const errorDescription = fragment.get("error_description");
        if (errorDescription) {
            query.set("error_description", errorDescription);
        }
    } else {
        query.set("code", code);
    }
    window.location.replace(`${window.location.pathname}?${query.toString()}`);
})();
"""
"""Static fragment replay script; mirrors ``conformance/callback.html`` and drops ``id_token``."""


class CallbackListenerError(RuntimeError):
    """Raised when the CLI callback listener cannot be configured or bound."""


def callback_bind_address(redirect_uri: str, *, listen: str | None = None) -> tuple[str, int, str]:
    """Derive the listener bind host, port, and path from the redirect URI.

    Args:
        redirect_uri: Configured OAuth ``redirectUri`` (HTTPS).
        listen: Optional ``HOST:PORT`` override from ``--callback-listen``.

    Returns:
        Tuple of ``(bind_host, port, path)``.

    Raises:
        CallbackListenerError: If the redirect URI or override is unusable.
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme != "https" or not parts.hostname:
        raise CallbackListenerError("redirectUri must be an absolute https:// URL for the CLI callback listener")
    path = parts.path or "/"
    if listen is not None:
        host, separator, port_text = listen.rpartition(":")
        if not separator or not host or not port_text.isdigit() or not 0 <= int(port_text) < 65536:
            raise CallbackListenerError("--callback-listen must be HOST:PORT, for example 127.0.0.1:8443")
        return host.strip("[]"), int(port_text), path
    port = parts.port or 443
    if parts.hostname == _ALL_INTERFACES_HOST or _running_in_container():
        return _ALL_INTERFACES_HOST, port, path
    return _LOOPBACK_HOST, port, path


def _running_in_container() -> bool:
    """Return whether the CLI appears to run inside the published container image."""
    return Path("/.dockerenv").exists() or bool(os.environ.get("CONFORMANCE_TOOL_VERSION"))


def _paths_match(request_path: str, callback_path: str) -> bool:
    """Compare callback paths, tolerating a trailing slash difference."""
    return request_path.rstrip("/") == callback_path.rstrip("/")


@cache
def _beta_banner() -> str:
    """Render the shared UI beta banner with a local email feedback option."""
    templates = Path(__file__).parent / "api" / "templates"
    engine = Engine(dirs=[str(templates)])
    template = engine.get_template("conformance/partials/beta_notice.html")
    version = resolve_conformance_tool_version()
    subject = f"Conformance beta {version}: CLI feedback"
    body = (
        f"Tool version: {version}\nFlow: CLI\n\n"
        "Summary:\n\nSteps to reproduce:\n\nExpected behaviour:\n\nActual behaviour:\n\n"
        "Please remove credentials, tokens, certificates and personal/customer data before sending."
    )
    mailto = f"mailto:{FEEDBACK_EMAIL}?" + urlencode({"subject": subject, "body": body}, quote_via=quote)
    return template.render(
        Context(
            {
                "feedback_page": True,
                "cli_feedback_email": FEEDBACK_EMAIL,
                "cli_feedback_mailto": mailto,
                "cli_feedback_template": f"To: {FEEDBACK_EMAIL}\nSubject: {subject}\n\n{body}",
            },
            use_l10n=False,
            use_tz=False,
        )
    )


def _render_page(title: str, body: str, *, script: str | None = None) -> bytes:
    """Render a minimal static HTML page.

    Args:
        title: Page heading; escaped.
        body: Paragraph text; escaped.
        script: Optional trusted static inline script.

    Returns:
        UTF-8 encoded HTML document.
    """
    script_block = f"<script>{script}</script>" if script is not None else ""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><title>PSU authorization callback</title>"
        f"{script_block}</head><body style='margin: 0; font-family: system-ui, sans-serif;'>"
        f"{_beta_banner()}"
        f"<main style='padding: 16px;'><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p></main></body></html>"
    ).encode()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Serve the PSU callback path; every other request gets a generic 404."""

    server: _CallbackServer
    server_version = "ConformanceSuiteCallback"
    sys_version = ""

    def do_GET(self) -> None:
        """Handle a browser redirect from the ASPSP."""
        if len(self.path) > _MAX_QUERY_BYTES:
            self._respond(414, _render_page("Callback rejected", "Request target too long."))
            return
        target = urlsplit(self.path)
        if not _paths_match(target.path, self.server.callback_path):
            self._respond(404, _render_page("Not found", "This listener only serves the PSU callback."))
            return
        query = parse_qs(target.query, keep_blank_values=True)
        if not query:
            self._respond(
                200,
                _render_page(
                    "Completing authorization",
                    "The conformance suite is completing the callback.",
                    script=_FRAGMENT_BRIDGE_SCRIPT,
                ),
            )
            return
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]
        error_description = query.get("error_description", [None])[0]
        if not state or not (code or error):
            self._reject()
            return
        try:
            if error:
                self.server.session_store.capture_error(state, error=error, description=error_description)
            else:
                self.server.session_store.capture_code(state, code)
        except UnknownAuthSessionError, AuthSessionAlreadyResolvedError:
            self._reject()
            return
        if error:
            self._respond(
                200,
                _render_page(
                    "Authorization failed",
                    "The ASPSP reported an error. The conformance suite has recorded it; return to the CLI.",
                ),
            )
            return
        self._respond(
            200,
            _render_page(
                "Authorization code received",
                "You can close this tab and return to the CLI to continue the test run.",
            ),
        )

    def _reject(self) -> None:
        """Send the uniform failure page that never discloses which states exist."""
        self._respond(400, _render_page("Callback rejected", "Invalid or expired callback."))

    def _respond(self, status: int, body: bytes) -> None:
        """Write a no-store HTML response.

        Args:
            status: HTTP status code.
            body: Encoded HTML body.
        """
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature defined by BaseHTTPRequestHandler.
        """Suppress default access logging, which would echo authorisation codes to stderr.

        Args:
            format: Ignored printf-style format.
            *args: Ignored format arguments.
        """
        return


class _CallbackServer(ThreadingHTTPServer):
    """Threaded HTTPS server carrying the callback path and session store."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, callback_path: str, session_store: AuthSessionStore) -> None:
        """Bind the server.

        Args:
            address: ``(host, port)`` bind address.
            callback_path: Path component of the redirect URI.
            session_store: Store holding registered PSU sessions.
        """
        self.callback_path = callback_path
        self.session_store = session_store
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, _CallbackHandler)

    def server_bind(self) -> None:
        """Bind without HTTPServer's unused reverse DNS lookup.

        OAuth callback handling uses the configured path and one-shot state,
        not a canonical server hostname. A resolver timeout must not delay
        listener startup, including when bound to loopback or all interfaces.
        """
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def _container_tls_material() -> tuple[Path, Path] | None:
    """Return container TLS certificate/key paths when present and readable."""
    for directory in CONTAINER_TLS_DIRECTORIES:
        certificate = directory / _TLS_CERTIFICATE_FILENAME
        private_key = directory / _TLS_PRIVATE_KEY_FILENAME
        if os.access(certificate, os.R_OK) and os.access(private_key, os.R_OK):
            return certificate, private_key
    return None


def _write_ephemeral_tls_material(directory: Path, hostname: str) -> tuple[Path, Path]:
    """Generate a short-lived self-signed certificate for the callback host.

    Args:
        directory: Private temporary directory to write the PEM files into.
        hostname: Redirect URI host to include as a subject alternative name.

    Returns:
        Tuple of ``(certificate_path, private_key_path)``.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    names: list[x509.GeneralName] = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address(_LOOPBACK_HOST))]
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(hostname)))
    except ValueError:
        if hostname != "localhost":
            names.append(x509.DNSName(hostname))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Conformance Suite CLI callback")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = directory / _TLS_CERTIFICATE_FILENAME
    private_key_path = directory / _TLS_PRIVATE_KEY_FILENAME
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key_path.chmod(0o600)
    return certificate_path, private_key_path


@contextmanager
def psu_callback_listener(
    redirect_uri: str,
    *,
    session_store: AuthSessionStore,
    listen: str | None = None,
) -> Iterator[tuple[str, int]]:
    """Run the HTTPS PSU callback listener for the duration of a CLI run.

    Args:
        redirect_uri: Configured OAuth ``redirectUri`` the ASPSP redirects to.
        session_store: Store in which the executor registers PSU sessions.
        listen: Optional ``HOST:PORT`` bind override.

    Yields:
        The actual ``(host, port)`` the listener is bound to.

    Raises:
        CallbackListenerError: If the address is invalid or cannot be bound.
    """
    bind_host, port, callback_path = callback_bind_address(redirect_uri, listen=listen)
    hostname = urlsplit(redirect_uri).hostname or "localhost"
    with tempfile.TemporaryDirectory(prefix="conformance-callback-tls-") as temporary_directory:
        material = _container_tls_material() or _write_ephemeral_tls_material(Path(temporary_directory), hostname)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.load_cert_chain(certfile=str(material[0]), keyfile=str(material[1]))
            server = _CallbackServer((bind_host, port), callback_path=callback_path, session_store=session_store)
        except (OSError, ssl.SSLError) as error:
            raise CallbackListenerError(
                f"Unable to start PSU callback listener on {bind_host}:{port}: {error}. "
                "Free the port, change redirectUri, or pass --callback-listen HOST:PORT"
            ) from error
        thread: threading.Thread | None = None
        try:
            # Defer the handshake to the per-request worker thread so a stalled
            # client cannot block the accept loop.
            server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
            thread = threading.Thread(target=server.serve_forever, name="psu-callback-listener", daemon=True)
            thread.start()
            bound_host, bound_port = server.server_address[0], server.server_address[1]
            yield str(bound_host), int(bound_port)
        finally:
            if thread is not None and thread.ident is not None:
                server.shutdown()
            server.server_close()
            if thread is not None and thread.ident is not None:
                thread.join(timeout=5)
