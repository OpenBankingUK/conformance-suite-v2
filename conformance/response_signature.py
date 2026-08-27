"""Validate Open Banking detached response JWS signatures with JWKS keys."""

from __future__ import annotations

import base64
import json
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from joserfc import jwk, jws
from joserfc.errors import JoseError
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter

from conformance.json_types import JsonObject, JsonValue

_OPEN_BANKING_IAT = "http://openbanking.org.uk/iat"
"""Open Banking detached JWS issued-at protected header name."""

_OPEN_BANKING_ISS = "http://openbanking.org.uk/iss"
"""Open Banking detached JWS issuer protected header name."""

_OPEN_BANKING_TAN = "http://openbanking.org.uk/tan"
"""Open Banking detached JWS trust-anchor protected header name."""

_RESPONSE_SIGNATURE_REGISTRY = JWSRegistry(
    header_registry={
        **JWSRegistry.default_header_registry,
        _OPEN_BANKING_IAT: HeaderParameter("Open Banking issued-at header", "int"),
        _OPEN_BANKING_ISS: HeaderParameter("Open Banking issuer header", "str"),
        _OPEN_BANKING_TAN: HeaderParameter("Open Banking trust-anchor header", "str"),
    },
    algorithms=["PS256"],
)
"""JWS registry accepting Open Banking critical protected headers."""

_REQUIRED_OPEN_BANKING_CRITICAL_HEADERS = frozenset({_OPEN_BANKING_IAT, _OPEN_BANKING_ISS, _OPEN_BANKING_TAN})
"""Open Banking protected headers that must be listed as critical."""


class ResponseSignatureValidationError(ValueError):
    """Raised when a response ``x-jws-signature`` header cannot be validated."""


@dataclass(frozen=True)
class ResponseSignatureValidation:
    """Successful response-signature validation details.

    Attributes:
        key_id: JOSE ``kid`` protected header used to select the JWKS key.
        issuer: Open Banking ``iss`` protected header.
        trust_anchor: Open Banking ``tan`` protected header.
    """

    key_id: str
    issuer: str
    trust_anchor: str

    def to_json_object(self) -> JsonObject:
        """Return result-safe validation details.

        Returns:
            JSON object containing non-secret signature verification metadata.
        """
        return {
            "kid": self.key_id,
            "issuer": self.issuer,
            "trustAnchor": self.trust_anchor,
        }


def validate_ob_response_signature(
    *,
    signature: str | None,
    payload: bytes,
    jwks: Mapping[str, JsonValue],
) -> ResponseSignatureValidation:
    """Validate an Open Banking detached response JWS.

    Args:
        signature: Raw ``x-jws-signature`` response header value.
        payload: Exact response body bytes covered by the detached signature.
        jwks: JWKS document fetched from the discovery document's ``jwks_uri``.

    Returns:
        Non-secret details from the validated signature.

    Raises:
        ResponseSignatureValidationError: If the header is absent, malformed,
            uses unsupported protected headers, references no matching JWKS key,
            or fails PS256 verification over ``payload``.
    """
    if signature is None or not signature.strip():
        raise ResponseSignatureValidationError("x-jws-signature header is missing")

    protected_header = _decode_protected_header(signature.strip())
    validation = _validate_protected_header(protected_header)
    verification_key = _select_verification_key(jwks, validation.key_id)

    try:
        jws.deserialize_compact(
            signature.strip(),
            verification_key,
            algorithms=["PS256"],
            payload=payload,
            registry=_RESPONSE_SIGNATURE_REGISTRY,
        )
    except JoseError as error:
        raise ResponseSignatureValidationError("x-jws-signature failed PS256 verification") from error
    return validation


def _decode_protected_header(signature: str) -> JsonObject:
    """Decode the protected header from a detached compact JWS.

    Args:
        signature: Raw ``x-jws-signature`` header value.

    Returns:
        Protected header JSON object.

    Raises:
        ResponseSignatureValidationError: If the compact JWS or protected
            header JSON is malformed.
    """
    segments = signature.split(".")
    if len(segments) != 3:
        raise ResponseSignatureValidationError("x-jws-signature must contain three compact JWS segments")
    if segments[1] != "":
        raise ResponseSignatureValidationError("x-jws-signature must use detached payload form")
    try:
        protected_bytes = _base64url_decode(segments[0])
        protected_header = json.loads(protected_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ResponseSignatureValidationError("x-jws-signature protected header is invalid") from error
    if not isinstance(protected_header, dict):
        raise ResponseSignatureValidationError("x-jws-signature protected header must be a JSON object")
    return cast(JsonObject, protected_header)


def _validate_protected_header(protected_header: Mapping[str, JsonValue]) -> ResponseSignatureValidation:
    """Validate Open Banking response-signature protected header claims.

    Args:
        protected_header: Decoded protected header object from the detached JWS.

    Returns:
        Non-secret values extracted from the protected header.

    Raises:
        ResponseSignatureValidationError: If a mandatory Open Banking or JOSE
            protected header is missing or invalid.
    """
    if protected_header.get("alg") != "PS256":
        raise ResponseSignatureValidationError("x-jws-signature alg must be PS256")
    key_id = _required_header_string(protected_header, "kid")
    jws_type = protected_header.get("typ")
    if jws_type is not None and jws_type != "JOSE":
        raise ResponseSignatureValidationError("x-jws-signature typ must be JOSE when supplied")
    content_type = protected_header.get("cty")
    if content_type is not None and content_type not in {"json", "application/json"}:
        raise ResponseSignatureValidationError("x-jws-signature cty must be json or application/json when supplied")
    crit_header = protected_header.get("crit")
    if not isinstance(crit_header, list) or not all(isinstance(item, str) for item in crit_header):
        raise ResponseSignatureValidationError("x-jws-signature crit must be a string array")
    critical_headers = cast("list[str]", crit_header)
    _validate_b64_profile(protected_header, critical_headers)
    missing_critical_headers = sorted(_REQUIRED_OPEN_BANKING_CRITICAL_HEADERS - set(critical_headers))
    if missing_critical_headers:
        raise ResponseSignatureValidationError(
            "x-jws-signature crit must include: " + ", ".join(missing_critical_headers)
        )
    issued_at = protected_header.get(_OPEN_BANKING_IAT)
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise ResponseSignatureValidationError(f"x-jws-signature {_OPEN_BANKING_IAT} must be a JSON integer")
    issuer = _required_header_string(protected_header, _OPEN_BANKING_ISS)
    trust_anchor = _required_header_string(protected_header, _OPEN_BANKING_TAN)
    return ResponseSignatureValidation(key_id=key_id, issuer=issuer, trust_anchor=trust_anchor)


def _validate_b64_profile(protected_header: Mapping[str, JsonValue], crit_header: list[str]) -> None:
    """Validate old and v3.1.4+/v4 Open Banking detached-JWS payload profiles.

    Args:
        protected_header: Decoded protected header object from the detached JWS.
        crit_header: Parsed critical-header list from ``protected_header``.

    Raises:
        ResponseSignatureValidationError: If the ``b64`` header and critical
            header list mix incompatible old and v3.1.4+/v4 profiles.
    """
    b64_header = protected_header.get("b64")
    if b64_header is False:
        if "b64" not in crit_header:
            raise ResponseSignatureValidationError("x-jws-signature crit must include: b64")
        return
    if b64_header is None:
        if "b64" in crit_header:
            raise ResponseSignatureValidationError("x-jws-signature crit must not include b64 unless b64 is false")
        return
    raise ResponseSignatureValidationError("x-jws-signature b64 must be false when supplied")


def _required_header_string(protected_header: Mapping[str, JsonValue], key: str) -> str:
    """Return a required non-empty protected-header string.

    Args:
        protected_header: Decoded protected header object.
        key: Header key to extract.

    Returns:
        Non-empty header string.

    Raises:
        ResponseSignatureValidationError: If the header is absent or not a
            non-empty string.
    """
    value = protected_header.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResponseSignatureValidationError(f"x-jws-signature {key} must be a non-empty string")
    return value.strip()


def _select_verification_key(jwks: Mapping[str, JsonValue], key_id: str) -> jwk.Key:
    """Return the JWKS verification key whose ``kid`` matches ``key_id``.

    Args:
        jwks: JWKS document.
        key_id: Protected-header key id to locate.

    Returns:
        Imported public JWK suitable for JWS verification.

    Raises:
        ResponseSignatureValidationError: If the JWKS is malformed, no matching
            key exists, or the matching key cannot be imported.
    """
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise ResponseSignatureValidationError("JWKS response must contain a keys array")
    for raw_key in keys:
        if not isinstance(raw_key, dict) or raw_key.get("kid") != key_id:
            continue
        return _import_verification_key(cast(Mapping[str, JsonValue], raw_key), key_id)
    raise ResponseSignatureValidationError(f"JWKS does not contain a key for kid {key_id}")


def _import_verification_key(raw_key: Mapping[str, JsonValue], key_id: str) -> jwk.Key:
    """Import a JWKS entry as a public RSA key.

    Args:
        raw_key: Matching JWKS key object.
        key_id: Key id used for diagnostics.

    Returns:
        Imported RSA public key accepted by :mod:`joserfc`.

    Raises:
        ResponseSignatureValidationError: If the key cannot be imported from
            either JWK parameters or an ``x5c`` certificate chain.
    """
    try:
        return jwk.import_key(_string_key_parameters(raw_key), key_type="RSA")
    except ValueError, TypeError:
        x5c = raw_key.get("x5c")
        if not isinstance(x5c, list) or not x5c or not isinstance(x5c[0], str):
            raise ResponseSignatureValidationError(f"JWKS key {key_id} is not an importable RSA public key") from None
        try:
            return jwk.import_key(_x5c_certificate_pem(x5c[0]), key_type="RSA")
        except (ValueError, TypeError) as error:
            raise ResponseSignatureValidationError(f"JWKS x5c certificate for kid {key_id} is invalid") from error


def _string_key_parameters(raw_key: Mapping[str, JsonValue]) -> dict[str, str | list[str]]:
    """Return JWK parameters containing only strings and string arrays.

    Args:
        raw_key: Raw JWKS key entry.

    Returns:
        JWK dictionary accepted by :func:`joserfc.jwk.import_key`.
    """
    parameters: dict[str, str | list[str]] = {}
    for key, value in raw_key.items():
        if isinstance(value, str):
            parameters[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            parameters[key] = cast("list[str]", value)
    return parameters


def _x5c_certificate_pem(encoded_certificate: str) -> bytes:
    """Return PEM bytes for one base64 DER ``x5c`` certificate.

    Args:
        encoded_certificate: Base64 DER certificate value from a JWK ``x5c``
            array.

    Returns:
        PEM certificate bytes.
    """
    wrapped = "\n".join(textwrap.wrap(encoded_certificate.strip(), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n".encode("ascii")


def _base64url_decode(value: str) -> bytes:
    """Decode an unpadded base64url string.

    Args:
        value: Base64url text from a compact JWS segment.

    Returns:
        Decoded bytes.

    Raises:
        ValueError: If the segment cannot be decoded.
    """
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))
