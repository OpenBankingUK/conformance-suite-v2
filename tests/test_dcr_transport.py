"""Unit tests for DcrTransportConfig."""

from __future__ import annotations

import dataclasses

import pytest

from conformance.dcr.transport import (
    DcrTransportConfig,
    tls_skip_verify_error,
)

# ---------------------------------------------------------------------------
# DcrTransportConfig construction — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_transport_config() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")  # noqa: S106
    assert config.token_endpoint_auth_method == "tls_client_auth"  # noqa: S105
    assert config.disable_keep_alives is False
    assert config.tls_skip_verify is False
    assert config.connection_timeout_seconds == 30.0
    assert config.read_timeout_seconds == 30.0


@pytest.mark.unit
def test_private_key_jwt_auth_method() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="private_key_jwt")  # noqa: S106
    assert config.token_endpoint_auth_method == "private_key_jwt"  # noqa: S105


@pytest.mark.unit
def test_disable_keep_alives() -> None:
    config = DcrTransportConfig(
        token_endpoint_auth_method="tls_client_auth",  # noqa: S106
        disable_keep_alives=True,
    )
    assert config.disable_keep_alives is True


@pytest.mark.unit
def test_custom_timeouts() -> None:
    config = DcrTransportConfig(
        token_endpoint_auth_method="tls_client_auth",  # noqa: S106
        connection_timeout_seconds=60.0,
        read_timeout_seconds=120.0,
    )
    assert config.connection_timeout_seconds == 60.0
    assert config.read_timeout_seconds == 120.0


# ---------------------------------------------------------------------------
# DcrTransportConfig — tls_skip_verify rejection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tls_skip_verify_raises() -> None:
    """``tls_skip_verify=True`` is rejected instead of disabling verification."""
    with pytest.raises(ValueError, match="certificate verification"):
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",  # noqa: S106
            tls_skip_verify=True,
        )


@pytest.mark.unit
def test_accepts_tls_skip_verify_false() -> None:
    """``tls_skip_verify=False`` remains the default supported value."""
    config = DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")  # noqa: S106
    assert config.tls_skip_verify is False


@pytest.mark.unit
def test_tls_skip_verify_error_contains_remediation() -> None:
    """The rejection message directs users to CA bundle trust instead."""
    msg = tls_skip_verify_error()
    assert "trusted CA bundle" in msg


# ---------------------------------------------------------------------------
# DcrTransportConfig — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_zero_connection_timeout_raises() -> None:
    with pytest.raises(ValueError, match="connection_timeout_seconds"):
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",  # noqa: S106
            connection_timeout_seconds=0,
        )


@pytest.mark.unit
def test_negative_read_timeout_raises() -> None:
    with pytest.raises(ValueError, match="read_timeout_seconds"):
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",  # noqa: S106
            read_timeout_seconds=-1.0,
        )


# ---------------------------------------------------------------------------
# DcrTransportConfig is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_transport_config_is_frozen() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")  # noqa: S106
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.disable_keep_alives = True  # type: ignore[misc]
