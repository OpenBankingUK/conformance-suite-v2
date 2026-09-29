"""Suite-wide enforcement of the two offline guarantees the root conftest owns.

Every collected test must declare exactly one supported category, and no test
may open a connection to anything but the loopback interface. Fixtures live in
the narrowest package that consumes them, and shared fakes and builders live in
:mod:`tests.support`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator, Sequence
from typing import Any

import pytest

SUPPORTED_CATEGORIES = ("unit", "component")
"""The only pytest category markers this offline suite supports."""

LOOPBACK_HOSTNAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})
"""Hostnames treated as loopback without resolving them through DNS."""


class ExternalNetworkAccessError(RuntimeError):
    """Raised when a test tries to connect to anything but the loopback interface."""


def _is_loopback_target(address: object) -> bool:
    """Report whether a socket destination stays on the local host.

    Args:
        address: The address argument passed to ``socket.socket.connect``.

    Returns:
        ``True`` for loopback IP destinations and for address families that
        cannot leave the host (e.g. ``AF_UNIX`` paths), ``False`` otherwise.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("utf-8", errors="replace")
    if not isinstance(host, str):
        return False
    if host in LOOPBACK_HOSTNAMES:
        return True
    try:
        # Strip any IPv6 zone index (``fe80::1%en0``) before parsing.
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _reject_external_target(address: object) -> None:
    """Fail loudly when a socket destination would leave the local host.

    Args:
        address: The address argument passed to ``socket.socket.connect``.

    Raises:
        ExternalNetworkAccessError: If the destination is not loopback.
    """
    if _is_loopback_target(address):
        return
    raise ExternalNetworkAccessError(
        f"Offline suite blocked a connection to {address!r}. No supported test may contact Ozone, "
        "a bank, a model bank, or any other external endpoint: mock HTTP at the client boundary "
        "(httpx.MockTransport), or use the loopback fixtures when transport behaviour is under test."
    )


@pytest.fixture(scope="session", autouse=True)
def _offline_network_guard() -> Iterator[None]:
    """Block outbound sockets for the whole session while allowing loopback.

    The supported suite is offline by design, but marker names alone cannot
    prove it. Guarding ``socket.socket`` proves it for every client the product
    can build — ``httpx``, ``ssl``, and the standard library all route through
    these two methods — while leaving the loopback TLS/mTLS transport fixtures
    fully functional.

    Yields:
        Control back to pytest for the duration of the session.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    # ``Any`` mirrors the stdlib signature: the address shape depends on the
    # socket's address family and typeshed models it with a private alias.
    def guarded_connect(self: socket.socket, address: Any) -> None:
        _reject_external_target(address)
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _reject_external_target(address)
        return real_connect_ex(self, address)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(socket.socket, "connect", guarded_connect)
        patcher.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        yield


def pytest_collection_modifyitems(items: Sequence[pytest.Item]) -> None:
    """Fail collection unless every collected test carries exactly one category.

    The supported suite is offline-only and split into ``unit`` and
    ``component`` for ownership and focused iteration. A test with no category
    would silently escape both focused selections, and a test with both would
    run twice, so either state is a collection error rather than a warning.

    Args:
        items: Collected test items, in pytest collection order.

    Raises:
        pytest.UsageError: If any item has zero or more than one category marker.
    """
    offenders: list[str] = []
    for item in items:
        applied = [name for name in SUPPORTED_CATEGORIES if item.get_closest_marker(name) is not None]
        if len(applied) == 1:
            continue
        reason = "no category marker" if not applied else f"multiple category markers ({', '.join(applied)})"
        offenders.append(f"{item.nodeid}: {reason}")

    if not offenders:
        return

    expected = " or ".join(f"@pytest.mark.{name}" for name in SUPPORTED_CATEGORIES)
    detail = "\n".join(f"  - {offender}" for offender in sorted(set(offenders)))
    raise pytest.UsageError(
        f"Every test must declare exactly one category marker ({expected}).\n"
        f"{len(set(offenders))} offending test(s):\n{detail}"
    )
