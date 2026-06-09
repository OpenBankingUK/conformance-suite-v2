"""Shared URL validation for conformance network boundaries."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse


class HttpsUrlValidationError(ValueError):
    """Raised when a value is not a hardened HTTPS URL."""


_LEGACY_FCS_REDIRECT_HOST = "0.0.0.0"  # noqa: S104 - legacy registered redirect host, not a bind target.
"""Host literal used by the previous FCS Ozone model-bank redirect registration."""


def validate_https_url(value: str, *, label: str) -> None:
    """Validate a URL before it is accepted as an HTTPS network target.

    Args:
        value: URL string to validate.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the URL is not safe to fetch.
    """
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")

    parsed_url = urlparse(value)
    try:
        parsed_port = parsed_url.port
    except ValueError as error:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL") from error

    if parsed_port is not None and parsed_port <= 0:
        raise HttpsUrlValidationError(f"{label} must be a valid HTTPS URL")
    if parsed_url.scheme != "https" or parsed_url.hostname is None:
        raise HttpsUrlValidationError(f"{label} must be an HTTPS URL")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise HttpsUrlValidationError(f"{label} must not include credentials")
    _validate_hostname(parsed_url.hostname, label=label)


def validate_oauth_redirect_uri(value: str, *, label: str) -> None:
    """Validate an OAuth redirect URI accepted from config or manifests.

    General redirect URIs use :func:`validate_https_url`. The single legacy
    exception preserves compatibility with the previous FCS model-bank
    registration, which used the browser callback URI
    ``https://0.0.0.0:8443/conformancesuite/callback``. This exception is
    deliberately scoped to that host, port, and callback path because
    ``0.0.0.0`` is not a safe general-purpose redirect host.

    Args:
        value: Redirect URI string to validate.
        label: Human-readable field name used in validation errors.

    Raises:
        HttpsUrlValidationError: If the URI is neither a hardened HTTPS URL
            nor the recognised legacy FCS callback URI.
    """
    try:
        validate_https_url(value, label=label)
    except HttpsUrlValidationError:
        if _is_legacy_fcs_redirect_uri(value):
            return
        raise


def _is_legacy_fcs_redirect_uri(value: str) -> bool:
    """Return whether ``value`` is the previous FCS model-bank callback URI.

    Args:
        value: Redirect URI string to classify.

    Returns:
        ``True`` only for the old registered callback shape used by the
        previous FCS against the Ozone model bank.
    """
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    parsed_url = urlparse(value)
    try:
        parsed_port = parsed_url.port
    except ValueError:
        return False
    return (
        parsed_url.scheme == "https"
        and parsed_url.hostname == _LEGACY_FCS_REDIRECT_HOST
        and parsed_port == 8443
        and parsed_url.username is None
        and parsed_url.password is None
        and parsed_url.path.rstrip("/") == "/conformancesuite/callback"
        and not parsed_url.query
        and not parsed_url.fragment
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
