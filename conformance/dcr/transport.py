"""DCR mTLS transport configuration.

This module defines :class:`DcrTransportConfig`, the value object that
captures all transport-layer options for DCR HTTP connections.

Per the plan and Open Banking DCR decisions:

- ``disable_keep_alives`` is a normal transport option.
- ``tls_skip_verify`` is advanced/unsafe-only.  When ``True`` an explicit
  warning is emitted because skipping TLS verification is incompatible with
  a secure conformance run and must never be used against real ASPSP
  infrastructure.

The actual ``httpx.Client`` or ``ssl.SSLContext`` construction happens in the
DCR runner (Phase 5), not here.  This module is intentionally free of
transport-library imports so it can be used in validation, config parsing,
and test contexts without pulling in the HTTP stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

DcrTokenEndpointAuthMethod = Literal["tls_client_auth", "private_key_jwt"]
"""FAPI 1 Advanced-compatible token-endpoint client authentication methods.

Only ``tls_client_auth`` and ``private_key_jwt`` are accepted for DCR
operations.  ``tls_client_auth`` is preferred when advertised by the ASPSP's
discovery metadata.  Other methods (``client_secret_post``, ``none``, etc.)
are rejected as incompatible with FAPI 1 Advanced.
"""

# ---------------------------------------------------------------------------
# Domain dataclass
# ---------------------------------------------------------------------------

_TLS_SKIP_VERIFY_WARNING: str = (
    "DCR tls_skip_verify is enabled: TLS server certificate verification is "
    "disabled.  This is unsafe and must never be used against real ASPSP "
    "infrastructure or in certification runs."
)
"""Warning text emitted when ``tls_skip_verify`` is ``True`` in a :class:`DcrTransportConfig`."""


@dataclass(frozen=True)
class DcrTransportConfig:
    """mTLS transport options for DCR HTTP connections.

    Controls TLS and keep-alive behaviour for the HTTP client used in all DCR
    requests (OIDC discovery, registration, token, retrieve, update, delete).

    Attributes:
        token_endpoint_auth_method: Client authentication method for the
            token endpoint.  Must be FAPI 1 Advanced-compatible.
        disable_keep_alives: When ``True``, HTTP keep-alives are disabled and
            each request uses a fresh TCP connection.  Useful in some
            mTLS environments where the ASPSP does not support connection
            reuse with client certificates.
        tls_skip_verify: When ``True``, TLS server certificate verification
            is disabled.  This is advanced/unsafe and must never be used
            against real ASPSP infrastructure.  Defaults to ``False``.
        connection_timeout_seconds: Per-request connection timeout in seconds.
        read_timeout_seconds: Per-request read timeout in seconds.
    """

    token_endpoint_auth_method: DcrTokenEndpointAuthMethod
    disable_keep_alives: bool = False
    tls_skip_verify: bool = False
    connection_timeout_seconds: float = field(default=30.0)
    read_timeout_seconds: float = field(default=30.0)

    def __post_init__(self) -> None:
        """Emit a warning when tls_skip_verify is enabled.

        Raises:
            ValueError: If ``connection_timeout_seconds`` or
                ``read_timeout_seconds`` is not a positive number.
        """
        if self.tls_skip_verify:
            import warnings

            warnings.warn(_TLS_SKIP_VERIFY_WARNING, stacklevel=3)
        if self.connection_timeout_seconds <= 0:
            raise ValueError("DcrTransportConfig.connection_timeout_seconds must be positive")
        if self.read_timeout_seconds <= 0:
            raise ValueError("DcrTransportConfig.read_timeout_seconds must be positive")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def tls_skip_verify_warning() -> str:
    """Return the canonical warning text for tls_skip_verify being enabled.

    Exposed as a module-level function so tests can assert the exact warning
    message without duplicating the constant.

    Returns:
        The warning string emitted when :attr:`DcrTransportConfig.tls_skip_verify`
        is ``True``.
    """
    return _TLS_SKIP_VERIFY_WARNING
