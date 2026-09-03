"""Deterministic HTTPS service and protocol fixtures for DCR integration tests."""

from __future__ import annotations

import base64
import ipaddress
import json
import ssl
import threading
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from conformance.json_types import JsonObject, JsonValue

_FIXED_NOW = 1_800_000_000
_FIXTURE_ISSUER = "fixturesoftwareid"
_FIXTURE_AUDIENCE = "aspsp123"
_FIXTURE_KEY_ID = "fixture-signing-key"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_SUPPORTED_AUTH_METHODS = (
    "tls_client_auth",
    "private_key_jwt",
    "client_secret_jwt",
    "client_secret_basic",
)
_ALL_MANAGEMENT_METHODS = frozenset({"GET", "PUT", "DELETE"})


@dataclass(frozen=True)
class DcrTlsMaterials:
    """Paths to ephemeral certificates used by the local mTLS service."""

    ca_certificate_path: Path
    server_certificate_path: Path
    server_private_key_path: Path
    client_certificate_path: Path
    client_private_key_path: Path
    untrusted_client_certificate_path: Path
    untrusted_client_private_key_path: Path


@dataclass(frozen=True)
class DcrProtocolMaterials:
    """Reusable fake SSA, signing key, and JWKS values for DCR requests."""

    signing_private_key_path: Path
    signing_key: jwk.RSAKey
    signing_public_key: jwk.RSAKey
    jwks: JsonObject
    software_statement_assertion: str


@dataclass(frozen=True)
class DcrServiceEvent:
    """Non-sensitive observation of one request accepted by the service."""

    method: str
    path: str
    status_code: int
    mtls_verified: bool
    content_type: str | None
    error: str | None


@dataclass(frozen=True)
class DcrTokenRequest:
    """Form fields and headers for one supported client authentication method."""

    form: Mapping[str, str]
    headers: Mapping[str, str]


@dataclass
class _RegisteredClient:
    """Mutable server-side state for a dynamically registered client."""

    client_id: str
    client_secret: str
    registration_access_token: str
    grant_access_token: str | None
    metadata: JsonObject
    deleted: bool = False


@dataclass(frozen=True)
class _ServiceResponse:
    """Internal HTTP response emitted by the protocol state machine."""

    status_code: int
    body: JsonObject | None = None
    headers: Mapping[str, str] | None = None


class DcrTestService(AbstractContextManager["DcrTestService"]):
    """Stateful local HTTPS implementation of the DCR parity protocol."""

    def __init__(
        self,
        root: Path,
        *,
        management_methods: frozenset[str] = _ALL_MANAGEMENT_METHODS,
        response_omissions: Mapping[str, frozenset[str]] | None = None,
        response_overrides: Mapping[str, Mapping[str, JsonValue]] | None = None,
    ) -> None:
        """Create an unstarted service with fresh ephemeral credentials.

        Args:
            root: Directory in which ephemeral test credentials are written.
            management_methods: Optional management methods the fake server
                implements. POST registration and token requests are always
                available.
            response_omissions: Response fields to omit, keyed by POST, GET, or
                PUT, for response-validation regressions.
            response_overrides: Response field replacements, keyed by POST, GET,
                or PUT, for response-validation regressions.

        Raises:
            ValueError: If an unsupported management or response-shaping method
                is requested.
        """
        invalid_methods = management_methods - _ALL_MANAGEMENT_METHODS
        if invalid_methods:
            raise ValueError(f"Unsupported DCR management methods: {sorted(invalid_methods)}")
        response_methods = {*response_omissions} if response_omissions is not None else set()
        if response_overrides is not None:
            response_methods.update(response_overrides)
        invalid_response_methods = response_methods - {"POST", "GET", "PUT"}
        if invalid_response_methods:
            raise ValueError(f"Unsupported DCR response-shaping methods: {sorted(invalid_response_methods)}")
        root.mkdir(parents=True, exist_ok=True)
        self.management_methods = management_methods
        self.response_omissions = {method: frozenset(fields) for method, fields in (response_omissions or {}).items()}
        self.response_overrides = {method: dict(fields) for method, fields in (response_overrides or {}).items()}
        self.tls = _create_tls_materials(root / "tls")
        self.protocol = _create_protocol_materials(root / "protocol")
        self._clients: dict[str, _RegisteredClient] = {}
        self._events: list[DcrServiceEvent] = []
        self._client_counter = 0
        self._next_registration_failure_status: int | None = None
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._base_url: str | None = None

    @property
    def base_url(self) -> str:
        """Return the running service's HTTPS origin.

        Returns:
            HTTPS origin with the dynamically allocated local port.

        Raises:
            RuntimeError: If the service has not been started.
        """
        if self._base_url is None:
            raise RuntimeError("DCR test service has not been started")
        return self._base_url

    @property
    def discovery_url(self) -> str:
        """Return the OpenID discovery endpoint URL.

        Returns:
            Absolute local HTTPS OpenID discovery URL.
        """
        return f"{self.base_url}/.well-known/openid-configuration"

    @property
    def registration_endpoint(self) -> str:
        """Return the dynamic registration endpoint URL.

        Returns:
            Absolute local HTTPS registration URL.
        """
        return f"{self.base_url}/register"

    @property
    def token_endpoint(self) -> str:
        """Return the client-credentials token endpoint URL.

        Returns:
            Absolute local HTTPS token URL.
        """
        return f"{self.base_url}/token"

    @property
    def events(self) -> tuple[DcrServiceEvent, ...]:
        """Return an immutable snapshot of non-sensitive request observations.

        Returns:
            Events in service receipt order.
        """
        with self._lock:
            return tuple(self._events)

    def client(self, *, trusted_client_certificate: bool = True) -> httpx.Client:
        """Build an HTTP client configured for the service's private test CA.

        Args:
            trusted_client_certificate: Whether to present the trusted fixture
                client certificate. When false, no certificate is presented.

        Returns:
            Caller-owned synchronous HTTP client.
        """
        context = ssl.create_default_context(cafile=str(self.tls.ca_certificate_path))
        if trusted_client_certificate:
            context.load_cert_chain(
                certfile=str(self.tls.client_certificate_path),
                keyfile=str(self.tls.client_private_key_path),
            )
        return httpx.Client(base_url=self.base_url, verify=context, trust_env=False, timeout=3.0)

    def untrusted_client(self) -> httpx.Client:
        """Build a client presenting a certificate from an untrusted test CA.

        Returns:
            Caller-owned synchronous HTTP client that cannot complete mTLS.
        """
        context = ssl.create_default_context(cafile=str(self.tls.ca_certificate_path))
        context.load_cert_chain(
            certfile=str(self.tls.untrusted_client_certificate_path),
            keyfile=str(self.tls.untrusted_client_private_key_path),
        )
        return httpx.Client(base_url=self.base_url, verify=context, trust_env=False, timeout=3.0)

    def reset(self) -> None:
        """Clear clients, counters, and events for explicit scenario isolation."""
        with self._lock:
            self._clients.clear()
            self._events.clear()
            self._client_counter = 0
            self._next_registration_failure_status = None

    def fail_next_registration(self, status_code: int = 500) -> None:
        """Force the next registration request to fail deterministically.

        Args:
            status_code: HTTP error status returned for the next POST registration.

        Raises:
            ValueError: If ``status_code`` is outside the HTTP error range.
        """
        if not 400 <= status_code <= 599:
            raise ValueError("Forced registration status must be between 400 and 599")
        with self._lock:
            self._next_registration_failure_status = status_code

    def snapshot(self) -> JsonObject:
        """Return deterministic, non-sensitive state for test assertions.

        Returns:
            Client lifecycle state without credentials or bearer values.
        """
        with self._lock:
            return {
                "clients": [
                    {
                        "client_id": client.client_id,
                        "deleted": client.deleted,
                        "token_issued": client.grant_access_token is not None,
                        "token_endpoint_auth_method": client.metadata["token_endpoint_auth_method"],
                    }
                    for client in self._clients.values()
                ]
            }

    def registration_claims(
        self,
        *,
        token_endpoint_auth_method: str = "tls_client_auth",  # noqa: S107 - protocol enum, not a credential.
        overrides: Mapping[str, JsonValue] | None = None,
    ) -> JsonObject:
        """Build valid fixed-time Open Banking DCR registration claims.

        Args:
            token_endpoint_auth_method: Requested token endpoint authentication
                method.
            overrides: Claim values to replace for negative or update cases.

        Returns:
            JSON claims ready to sign as compact PS256 JOSE.
        """
        claims: JsonObject = {
            "iss": _FIXTURE_ISSUER,
            "aud": _FIXTURE_AUDIENCE,
            "iat": _FIXED_NOW,
            "exp": _FIXED_NOW + 300,
            "jti": "fixture-registration-jti",
            "software_statement": self.protocol.software_statement_assertion,
            "application_type": "web",
            "redirect_uris": ["https://client.example.test/callback"],
            "grant_types": ["client_credentials", "authorization_code"],
            "response_types": ["code id_token"],
            "scope": "accounts openid",
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "id_token_signed_response_alg": "PS256",
            "request_object_signing_alg": "PS256",
        }
        if token_endpoint_auth_method == "tls_client_auth":  # noqa: S105 - protocol enum, not a credential.
            claims["tls_client_auth_subject_dn"] = "CN=fixture-client,OU=Test,O=Open Banking Fixture,C=GB"
        elif token_endpoint_auth_method in {"private_key_jwt", "client_secret_jwt"}:
            claims["token_endpoint_auth_signing_alg"] = "PS256"  # noqa: S105 - algorithm identifier, not a credential.
        if overrides is not None:
            claims.update(overrides)
        return claims

    def sign_registration(
        self,
        *,
        token_endpoint_auth_method: str = "tls_client_auth",  # noqa: S107 - protocol enum, not a credential.
        overrides: Mapping[str, JsonValue] | None = None,
        algorithm: str = "PS256",
    ) -> str:
        """Sign registration claims as a raw compact JOSE body.

        Args:
            token_endpoint_auth_method: Requested token endpoint authentication
                method.
            overrides: Claim values to replace before signing.
            algorithm: JWS algorithm placed in the protected header.

        Returns:
            Compact signed registration JWT.
        """
        return jwt.encode(
            {"alg": algorithm, "kid": _FIXTURE_KEY_ID},
            self.registration_claims(
                token_endpoint_auth_method=token_endpoint_auth_method,
                overrides=overrides,
            ),
            self.protocol.signing_key,
            algorithms=[algorithm],
        )

    def token_request(self, registration: Mapping[str, JsonValue]) -> DcrTokenRequest:
        """Build token request inputs for the registered auth method.

        Args:
            registration: Successful registration response returned by the
                service.

        Returns:
            Form fields and HTTP headers implementing the selected authentication
            method.

        Raises:
            ValueError: If response fields are absent or have unexpected types.
        """
        client_id = _required_response_string(registration, "client_id")
        client_secret = _required_response_string(registration, "client_secret")
        method = _required_response_string(registration, "token_endpoint_auth_method")
        form = {"grant_type": "client_credentials"}
        headers: dict[str, str] = {}
        if method == "tls_client_auth":
            form["client_id"] = client_id
        elif method == "client_secret_basic":
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif method == "private_key_jwt":
            form.update(self._assertion_form(client_id, self.protocol.signing_key, algorithm="PS256"))
        elif method == "client_secret_jwt":
            secret_key = jwk.import_key(client_secret, "oct")
            form.update(self._assertion_form(client_id, secret_key, algorithm="HS256"))
        else:
            raise ValueError(f"Unsupported fixture auth method: {method}")
        return DcrTokenRequest(form=form, headers=headers)

    def _assertion_form(self, client_id: str, key: jwk.Key, *, algorithm: str) -> dict[str, str]:
        """Build private-key or client-secret JWT token form fields.

        Args:
            client_id: Dynamic client identifier used as issuer and subject.
            key: JOSE signing key appropriate to the authentication method.
            algorithm: Protected-header signing algorithm.

        Returns:
            Token form fields containing the client assertion.
        """
        assertion = jwt.encode(
            {"alg": algorithm, "kid": _FIXTURE_KEY_ID},
            {
                "iss": client_id,
                "sub": client_id,
                "aud": self.token_endpoint,
                "iat": _FIXED_NOW,
                "exp": _FIXED_NOW + 300,
                "jti": f"fixture-assertion-{client_id}",
            },
            key,
            algorithms=[algorithm],
        )
        return {
            "client_id": client_id,
            "client_assertion_type": _ASSERTION_TYPE,
            "client_assertion": assertion,
        }

    def start(self) -> Self:
        """Start the mTLS server on a dynamically allocated local port.

        Returns:
            This service, suitable for context-manager use.

        Raises:
            RuntimeError: If the same service is started more than once.
        """
        if self._server is not None:
            raise RuntimeError("DCR test service is already running")
        owner = self

        class RequestHandler(BaseHTTPRequestHandler):
            """Bridge standard-library HTTPS requests to the DCR state machine."""

            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                """Handle a GET request."""
                owner._serve_http(self)

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                """Handle a POST request."""
                owner._serve_http(self)

            def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                """Handle a PUT request."""
                owner._serve_http(self)

            def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                """Handle a DELETE request."""
                owner._serve_http(self)

            def log_message(self, format_string: str, *args: object) -> None:
                """Suppress non-deterministic standard-library access logs.

                Args:
                    format_string: Standard-library log format string.
                    *args: Values for the log format string.
                """

        server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=str(self.tls.server_certificate_path),
            keyfile=str(self.tls.server_private_key_path),
        )
        context.load_verify_locations(cafile=str(self.tls.ca_certificate_path))
        context.verify_mode = ssl.CERT_REQUIRED
        server.socket = context.wrap_socket(server.socket, server_side=True)
        self._server = server
        port = server.server_address[1]
        self._base_url = f"https://localhost:{port}"
        self._thread = threading.Thread(target=server.serve_forever, name="dcr-test-service", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        """Stop the local server and release its listening socket."""
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=3.0)
        self._server = None
        self._thread = None
        self._base_url = None

    def __enter__(self) -> Self:
        """Start and return the service for context-manager use.

        Returns:
            Running service instance.
        """
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the service when its context exits.

        Args:
            exc_type: Exception type raised in the context, if any.
            exc_value: Exception instance raised in the context, if any.
            traceback: Exception traceback raised in the context, if any.
        """
        self.close()

    def _serve_http(self, handler: BaseHTTPRequestHandler) -> None:
        """Read, route, and serialize one accepted HTTPS request.

        Args:
            handler: Active standard-library HTTP request handler.
        """
        content_length = int(handler.headers.get("Content-Length", "0"))
        body = handler.rfile.read(content_length) if content_length else b""
        response = self._dispatch(
            handler.command,
            urlsplit(handler.path).path,
            {name.lower(): value for name, value in handler.headers.items()},
            body,
        )
        payload = (
            json.dumps(response.body, sort_keys=True, separators=(",", ":")).encode()
            if response.body is not None
            else b""
        )
        handler.send_response(response.status_code)
        if response.body is not None:
            handler.send_header("Content-Type", "application/json")
        for name, value in (response.headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        if payload:
            handler.wfile.write(payload)

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> _ServiceResponse:
        """Route one verified-mTLS request through the state machine.

        Args:
            method: HTTP request method.
            path: URL path without query string.
            headers: Lowercase request headers.
            body: Exact request entity bytes.

        Returns:
            Deterministic service response.
        """
        with self._lock:
            if method == "GET" and path == "/.well-known/openid-configuration":
                response = self._discovery_response()
            elif method == "GET" and path == "/jwks":
                response = _ServiceResponse(200, self.protocol.jwks)
            elif method == "POST" and path == "/register":
                if self._next_registration_failure_status is None:
                    response = self._register(headers, body)
                else:
                    response = _error(
                        self._next_registration_failure_status,
                        "injected_failure",
                        "Deterministic registration prerequisite failure",
                    )
                    self._next_registration_failure_status = None
            elif method == "POST" and path == "/token":
                response = self._issue_token(headers, body)
            elif path.startswith("/register/"):
                response = self._manage(method, path.removeprefix("/register/"), headers, body)
            else:
                response = _error(404, "not_found", "No fixture endpoint exists for this request")
            error = None
            if response.body is not None and isinstance(response.body.get("error"), str):
                error = cast(str, response.body["error"])
            self._events.append(
                DcrServiceEvent(
                    method=method,
                    path=path,
                    status_code=response.status_code,
                    mtls_verified=True,
                    content_type=headers.get("content-type"),
                    error=error,
                )
            )
            return response

    def _discovery_response(self) -> _ServiceResponse:
        """Build deterministic OpenID provider metadata.

        Returns:
            Successful discovery response.
        """
        return _ServiceResponse(
            200,
            {
                "issuer": self.base_url,
                "jwks_uri": f"{self.base_url}/jwks",
                "registration_endpoint": self.registration_endpoint,
                "token_endpoint": self.token_endpoint,
                "token_endpoint_auth_methods_supported": list(_SUPPORTED_AUTH_METHODS),
                "token_endpoint_auth_signing_alg_values_supported": ["PS256", "HS256"],
                "registration_management_methods_supported": [
                    cast(JsonValue, method) for method in sorted(self.management_methods)
                ],
            },
        )

    def _register(self, headers: Mapping[str, str], body: bytes) -> _ServiceResponse:
        """Validate compact registration JOSE and create one client.

        Args:
            headers: Lowercase HTTP request headers.
            body: Raw compact registration JOSE bytes.

        Returns:
            Registration success or a deterministic protocol error.
        """
        claims_or_error = self._validated_registration(headers, body)
        if isinstance(claims_or_error, _ServiceResponse):
            return claims_or_error
        claims = claims_or_error
        self._client_counter += 1
        client_id = f"fixture-client-{self._client_counter:04d}"
        client_secret = f"fixture-client-material-{self._client_counter:04d}"
        registration_token = f"fixture-registration-token-{self._client_counter:04d}"
        metadata = {key: value for key, value in claims.items() if key not in {"iss", "aud", "iat", "exp", "jti"}}
        metadata["software_id"] = _FIXTURE_ISSUER
        response_body: JsonObject = {
            **metadata,
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": _FIXED_NOW,
            "client_secret_expires_at": 0,
            "registration_access_token": registration_token,
            "registration_client_uri": f"{self.registration_endpoint}/{client_id}",
        }
        self._clients[client_id] = _RegisteredClient(
            client_id=client_id,
            client_secret=client_secret,
            registration_access_token=registration_token,
            grant_access_token=None,
            metadata=metadata,
        )
        return _ServiceResponse(201, self._shape_registration_response("POST", response_body))

    def _validated_registration(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> JsonObject | _ServiceResponse:
        """Validate registration transport, signature, and relevant DCR claims.

        Args:
            headers: Lowercase HTTP request headers.
            body: Raw compact registration JOSE bytes.

        Returns:
            Verified JSON claims or a deterministic error response.
        """
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/jose":
            return _error(415, "invalid_request", "Content-Type must be application/jose")
        try:
            token = jwt.decode(body, self.protocol.signing_public_key, algorithms=["PS256"])
            claims = cast(JsonObject, token.claims)
        except JoseError, ValueError, TypeError, UnicodeDecodeError:
            return _error(400, "invalid_software_statement", "Registration JOSE must be valid compact PS256")

        required = {
            "iss",
            "aud",
            "iat",
            "exp",
            "jti",
            "software_statement",
            "application_type",
            "redirect_uris",
            "grant_types",
            "response_types",
            "scope",
            "token_endpoint_auth_method",
            "id_token_signed_response_alg",
            "request_object_signing_alg",
        }
        if required - claims.keys():
            return _error(400, "invalid_client_metadata", "Required registration claims are missing")
        if claims["iss"] != _FIXTURE_ISSUER:
            return _error(400, "invalid_software_statement", "Registration issuer is not accepted")
        if claims["aud"] != _FIXTURE_AUDIENCE:
            return _error(400, "invalid_software_statement", "Registration audience is not accepted")
        if not isinstance(claims["exp"], int) or claims["exp"] <= _FIXED_NOW:
            return _error(400, "invalid_software_statement", "Registration claims are expired")
        if claims["software_statement"] != self.protocol.software_statement_assertion:
            return _error(400, "invalid_software_statement", "Software statement is not accepted")
        method = claims["token_endpoint_auth_method"]
        if not isinstance(method, str) or method not in _SUPPORTED_AUTH_METHODS:
            return _error(400, "invalid_client_metadata", "Token endpoint authentication method is unsupported")
        if claims.get("token_endpoint_auth_signing_alg") == "RS256":
            return _error(400, "invalid_client_metadata", "RS256 token authentication signing is unsupported")
        response_types = claims["response_types"]
        if (
            not isinstance(response_types, list)
            or not response_types
            or any(value not in {"code", "code id_token"} for value in response_types)
        ):
            return _error(400, "invalid_client_metadata", "response_types contains an unsupported value")
        if claims["application_type"] not in {"web", "native"}:
            return _error(400, "invalid_client_metadata", "application_type must be web or native")
        redirect_uris = claims["redirect_uris"]
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(isinstance(uri, str) and urlsplit(uri).scheme == "https" for uri in redirect_uris)
        ):
            return _error(400, "invalid_redirect_uri", "redirect_uris must contain absolute HTTPS URLs")
        subject_dn = claims.get("tls_client_auth_subject_dn")
        if method == "tls_client_auth" and (
            not isinstance(subject_dn, str) or not subject_dn or len(subject_dn) > 512 or "=" not in subject_dn
        ):
            return _error(400, "invalid_client_metadata", "A valid TLS client certificate subject DN is required")
        return claims

    def _issue_token(self, headers: Mapping[str, str], body: bytes) -> _ServiceResponse:
        """Authenticate a dynamic client and issue its deterministic grant token.

        Args:
            headers: Lowercase HTTP request headers.
            body: URL-encoded token request body.

        Returns:
            OAuth token success or exact OAuth error response.
        """
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            return _error(415, "invalid_request", "Token requests must be form encoded")
        form = {name: values[-1] for name, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        if form.get("grant_type") != "client_credentials":
            return _error(400, "unsupported_grant_type", "grant_type must be client_credentials")
        client = self._authenticated_token_client(headers, form)
        if client is None or client.deleted:
            return _error(401, "invalid_client", "Dynamic client authentication failed")
        if client.grant_access_token is None:
            client.grant_access_token = f"fixture-grant-token-{client.client_id}"
        return _ServiceResponse(
            200,
            {
                "access_token": client.grant_access_token,
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": "accounts",
            },
            {"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    def _authenticated_token_client(
        self,
        headers: Mapping[str, str],
        form: Mapping[str, str],
    ) -> _RegisteredClient | None:
        """Resolve and verify the auth method configured at registration.

        Args:
            headers: Lowercase token request headers.
            form: Parsed token form fields.

        Returns:
            Authenticated client or ``None`` for every invalid boundary.
        """
        basic_credentials = _decode_basic_credentials(headers.get("authorization"))
        client_id = basic_credentials[0] if basic_credentials is not None else form.get("client_id")
        if client_id is None:
            return None
        client = self._clients.get(client_id)
        if client is None:
            return None
        method = client.metadata["token_endpoint_auth_method"]
        if method == "tls_client_auth":
            return client if basic_credentials is None and "client_secret" not in form else None
        if method == "client_secret_basic":
            has_basic_credentials = basic_credentials == (client_id, client.client_secret)
            return client if has_basic_credentials and "client_secret" not in form else None
        if method == "private_key_jwt":
            return client if self._valid_assertion(form, client, self.protocol.signing_public_key, "PS256") else None
        if method == "client_secret_jwt":
            secret_key = jwk.import_key(client.client_secret, "oct")
            return client if self._valid_assertion(form, client, secret_key, "HS256") else None
        return None

    def _valid_assertion(
        self,
        form: Mapping[str, str],
        client: _RegisteredClient,
        key: jwk.Key,
        algorithm: str,
    ) -> bool:
        """Validate a token endpoint client assertion.

        Args:
            form: Parsed token form fields.
            client: Dynamic client being authenticated.
            key: Verification key for the configured auth method.
            algorithm: Required assertion algorithm.

        Returns:
            Whether assertion type, signature, and fixed-time claims are valid.
        """
        if form.get("client_assertion_type") != _ASSERTION_TYPE:
            return False
        assertion = form.get("client_assertion")
        if assertion is None:
            return False
        try:
            claims = cast(JsonObject, jwt.decode(assertion, key, algorithms=[algorithm]).claims)
        except JoseError, ValueError, TypeError:
            return False
        return (
            claims.get("iss") == client.client_id
            and claims.get("sub") == client.client_id
            and claims.get("aud") == self.token_endpoint
            and isinstance(claims.get("exp"), int)
            and cast(int, claims["exp"]) > _FIXED_NOW
        )

    def _manage(
        self,
        method: str,
        client_id: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> _ServiceResponse:
        """Apply GET, PUT, or DELETE to one dynamic registration.

        Args:
            method: Requested management method.
            client_id: Client identifier from the request path.
            headers: Lowercase request headers.
            body: Exact request body.

        Returns:
            Management success, unsupported-method response, or 401.
        """
        if method not in self.management_methods:
            return _ServiceResponse(
                405,
                {"error": "method_not_allowed", "error_description": "Optional management method is disabled"},
                {"Allow": ", ".join(sorted(self.management_methods))},
            )
        client = self._clients.get(client_id)
        if client is None or client.deleted or not self._valid_management_bearer(headers, client):
            return _error(401, "invalid_token", "Client is unknown, deleted, or not authorized")
        if method == "GET":
            return _ServiceResponse(200, self._shape_registration_response("GET", self._registration_response(client)))
        if method == "DELETE":
            client.deleted = True
            return _ServiceResponse(204)
        if method == "PUT":
            claims_or_error = self._validated_registration(headers, body)
            if isinstance(claims_or_error, _ServiceResponse):
                return claims_or_error
            client.metadata = {
                key: value for key, value in claims_or_error.items() if key not in {"iss", "aud", "iat", "exp", "jti"}
            }
            client.metadata["software_id"] = _FIXTURE_ISSUER
            return _ServiceResponse(200, self._shape_registration_response("PUT", self._registration_response(client)))
        return _error(405, "method_not_allowed", "Only GET, PUT, and DELETE manage registrations")

    def _valid_management_bearer(
        self,
        headers: Mapping[str, str],
        client: _RegisteredClient,
    ) -> bool:
        """Check the generated client-credentials token on management calls.

        Args:
            headers: Lowercase request headers.
            client: Dynamic client named in the management URL.

        Returns:
            Whether the request carries the client's current grant token.
        """
        expected = None if client.grant_access_token is None else f"Bearer {client.grant_access_token}"
        return expected is not None and headers.get("authorization") == expected

    def _registration_response(self, client: _RegisteredClient) -> JsonObject:
        """Build a DCR 3.4 management response from current client state.

        Args:
            client: Registered client to serialize.

        Returns:
            Complete deterministic client metadata response.
        """
        return {
            **client.metadata,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "client_id_issued_at": _FIXED_NOW,
            "client_secret_expires_at": 0,
            "registration_access_token": client.registration_access_token,
            "registration_client_uri": f"{self.registration_endpoint}/{client.client_id}",
        }

    def _shape_registration_response(self, method: str, response: JsonObject) -> JsonObject:
        """Apply method-specific omissions and replacements to a test response.

        Args:
            method: POST, GET, or PUT response surface.
            response: Complete successful registration response.

        Returns:
            Independently mutable response shaped for a regression test.
        """
        shaped = dict(response)
        for field_name in self.response_omissions.get(method, ()):
            shaped.pop(field_name, None)
        shaped.update(self.response_overrides.get(method, {}))
        return shaped


def _required_response_string(response: Mapping[str, JsonValue], key: str) -> str:
    """Extract a required string from a fixture protocol response.

    Args:
        response: Parsed JSON response mapping.
        key: Required response field.

    Returns:
        Non-empty response value.

    Raises:
        ValueError: If the field is absent or is not a non-empty string.
    """
    value = response.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Registration response field {key!r} must be a non-empty string")
    return value


def _decode_basic_credentials(value: str | None) -> tuple[str, str] | None:
    """Decode HTTP Basic credentials without leaking them into errors.

    Args:
        value: Authorization header value.

    Returns:
        Client identifier and credential, or ``None`` when malformed.
    """
    if value is None or not value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(value.removeprefix("Basic "), validate=True).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
    except ValueError, UnicodeDecodeError:
        return None
    return client_id, client_secret


def _error(status_code: int, error: str, description: str) -> _ServiceResponse:
    """Build a stable OAuth or DCR error response.

    Args:
        status_code: HTTP status to emit.
        error: Machine-readable protocol error.
        description: Non-sensitive deterministic explanation.

    Returns:
        Structured JSON error response.
    """
    return _ServiceResponse(status_code, {"error": error, "error_description": description})


def _write_private_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    """Write an ephemeral fixture RSA private key with restrictive permissions.

    Args:
        path: Destination beneath pytest's per-test fixture directory.
        key: Ephemeral key to serialize.
    """
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def _certificate(
    *,
    subject: x509.Name,
    public_key: rsa.RSAPublicKey,
    issuer: x509.Name,
    issuer_key: rsa.RSAPrivateKey,
    serial_number: int,
    is_ca: bool,
    extended_usage: x509.ObjectIdentifier | None = None,
) -> x509.CertificateBuilder:
    """Build and sign fixed-validity fixture certificate fields.

    Args:
        subject: Certificate subject name.
        public_key: Subject RSA public key.
        issuer: Issuing certificate subject name.
        issuer_key: Issuing RSA private key.
        serial_number: Stable positive fixture serial number.
        is_ca: Whether to add CA basic constraints.
        extended_usage: Optional server or client TLS usage.

    Returns:
        Certificate builder ready for optional SAN fields and signing.
    """
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(serial_number)
        .not_valid_before(datetime(2025, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=not is_ca,
                content_commitment=False,
                key_encipherment=not is_ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    if extended_usage is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([extended_usage]), critical=False)
    return builder


def _create_tls_materials(root: Path) -> DcrTlsMaterials:
    """Generate a private CA, server certificate, and trusted/untrusted clients.

    Args:
        root: Directory receiving ephemeral PEM files.

    Returns:
        Paths to all generated mTLS fixture files.
    """
    root.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DCR fixture CA")])
    ca_certificate = _certificate(
        subject=ca_name,
        public_key=ca_key.public_key(),
        issuer=ca_name,
        issuer_key=ca_key,
        serial_number=1,
        is_ca=True,
    ).sign(ca_key, hashes.SHA256())

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server_certificate = (
        _certificate(
            subject=server_name,
            public_key=server_key.public_key(),
            issuer=ca_name,
            issuer_key=ca_key,
            serial_number=2,
            is_ca=False,
            extended_usage=ExtendedKeyUsageOID.SERVER_AUTH,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DCR fixture client")])
    client_certificate = _certificate(
        subject=client_name,
        public_key=client_key.public_key(),
        issuer=ca_name,
        issuer_key=ca_key,
        serial_number=3,
        is_ca=False,
        extended_usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    ).sign(ca_key, hashes.SHA256())

    untrusted_ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    untrusted_ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Untrusted DCR fixture CA")])
    untrusted_client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    untrusted_client_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Untrusted fixture client")])
    untrusted_client_certificate = _certificate(
        subject=untrusted_client_name,
        public_key=untrusted_client_key.public_key(),
        issuer=untrusted_ca_name,
        issuer_key=untrusted_ca_key,
        serial_number=4,
        is_ca=False,
        extended_usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    ).sign(untrusted_ca_key, hashes.SHA256())

    ca_path = root / "ca.pem"
    server_certificate_path = root / "server.pem"
    server_key_path = root / "server-key.pem"
    client_certificate_path = root / "client.pem"
    client_key_path = root / "client-key.pem"
    untrusted_certificate_path = root / "untrusted-client.pem"
    untrusted_key_path = root / "untrusted-client-key.pem"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    server_certificate_path.write_bytes(server_certificate.public_bytes(serialization.Encoding.PEM))
    _write_private_key(server_key_path, server_key)
    client_certificate_path.write_bytes(client_certificate.public_bytes(serialization.Encoding.PEM))
    _write_private_key(client_key_path, client_key)
    untrusted_certificate_path.write_bytes(untrusted_client_certificate.public_bytes(serialization.Encoding.PEM))
    _write_private_key(untrusted_key_path, untrusted_client_key)
    return DcrTlsMaterials(
        ca_certificate_path=ca_path,
        server_certificate_path=server_certificate_path,
        server_private_key_path=server_key_path,
        client_certificate_path=client_certificate_path,
        client_private_key_path=client_key_path,
        untrusted_client_certificate_path=untrusted_certificate_path,
        untrusted_client_private_key_path=untrusted_key_path,
    )


def _create_protocol_materials(root: Path) -> DcrProtocolMaterials:
    """Generate ephemeral PS256 key, public JWKS, and fake signed SSA.

    Args:
        root: Directory receiving the signing private key fixture.

    Returns:
        Protocol materials shared by request builders and service validation.
    """
    root.mkdir(parents=True, exist_ok=True)
    signing_key = jwk.generate_key("RSA", 2048, private=True, auto_kid=False)
    public_data = signing_key.as_dict(is_private=False)
    public_data["kid"] = _FIXTURE_KEY_ID
    public_data["use"] = "sig"
    public_data["alg"] = "PS256"
    signing_public_key = cast(jwk.RSAKey, jwk.import_key(public_data))
    private_key_path = root / "fixture-signing-key.pem"
    private_key_path.write_bytes(signing_key.as_pem(private=True))
    private_key_path.chmod(0o600)
    software_statement = jwt.encode(
        {"alg": "PS256", "kid": _FIXTURE_KEY_ID},
        {
            "iss": "fixture-trust-anchor",
            "iat": _FIXED_NOW,
            "exp": _FIXED_NOW + 3600,
            "software_id": _FIXTURE_ISSUER,
            "software_redirect_uris": ["https://client.example.test/callback"],
        },
        signing_key,
        algorithms=["PS256"],
    )
    return DcrProtocolMaterials(
        signing_private_key_path=private_key_path,
        signing_key=signing_key,
        signing_public_key=signing_public_key,
        jwks={"keys": [cast(JsonObject, public_data)]},
        software_statement_assertion=software_statement,
    )


def running_dcr_test_service(
    root: Path,
    *,
    management_methods: frozenset[str] = _ALL_MANAGEMENT_METHODS,
) -> Iterator[DcrTestService]:
    """Yield a running deterministic DCR service and always clean it up.

    Args:
        root: Directory for ephemeral fixture material.
        management_methods: Optional implemented management operations.

    Yields:
        Running service instance.
    """
    with DcrTestService(root, management_methods=management_methods) as service:
        yield service
