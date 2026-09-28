"""Credential material that is either a file reference or inline PEM text.

Participants running the hardened container previously had to mount ``/certs``
read-only before any credential could be configured, because every credential
was an absolute path. This module adds an equally supported alternative:
material pasted or uploaded in the browser wizard and carried inline in the
configuration.

Each credential is *either* a path reference *or* inline text, never both.
Inline material is consumed directly wherever the underlying library accepts
bytes (``joserfc.jwk.import_key``, ``ssl.SSLContext.load_verify_locations``
with ``cadata``, and the software statement assertion, which is already text).
``ssl.SSLContext.load_cert_chain`` is the single exception: OpenSSL only
accepts filesystem paths there, so :func:`apply_client_certificate`
materialises the pair into an anonymous ``memfd`` (Linux) or a ``0600``
temporary file, loads the chain, and removes it in a ``finally`` block on both
success and failure. No secret is ever written to a durable location.

Inline values must never reach logs, tracebacks, evidence, or shared result
files: :class:`CredentialMaterial` redacts itself in ``repr``/``str``, and
:func:`scrub_pem` removes PEM blocks from free-text output.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from cryptography import x509

from conformance.json_types import JsonValue

MAX_INLINE_CREDENTIAL_BYTES: Final[int] = 64 * 1024
"""Upper bound on one inline credential, bounding Django session growth."""

_PEM_BLOCK_PATTERN: Final[str] = "-----BEGIN "
"""Opening marker of a PEM block, used by :func:`scrub_pem`."""

REDACTED_INLINE: Final[str] = "<inline material redacted>"
"""Placeholder rendered instead of inline material in ``repr``/``str``."""

type CredentialKind = Literal["certificate", "private_key", "ca_bundle", "assertion"]
"""Artefact a credential is expected to carry, used for non-secret descriptors."""


class CredentialError(ValueError):
    """Raised when credential material is malformed or cannot be accessed."""


@dataclass(frozen=True)
class CredentialMaterial:
    """One credential supplied either by path reference or as inline text.

    Exactly one of ``path`` and ``inline`` is set. ``__repr__`` and ``__str__``
    redact ``inline`` so pasted private keys cannot leak through tracebacks,
    debug dumps, or ``f``-string interpolation of the configuration object.

    Attributes:
        path: Absolute filesystem reference, when supplied as a path.
        inline: PEM or compact-JWS text, when supplied inline.
    """

    path: Path | None = None
    inline: str | None = None

    def __post_init__(self) -> None:
        """Enforce the exactly-one-form invariant.

        Raises:
            CredentialError: If neither or both forms are supplied.
        """
        if (self.path is None) == (self.inline is None):
            raise CredentialError("Credential material must be either a path reference or inline material")

    @property
    def is_inline(self) -> bool:
        """Whether this credential carries inline material.

        Returns:
            ``True`` when the credential holds inline text.
        """
        return self.inline is not None

    def __repr__(self) -> str:
        """Return a representation that never contains inline material.

        Returns:
            Path-bearing representation, or a redacted inline marker.
        """
        if self.path is not None:
            return f"CredentialMaterial(path={self.path!r})"
        return f"CredentialMaterial(inline={REDACTED_INLINE!r})"

    def __str__(self) -> str:
        """Return a printable form that never contains inline material.

        Returns:
            The path as text, or a redacted inline marker.
        """
        return str(self.path) if self.path is not None else REDACTED_INLINE


def credential_from_path(path: Path) -> CredentialMaterial:
    """Build path-referenced credential material.

    Args:
        path: Absolute filesystem reference.

    Returns:
        Credential material carrying the path.
    """
    return CredentialMaterial(path=path)


def credential_from_inline(text: str) -> CredentialMaterial:
    """Build inline credential material from pasted or uploaded text.

    Args:
        text: Raw PEM or compact-JWS text.

    Returns:
        Credential material carrying normalised inline text.

    Raises:
        CredentialError: If the text is blank or exceeds the size ceiling.
    """
    return CredentialMaterial(inline=normalise_inline_material(text))


def normalise_inline_material(text: str) -> str:
    """Normalise pasted credential text for storage and parsing.

    Browsers submit textarea content with CRLF line endings, and participants
    frequently paste material with leading or trailing blank lines. PEM parsers
    tolerate neither reliably, so line endings are normalised to LF, trailing
    whitespace is stripped per line, and a single trailing newline is applied.

    Args:
        text: Raw pasted or uploaded credential text.

    Returns:
        Normalised text ending in a single newline.

    Raises:
        CredentialError: If the text is blank or exceeds
            :data:`MAX_INLINE_CREDENTIAL_BYTES`.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalised.split("\n")]
    body = "\n".join(lines).strip()
    if not body:
        raise CredentialError("Inline credential material must not be empty")
    if len(body.encode("utf-8")) > MAX_INLINE_CREDENTIAL_BYTES:
        raise CredentialError(f"Inline credential material must not exceed {MAX_INLINE_CREDENTIAL_BYTES} bytes")
    # PEM parsers require the final armour line to be newline-terminated, while
    # a compact JWS is a single token that a trailing newline would corrupt.
    return f"{body}\n" if _PEM_BLOCK_PATTERN in body else body


def validate_inline_material(text: str, *, kind: CredentialKind) -> str:
    """Validate that pasted or uploaded material is the expected artefact.

    The check is structural only, so operators are told immediately when they
    paste the wrong file (a certificate into a private-key box, for example)
    rather than discovering it during a run. It deliberately does not perform
    cryptographic verification, and never echoes the material.

    Args:
        text: Raw pasted or uploaded credential text.
        kind: Artefact the credential is expected to carry.

    Returns:
        Normalised material.

    Raises:
        CredentialError: If the material is blank, oversized, or is clearly not
            the expected artefact type.
    """
    normalised = normalise_inline_material(text)
    if kind == "assertion":
        segments = normalised.split(".")
        if len(segments) != 3 or not all(segments):
            raise CredentialError("Paste the software statement assertion as a compact JWS with three segments")
        return normalised
    if kind in {"certificate", "ca_bundle"}:
        if "-----BEGIN CERTIFICATE-----" not in normalised:
            raise CredentialError("Paste PEM content containing a BEGIN CERTIFICATE block")
        return normalised
    if "PRIVATE KEY-----" not in normalised:
        # The message names the PEM armour marker so participants can check
        # what they pasted; it carries no credential material.
        raise CredentialError("Paste PEM content containing a BEGIN PRIVATE KEY block")  # pragma: allowlist secret
    return normalised


def credential_bytes(material: CredentialMaterial, *, label: str) -> bytes:
    """Return credential bytes without echoing the material in errors.

    Args:
        material: Credential to read.
        label: Human-readable configuration field name for error reporting.

    Returns:
        Raw credential bytes.

    Raises:
        CredentialError: If a path-referenced credential cannot be read.
    """
    path = material.path
    if path is None:
        return (material.inline or "").encode("utf-8")
    try:
        return path.read_bytes()
    except OSError as error:
        raise CredentialError(f"Unable to read {label} from disk") from error


def credential_text(material: CredentialMaterial, *, label: str) -> str:
    """Return credential text without echoing the material in errors.

    Args:
        material: Credential to read.
        label: Human-readable configuration field name for error reporting.

    Returns:
        Credential text, stripped of surrounding whitespace.

    Raises:
        CredentialError: If a path-referenced credential cannot be decoded.
    """
    try:
        return credential_bytes(material, label=label).decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise CredentialError(f"{label} must contain UTF-8 text") from error


def parse_credential_material(
    config: Mapping[str, JsonValue],
    *,
    path_key: str,
    inline_key: str,
    location: str | None = None,
    require_existing: bool = False,
) -> CredentialMaterial | None:
    """Parse one credential supplied as either a path or inline material.

    Args:
        config: JSON object containing the credential keys.
        path_key: Key carrying an absolute path reference.
        inline_key: Key carrying inline PEM or compact-JWS text.
        location: Parent configuration location used in error messages.
        require_existing: Whether a path reference must already exist on disk.

    Returns:
        Parsed credential material, or ``None`` when neither key is present.

    Raises:
        CredentialError: If both forms are supplied for the same credential, a
            value is not a non-empty string, a path is not absolute, or a
            required path does not exist.
    """
    path_label = _qualified(location, path_key)
    inline_label = _qualified(location, inline_key)
    raw_path = config.get(path_key)
    raw_inline = config.get(inline_key)
    if raw_path is not None and raw_inline is not None:
        raise CredentialError(f"{path_label} and {inline_label} must not both be supplied")

    if raw_inline is not None:
        if not isinstance(raw_inline, str):
            raise CredentialError(f"{inline_label} must be a non-empty string when supplied")
        try:
            return credential_from_inline(raw_inline)
        except CredentialError as error:
            raise CredentialError(f"{inline_label}: {error}") from error

    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CredentialError(f"{path_label} must be a non-empty string when supplied")
    path = Path(raw_path.strip())
    if not path.is_absolute():
        raise CredentialError(f"{path_label} must be an absolute file path")
    resolved = path.resolve()
    if require_existing and not resolved.is_file():
        raise CredentialError(f"{path_label} must point to an existing file")
    return credential_from_path(resolved)


def _qualified(location: str | None, key: str) -> str:
    """Join an optional configuration location with a field key.

    Args:
        location: Parent configuration location, when present.
        key: Field key.

    Returns:
        Dotted label used in error messages.
    """
    return f"{location}.{key}" if location else key


def apply_ca_bundle(context: ssl.SSLContext, material: CredentialMaterial) -> None:
    """Load a CA bundle into an SSL context from a path or inline PEM.

    Args:
        context: SSL context to configure.
        material: CA bundle credential.

    Raises:
        CredentialError: If the bundle cannot be loaded.
    """
    try:
        if material.inline is not None:
            context.load_verify_locations(cadata=material.inline)
        else:
            context.load_verify_locations(cafile=str(material.path))
    except (OSError, ssl.SSLError) as error:
        raise CredentialError("Unable to load the configured TLS CA bundle") from error


def apply_client_certificate(
    context: ssl.SSLContext,
    certificate: CredentialMaterial,
    private_key: CredentialMaterial,
) -> None:
    """Load an mTLS client certificate and key into an SSL context.

    ``ssl.SSLContext.load_cert_chain`` accepts only filesystem paths, so inline
    material is materialised into an anonymous ``memfd`` where available, and
    otherwise into a ``0600`` temporary file with a random name. The descriptor
    and any temporary path are closed and unlinked in a ``finally`` block, so
    nothing survives either a successful load or a load failure.

    Args:
        context: SSL context to configure.
        certificate: Client certificate credential.
        private_key: Client private-key credential.

    Raises:
        CredentialError: If the certificate or key cannot be loaded.
    """
    with ExitStack() as stack:
        try:
            certificate_file = stack.enter_context(_materialised(certificate))
            private_key_file = stack.enter_context(_materialised(private_key))
            context.load_cert_chain(certfile=certificate_file, keyfile=private_key_file)
        except (OSError, ssl.SSLError) as error:
            raise CredentialError("Unable to load the configured TLS client certificate and private key") from error


@contextmanager
def _materialised(material: CredentialMaterial) -> Iterator[str]:
    """Yield a filesystem path for credential material, cleaning up inline copies.

    Args:
        material: Credential to expose as a path.

    Yields:
        Filesystem path usable by OpenSSL for the duration of the context.
    """
    if material.inline is None:
        yield str(material.path)
        return

    payload = material.inline.encode("utf-8")
    memfd_create = getattr(os, "memfd_create", None)
    if memfd_create is not None:
        descriptor = cast(int, memfd_create("credential", 0))
        try:
            os.write(descriptor, payload)
            yield f"/proc/self/fd/{descriptor}"
        finally:
            os.close(descriptor)
        return

    descriptor, temporary_path = tempfile.mkstemp(suffix=".pem")
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
        os.close(descriptor)
        yield temporary_path
    finally:
        Path(temporary_path).unlink(missing_ok=True)


def describe_credential(material: CredentialMaterial | None, *, kind: CredentialKind) -> str | None:
    """Summarise a credential for the wizard without revealing its material.

    Stored inline material is never re-rendered in the browser. This produces
    the non-secret descriptor shown beside the "Configured" badge: certificate
    subject and expiry, software statement ``software_id``/``kid``, or a
    neutral artefact description when nothing safe can be derived.

    Args:
        material: Credential to describe, or ``None`` when unconfigured.
        kind: Artefact the credential is expected to carry.

    Returns:
        Short non-secret descriptor, or ``None`` when unconfigured.
    """
    if material is None:
        return None
    if material.path is not None:
        return f"File reference: {material.path}"
    inline = material.inline or ""
    if kind == "certificate":
        return _describe_certificate(inline)
    if kind == "ca_bundle":
        count = inline.count("-----BEGIN CERTIFICATE-----")
        return f"Pasted CA bundle ({count} certificate{'s' if count != 1 else ''})"
    if kind == "assertion":
        return _describe_assertion(inline)
    return "Pasted private key"


def _describe_certificate(pem: str) -> str:
    """Derive a subject and expiry descriptor from an inline PEM certificate.

    Args:
        pem: Inline PEM certificate text.

    Returns:
        Subject and ``notAfter`` summary, or a neutral fallback.
    """
    try:
        certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError:
        return "Pasted certificate"
    subject = certificate.subject.rfc4514_string()
    expiry = certificate.not_valid_after_utc.date().isoformat()
    return f"Pasted certificate: {subject} (expires {expiry})"


def _describe_assertion(token: str) -> str:
    """Derive non-secret identifiers from an inline software statement assertion.

    The SSA signature is deliberately not verified here: the canonical plan
    carries no trust anchor for it, and verification belongs to the receiving
    ASPSP. Only the unverified ``kid`` header and ``software_id`` claim are
    read, purely to label the configured value in the wizard.

    Args:
        token: Compact JWS software statement assertion.

    Returns:
        ``software_id``/``kid`` summary, or a neutral fallback.
    """
    segments = token.strip().split(".")
    if len(segments) != 3:
        return "Pasted software statement assertion"
    parts: list[str] = []
    for segment, field in ((segments[0], "kid"), (segments[1], "software_id")):
        value = _unverified_claim(segment, field)
        if value is not None:
            parts.append(f"{field}={value}")
    if not parts:
        return "Pasted software statement assertion"
    joined = ", ".join(parts)
    return f"Pasted software statement assertion ({joined})"


def _unverified_claim(segment: str, field: str) -> str | None:
    """Read one string member from an unverified base64url JWS segment.

    Args:
        segment: Base64url-encoded JWS header or payload segment.
        field: Member name to read.

    Returns:
        The member value, or ``None`` when absent or undecodable.
    """
    try:
        padded = segment + "=" * (-len(segment) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    value = decoded.get(field)
    return value if isinstance(value, str) and value else None


def scrub_pem(text: str) -> str:
    """Replace PEM blocks in free text with a redaction placeholder.

    Defence in depth for evidence, log, and error paths that interpolate
    arbitrary strings: even if inline material reaches one of them, the PEM
    body is removed before the text is persisted or displayed.

    Args:
        text: Arbitrary free text that may embed PEM blocks.

    Returns:
        The text with each PEM block replaced by ``***``.
    """
    if _PEM_BLOCK_PATTERN not in text:
        return text
    result: list[str] = []
    remaining = text
    while True:
        start = remaining.find(_PEM_BLOCK_PATTERN)
        if start == -1:
            result.append(remaining)
            break
        result.append(remaining[:start])
        result.append("***")
        end_marker = remaining.find("-----END ", start)
        if end_marker == -1:
            break
        closing = remaining.find("-----", end_marker + len("-----END "))
        remaining = remaining[closing + len("-----") :] if closing != -1 else ""
    return "".join(result)
