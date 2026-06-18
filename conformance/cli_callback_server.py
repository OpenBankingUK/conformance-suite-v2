"""Local OAuth callback listener for standalone CLI PSU authorisation runs."""

from __future__ import annotations

import logging
import ssl
import subprocess
import threading
from contextlib import AbstractContextManager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import parse_qs, urlsplit

from conformance.api.auth_session_store import (
    AuthSessionAlreadyResolvedError,
    AuthSessionStore,
    UnknownAuthSessionError,
)

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",  # noqa: S104 - accepted only as a participant-configured loopback redirect host.
        "::1",
    }
)
"""Redirect URI hostnames the CLI can service with its local listener."""

_CALLBACK_PATHS = frozenset({"/callback/", "/conformancesuite/callback", "/conformancesuite/callback/"})
"""Callback paths accepted by the standalone CLI listener."""

_DEFAULT_CERT_PATH = Path("local-config/certs/dev-server.crt")
"""Default local self-signed certificate path for the CLI callback server."""

_DEFAULT_KEY_PATH = Path("local-config/certs/dev-server.key")
"""Default local self-signed private-key path for the CLI callback server."""


class CliCallbackServerError(RuntimeError):
    """Raised when the CLI callback listener cannot be started."""


class CliCallbackServer(AbstractContextManager["CliCallbackServer"]):
    """HTTPS callback listener used by standalone CLI manual PSU flows.

    Attributes:
        redirect_uri: OAuth redirect URI registered for the current CLI run.
        auth_session_store: Process-local auth-session store shared with the
            executor that is waiting for callbacks.
        certificate_path: TLS certificate used for the HTTPS listener.
        private_key_path: TLS private key paired with ``certificate_path``.
    """

    redirect_uri: str
    auth_session_store: AuthSessionStore
    certificate_path: Path
    private_key_path: Path

    def __init__(
        self,
        *,
        redirect_uri: str,
        auth_session_store: AuthSessionStore,
        certificate_path: Path = _DEFAULT_CERT_PATH,
        private_key_path: Path = _DEFAULT_KEY_PATH,
    ) -> None:
        """Initialise the callback server wrapper.

        Args:
            redirect_uri: HTTPS redirect URI whose host/port/path should be
                served locally.
            auth_session_store: Auth-session store to update when the browser
                callback arrives.
            certificate_path: Local server certificate path.
            private_key_path: Local server private-key path.
        """
        self.redirect_uri = redirect_uri
        self.auth_session_store = auth_session_store
        self.certificate_path = certificate_path
        self.private_key_path = private_key_path
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> CliCallbackServer:
        """Start the HTTPS listener.

        Returns:
            The running server wrapper.

        Raises:
            CliCallbackServerError: If the redirect URI is not local HTTPS, the
                TLS certificate cannot be prepared, or the port cannot bind.
        """
        parsed = urlsplit(self.redirect_uri)
        if parsed.scheme != "https":
            raise CliCallbackServerError("CLI callback listener requires an HTTPS oauth.redirectUri")
        hostname = parsed.hostname
        if hostname not in _LOOPBACK_HOSTS:
            raise CliCallbackServerError(
                "CLI callback listener only supports loopback oauth.redirectUri hosts "
                "(localhost, 127.0.0.1, 0.0.0.0, or ::1)"
            )
        port = parsed.port or 443
        self._ensure_certificate_pair()

        handler = _build_callback_handler(auth_session_store=self.auth_session_store)
        bind_host = "::" if hostname == "::1" else hostname
        try:
            server = ThreadingHTTPServer((bind_host, port), handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(self.certificate_path), str(self.private_key_path))
            server.socket = context.wrap_socket(server.socket, server_side=True)
        except OSError as error:
            message = f"Unable to start CLI callback listener on {hostname}:{port}: {error}"
            raise CliCallbackServerError(message) from error
        except ssl.SSLError as error:
            raise CliCallbackServerError(f"Unable to load CLI callback TLS certificate: {error}") from error

        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="cli-callback-server", daemon=True)
        self._thread.start()
        logger.info("CLI callback listener started at %s", self.redirect_uri)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the HTTPS listener.

        Args:
            exc_type: Exception type from the managed block, if any.
            exc_value: Exception instance from the managed block, if any.
            traceback: Traceback from the managed block, if any.
        """
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _ensure_certificate_pair(self) -> None:
        """Create the local self-signed listener certificate when absent.

        Raises:
            CliCallbackServerError: If OpenSSL cannot generate the certificate.
        """
        if self.certificate_path.is_file() and self.private_key_path.is_file():
            return
        self.certificate_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "365",
            "-keyout",
            str(self.private_key_path),
            "-out",
            str(self.certificate_path),
            "-subj",
            "/CN=0.0.0.0",
            "-addext",
            "subjectAltName=IP:0.0.0.0,IP:127.0.0.1,DNS:localhost",
        ]
        try:
            subprocess.run(  # noqa: S603 - fixed executable/args; paths are local repo callback cert files.
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise CliCallbackServerError("Unable to generate CLI callback TLS certificate with openssl") from error


def needs_cli_callback_listener(*, redirect_uri: str | None, has_manual_psu_step: bool) -> bool:
    """Return whether a standalone CLI run should start the callback listener.

    Args:
        redirect_uri: OAuth redirect URI from participant config, if present.
        has_manual_psu_step: Whether the selected plan includes a manual PSU
            authorization step that will wait for a browser redirect.

    Returns:
        ``True`` when a manual PSU step is selected and the redirect host is a
        loopback address the CLI can bind.
    """
    if redirect_uri is None or not has_manual_psu_step:
        return False
    parsed = urlsplit(redirect_uri)
    return parsed.scheme == "https" and parsed.hostname in _LOOPBACK_HOSTS


def _build_callback_handler(*, auth_session_store: AuthSessionStore) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one auth-session store.

    Args:
        auth_session_store: Store to update when callbacks arrive.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass suitable for ``HTTPServer``.
    """

    class _CallbackHandler(BaseHTTPRequestHandler):
        """HTTP handler for CLI OAuth callbacks."""

        def do_GET(self) -> None:
            """Handle a browser OAuth callback request."""
            parsed = urlsplit(self.path)
            if parsed.path not in _CALLBACK_PATHS:
                self._send_html(HTTPStatus.NOT_FOUND, _html_page("Callback not found."))
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            if not query:
                self._send_html(HTTPStatus.OK, _fragment_bridge_html())
                return
            status, body = _capture_callback_query(auth_session_store=auth_session_store, query=query)
            self._send_html(status, body)

        def log_message(self, format_string: str, *args: object) -> None:
            """Route HTTP server access logs through standard logging.

            Args:
                format_string: ``BaseHTTPRequestHandler`` log format string.
                *args: Values interpolated by the standard library handler.
            """
            logger.debug("CLI callback: " + format_string, *args)

        def _send_html(self, status: HTTPStatus, body: str) -> None:
            """Send one HTML response.

            Args:
                status: HTTP response status.
                body: HTML response body.
            """
            encoded_body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded_body)))
            self.end_headers()
            self.wfile.write(encoded_body)

    return _CallbackHandler


def _capture_callback_query(
    *,
    auth_session_store: AuthSessionStore,
    query: dict[str, list[str]],
) -> tuple[HTTPStatus, str]:
    """Capture one OAuth callback query string in the auth-session store.

    Args:
        auth_session_store: Store that owns the awaiting session.
        query: Parsed query parameter mapping.

    Returns:
        HTTP status and HTML response body for the browser.
    """
    state = _first_query_value(query, "state")
    code = _first_query_value(query, "code")
    error = _first_query_value(query, "error")
    error_description = _first_query_value(query, "error_description")
    if not state or (not code and not error):
        return HTTPStatus.BAD_REQUEST, _html_page("Invalid or expired callback.")
    try:
        if error:
            auth_session_store.capture_error(state, error=error, description=error_description)
            return HTTPStatus.OK, _html_page("Authorization failed. Return to the CLI for details.")
        auth_session_store.capture_code(state, code or "")
    except UnknownAuthSessionError, AuthSessionAlreadyResolvedError:
        return HTTPStatus.BAD_REQUEST, _html_page("Invalid or expired callback.")
    return HTTPStatus.OK, _html_page("Authorization code received. You can close this tab and return to the CLI.")


def _first_query_value(query: dict[str, list[str]], name: str) -> str | None:
    """Return the first query value for a parameter.

    Args:
        query: Parsed query parameter mapping.
        name: Parameter name to read.

    Returns:
        First value, or ``None`` when absent.
    """
    values = query.get(name)
    if not values:
        return None
    return values[0]


def _fragment_bridge_html() -> str:
    """Return a browser-side bridge for fragment-form OAuth callbacks.

    Returns:
        Static HTML that replays ``state`` and ``code`` or ``error`` from the
        URL fragment as a query string while deliberately dropping ``id_token``.
    """
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Completing authorization</title>
  <script>
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
  </script>
</head>
<body><h1>Completing authorization</h1><p>The CLI is completing the callback.</p></body>
</html>"""


def _html_page(message: str) -> str:
    """Return a minimal escaped-free static HTML page.

    Args:
        message: Static page message. Callers must not pass request-supplied
            text because this helper intentionally does not perform escaping.

    Returns:
        Minimal HTML document.
    """
    return f'<!doctype html><html lang="en"><body><h1>{message}</h1></body></html>'
