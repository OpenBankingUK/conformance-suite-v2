"""Unit tests for DcrTransportConfig."""

from __future__ import annotations

import warnings

import pytest

from conformance.dcr.transport import (
    DcrTransportConfig,
    tls_skip_verify_warning,
)


# ---------------------------------------------------------------------------
# DcrTransportConfig construction — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_transport_config() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")
    assert config.token_endpoint_auth_method == "tls_client_auth"
    assert config.disable_keep_alives is False
    assert config.tls_skip_verify is False
    assert config.connection_timeout_seconds == 30.0
    assert config.read_timeout_seconds == 30.0


@pytest.mark.unit
def test_private_key_jwt_auth_method() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="private_key_jwt")
    assert config.token_endpoint_auth_method == "private_key_jwt"


@pytest.mark.unit
def test_disable_keep_alives() -> None:
    config = DcrTransportConfig(
        token_endpoint_auth_method="tls_client_auth",
        disable_keep_alives=True,
    )
    assert config.disable_keep_alives is True


@pytest.mark.unit
def test_custom_timeouts() -> None:
    config = DcrTransportConfig(
        token_endpoint_auth_method="tls_client_auth",
        connection_timeout_seconds=60.0,
        read_timeout_seconds=120.0,
    )
    assert config.connection_timeout_seconds == 60.0
    assert config.read_timeout_seconds == 120.0


# ---------------------------------------------------------------------------
# DcrTransportConfig — tls_skip_verify warning
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tls_skip_verify_emits_warning() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",
            tls_skip_verify=True,
        )
    assert len(w) == 1
    assert tls_skip_verify_warning() in str(w[0].message)


@pytest.mark.unit
def test_no_warning_when_tls_skip_verify_false() -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")
    assert len(w) == 0


@pytest.mark.unit
def test_tls_skip_verify_warning_contains_unsafe_message() -> None:
    msg = tls_skip_verify_warning()
    assert "unsafe" in msg.lower()


# ---------------------------------------------------------------------------
# DcrTransportConfig — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_zero_connection_timeout_raises() -> None:
    with pytest.raises(ValueError, match="connection_timeout_seconds"):
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",
            connection_timeout_seconds=0,
        )


@pytest.mark.unit
def test_negative_read_timeout_raises() -> None:
    with pytest.raises(ValueError, match="read_timeout_seconds"):
        DcrTransportConfig(
            token_endpoint_auth_method="tls_client_auth",
            read_timeout_seconds=-1.0,
        )


# ---------------------------------------------------------------------------
# DcrTransportConfig is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_transport_config_is_frozen() -> None:
    config = DcrTransportConfig(token_endpoint_auth_method="tls_client_auth")
    with pytest.raises(Exception):
        config.disable_keep_alives = True  # type: ignore[misc]
