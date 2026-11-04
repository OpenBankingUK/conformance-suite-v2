"""Shared URL validation for conformance network boundaries."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_address
from urllib.parse import ParseResult, urlparse


class HttpsUrlValidationError(ValueError):
    """Raised when a value is not a hardened HTTPS URL."""


_LEGACY_FCS_REDIRECT_HOST = "0.0.0.0"  # noqa: S104 - legacy registered redirect host, not a bind target.
"""Host literal used by the previous FCS Ozone model-bank redirect registration."""

_IPV4_THIS_NETWORK = IPv4Network("0.0.0.0/8")
"""RFC 1122 section 3.2.1.3 "this network" block, which is never a valid destination host."""

_IPV4_RESERVED = IPv4Network("240.0.0.0/4")
"""IANA reserved block, including the limited broadcast address."""


def validate_https_url(value: str, *, label: str) -> None:
    """Validate a URL before it is accepted as an HTTPS network target.

    Args:
        value: URL string to validate.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the URL is not safe to fetch.
    """
    parsed_url = _parse_https_url(value, label=label)
    assert parsed_url.hostname is not None  # noqa: S101 - guaranteed by _parse_https_url.
    _validate_hostname(parsed_url.hostname, label=label)


def _parse_https_url(value: str, *, label: str) -> ParseResult:
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
    try:
        parsed_url = urlparse(value)
        parsed_port = parsed_url.port
    except ValueError as error:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL") from error

    if parsed_port is not None and parsed_port <= 0:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        raise HttpsUrlValidationError(f"{label} must be an HTTPS URL")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise HttpsUrlValidationError(f"{label} must not include credentials")
    return parsed_url


def validate_oauth_redirect_uri(value: str, *, label: str) -> None:
    """Validate an OB DCR/FAPI HTTPS browser callback without fetching it.

    RFC 6749 section 3.1.2 requires an absolute URI without a fragment, OB DCR
    3.4 requires HTTPS and forbids the host ``localhost``, and FAPI 1 requires
    exact pre-registered matching. None of them bans IP literals, so a DNS
    hostname or a unicast IPv4/IPv6 literal such as ``127.0.0.1`` or ``[::1]``
    is accepted. ``0.0.0.0`` is the unspecified address rather than a
    destination, so only the exact FCS v1 URI
    ``https://0.0.0.0:8443/conformancesuite/callback`` is accepted. Unlike
    outbound URLs, callbacks are browser destinations that FCS never fetches,
    so the outbound IP-literal restriction does not apply. Acceptance never
    proves Directory or ASPSP registration, and callers must preserve the
    original URI for exact registered-URI matching.

    Args:
        value: Redirect URI string to validate.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the URI violates the callback policy.
    """
    parsed_url = _parse_https_url(value, label=label)
    hostname = parsed_url.hostname
    assert hostname is not None  # noqa: S101 - guaranteed by _parse_https_url.
    if "#" in value:
        raise HttpsUrlValidationError(f"{label} must not include a fragment")
    if "\\" in value:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
    if _is_legacy_fcs_redirect_uri(value, parsed_url):
        return
    try:
        address = ip_address(hostname)
    except ValueError:
        # A bracketed host that is not an IP address is an IPvFuture literal, which has no usable destination.
        if parsed_url.netloc.startswith("["):
            raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL") from None
        _validate_callback_dns_hostname(hostname, label=label)
        return
    _validate_callback_ip_literal(address, label=label)


def _validate_callback_dns_hostname(hostname: str, *, label: str) -> None:
    """Validate a DNS callback host, rejecting ``localhost`` and numeric IPv4 spellings.

    Args:
        hostname: Non-IP hostname extracted from a parsed callback URI.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the host is ``localhost``, malformed, or
            spelled as an abbreviated, integer, octal or hexadecimal IPv4 address.
    """
    dns_hostname = hostname.removesuffix(".")
    if dns_hostname == "localhost" or dns_hostname.endswith(".localhost"):
        raise HttpsUrlValidationError(f"{label} must not use localhost (Open Banking DCR 3.4)")
    _validate_dns_hostname(hostname, label=label)
    # Browsers read a final numeric or hex label as an IPv4 address, so such
    # spellings are ambiguous aliases of an IP literal rather than DNS names.
    last_label = dns_hostname.rsplit(".", 1)[-1]
    if last_label.isdigit() or (
        last_label.startswith("0x") and all(character in "0123456789abcdef" for character in last_label[2:])
    ):
        raise HttpsUrlValidationError(
            f"{label} must use a DNS hostname, a dotted-decimal IPv4 or a bracketed IPv6 literal"
        )


def _validate_callback_ip_literal(address: IPv4Address | IPv6Address, *, label: str) -> None:
    """Validate that an IP-literal callback host is a unicast destination a browser can connect to.

    Args:
        address: Parsed IP literal from the callback URI host.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the address is unspecified, multicast,
            reserved, IPv4-mapped, scoped or IPv6 link-local.
    """
    if address == IPv4Address(_LEGACY_FCS_REDIRECT_HOST):
        raise HttpsUrlValidationError(
            f"{label} may use 0.0.0.0 only as the FCS v1 callback https://0.0.0.0:8443/conformancesuite/callback"
        )
    if address.is_unspecified or address.is_multicast:
        raise HttpsUrlValidationError(f"{label} must use a unicast IP address or DNS hostname")
    if isinstance(address, IPv4Address):
        unusable = address in _IPV4_THIS_NETWORK or address in _IPV4_RESERVED
    else:
        unusable = address.scope_id is not None or address.ipv4_mapped is not None or address.is_link_local
    if unusable:
        raise HttpsUrlValidationError(f"{label} must use a unicast IP address or DNS hostname")


def _is_legacy_fcs_redirect_uri(value: str, parsed_url: ParseResult) -> bool:
    """Return whether ``value`` is the previous FCS model-bank callback URI.

    Args:
        value: Redirect URI string already accepted by ``_parse_https_url``.
        parsed_url: Parse result of ``value`` from ``_parse_https_url``.

    Returns:
        ``True`` only for the old registered callback shape used by the
        previous FCS against the Ozone model bank.
    """
    return (
        parsed_url.hostname == _LEGACY_FCS_REDIRECT_HOST
        and parsed_url.port == 8443
        and parsed_url.path.rstrip("/") == "/conformancesuite/callback"
        and ";" not in value
        and "?" not in value
    )


def _validate_hostname(hostname: str, *, label: str) -> None:
    """Validate a URL hostname as a DNS name, rejecting IP literals.

    Args:
        hostname: Hostname component extracted from a parsed URL.
        label: Human-readable label used in error messages (e.g. field name).

    Raises:
        HttpsUrlValidationError: If the hostname is not a valid DNS hostname.
    """
    try:
        ip_address(hostname)
    except ValueError:
        pass
    else:
        raise HttpsUrlValidationError(f"{label} must use a DNS hostname, not an IP literal")
    _validate_dns_hostname(hostname, label=label)


def _validate_dns_hostname(hostname: str, *, label: str) -> None:
    """Validate a DNS hostname per RFC 1123 label rules.

    Args:
        hostname: The DNS hostname to validate (e.g. "example.com").
        label: A human-readable label identifying the field being validated,
            used in error messages.

    Raises:
        HttpsUrlValidationError: If the hostname violates DNS naming rules.
    """
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as error:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL") from error

    trimmed_hostname = hostname.removesuffix(".")
    labels = trimmed_hostname.split(".")
    if not trimmed_hostname or len(trimmed_hostname) > 253:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
    for hostname_label in labels:
        if (
            not hostname_label
            or len(hostname_label) > 63
            or hostname_label.startswith("-")
            or hostname_label.endswith("-")
        ):
            raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
        if not all(character.isalnum() or character == "-" for character in hostname_label):
            raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
