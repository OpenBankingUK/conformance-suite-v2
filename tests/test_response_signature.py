"""Tests for Open Banking detached response JWS validation."""

from __future__ import annotations

import base64
import json
from typing import cast

import pytest
from joserfc import jwk, jws
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter

from conformance.json_types import JsonObject
from conformance.response_signature import ResponseSignatureValidationError, validate_ob_response_signature

_OPEN_BANKING_IAT = "http://openbanking.org.uk/iat"
"""Open Banking detached JWS issued-at protected header name."""

_OPEN_BANKING_ISS = "http://openbanking.org.uk/iss"
"""Open Banking detached JWS issuer protected header name."""

_OPEN_BANKING_TAN = "http://openbanking.org.uk/tan"
"""Open Banking detached JWS trust-anchor protected header name."""


def _registry() -> JWSRegistry:
    """Return a test JWS registry that accepts Open Banking headers.

    Returns:
        Registry with PS256 and Open Banking protected headers enabled.
    """
    headers = {
        **JWSRegistry.default_header_registry,
        _OPEN_BANKING_IAT: HeaderParameter("Open Banking issued-at header", "int"),
        _OPEN_BANKING_ISS: HeaderParameter("Open Banking issuer header", "str"),
        _OPEN_BANKING_TAN: HeaderParameter("Open Banking trust-anchor header", "str"),
    }
    return JWSRegistry(header_registry=headers, algorithms=["PS256"])


def _signed_response(payload: bytes) -> tuple[str, JsonObject]:
    """Return a detached response signature and matching JWKS.

    Args:
        payload: Response bytes to sign.

    Returns:
        Tuple of detached compact JWS and public JWKS.
    """
    signing_key = jwk.generate_key("RSA", 2048, private=True, auto_kid=False)
    public_key = signing_key.as_dict(is_private=False)
    public_key["kid"] = "response-key"
    protected = {
        "alg": "PS256",
        "kid": "response-key",
        "b64": False,
        "crit": ["b64", _OPEN_BANKING_IAT, _OPEN_BANKING_ISS, _OPEN_BANKING_TAN],
        _OPEN_BANKING_IAT: 1_774_120_000,
        _OPEN_BANKING_ISS: "0015800001041RHAAY",
        _OPEN_BANKING_TAN: "openbanking.org.uk",
    }
    compact_jws = jws.serialize_compact(protected, payload, signing_key, algorithms=["PS256"], registry=_registry())
    return jws.detach_content(compact_jws), cast(JsonObject, {"keys": [public_key]})


def _detached_signature_with_header(protected_header: JsonObject) -> str:
    """Build a detached compact JWS shell with a caller-supplied header.

    Args:
        protected_header: Protected JWS header to encode into the first compact
            JWS segment.

    Returns:
        Detached compact JWS string suitable for header-validation tests.
    """
    protected_bytes = json.dumps(protected_header, separators=(",", ":")).encode("utf-8")
    protected_segment = base64.urlsafe_b64encode(protected_bytes).rstrip(b"=").decode("ascii")
    return f"{protected_segment}..signature"


@pytest.mark.unit
def test_validate_ob_response_signature_accepts_matching_jwks_key() -> None:
    """A detached response JWS validates against the matching JWKS key."""
    payload = b'{"Data":{"Status":"AcceptedSettlementInProcess"}}'
    signature, jwks_document = _signed_response(payload)

    result = validate_ob_response_signature(signature=signature, payload=payload, jwks=jwks_document)

    assert result.to_json_object() == {
        "kid": "response-key",
        "issuer": "0015800001041RHAAY",
        "trustAnchor": "openbanking.org.uk",
    }


@pytest.mark.unit
def test_validate_ob_response_signature_rejects_tampered_payload() -> None:
    """A detached response JWS fails when the payload bytes differ."""
    signature, jwks_document = _signed_response(b'{"Data":{"Status":"AcceptedSettlementInProcess"}}')

    with pytest.raises(ResponseSignatureValidationError, match="failed PS256 verification"):
        validate_ob_response_signature(
            signature=signature,
            payload=b'{"Data":{"Status":"Rejected"}}',
            jwks=jwks_document,
        )


@pytest.mark.unit
def test_validate_ob_response_signature_requires_matching_kid() -> None:
    """A detached response JWS fails when JWKS contains no matching key id."""
    payload = b'{"Data":{"Status":"AcceptedSettlementInProcess"}}'
    signature, _jwks_document = _signed_response(payload)

    with pytest.raises(ResponseSignatureValidationError, match="does not contain a key"):
        validate_ob_response_signature(signature=signature, payload=payload, jwks={"keys": []})


@pytest.mark.unit
def test_validate_ob_response_signature_requires_unencoded_payload_header() -> None:
    """Open Banking response signatures must declare RFC 7797 unencoded payloads."""
    signature = _detached_signature_with_header(
        {
            "alg": "PS256",
            "kid": "response-key",
            "crit": ["b64", _OPEN_BANKING_IAT, _OPEN_BANKING_ISS, _OPEN_BANKING_TAN],
            _OPEN_BANKING_IAT: 1_774_120_000,
            _OPEN_BANKING_ISS: "0015800001041RHAAY",
            _OPEN_BANKING_TAN: "openbanking.org.uk",
        }
    )

    with pytest.raises(ResponseSignatureValidationError, match="b64 must be false"):
        validate_ob_response_signature(signature=signature, payload=b"{}", jwks={"keys": []})


@pytest.mark.unit
def test_validate_ob_response_signature_requires_critical_ob_headers() -> None:
    """Critical headers must include b64 and Open Banking signature parameters."""
    signature = _detached_signature_with_header(
        {
            "alg": "PS256",
            "kid": "response-key",
            "b64": False,
            "crit": ["b64"],
            _OPEN_BANKING_IAT: 1_774_120_000,
            _OPEN_BANKING_ISS: "0015800001041RHAAY",
            _OPEN_BANKING_TAN: "openbanking.org.uk",
        }
    )

    with pytest.raises(ResponseSignatureValidationError, match="crit must include:"):
        validate_ob_response_signature(signature=signature, payload=b"{}", jwks={"keys": []})
