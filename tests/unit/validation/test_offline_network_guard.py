"""Regression tests for the suite-wide external-network guard."""

from __future__ import annotations

import socket

import pytest

from tests.conftest import ExternalNetworkAccessError, _is_loopback_target, _reject_external_target

pytestmark = pytest.mark.unit


def test_offline_guard_allows_only_local_targets() -> None:
    """Allow loopback IPs, known loopback hostnames, and local socket paths."""
    allowed_targets: tuple[object, ...] = (
        ("127.0.0.1", 443),
        ("::1", 443, 0, 0),
        ("localhost", 443),
        "conformance-suite.sock",
    )

    for target in allowed_targets:
        assert _is_loopback_target(target)
        _reject_external_target(target)


def test_offline_guard_rejects_external_targets() -> None:
    """Reject external IPv4, IPv6, and hostname destinations."""
    blocked_targets: tuple[object, ...] = (
        ("192.0.2.1", 443),
        ("2001:db8::1", 443, 0, 0),
        ("example.com", 443),
    )

    for target in blocked_targets:
        assert not _is_loopback_target(target)
        with pytest.raises(ExternalNetworkAccessError, match="Offline suite blocked a connection"):
            _reject_external_target(target)


def test_offline_guard_intercepts_real_socket_connections() -> None:
    """Prove the autouse fixture blocks a real socket before network access."""
    with socket.socket() as outbound_socket:
        with pytest.raises(ExternalNetworkAccessError, match="192.0.2.1"):
            outbound_socket.connect(("192.0.2.1", 443))
