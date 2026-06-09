"""Dependency proof tests for PS256 JOSE support.

These tests keep chunk A constrained to dependency selection and proof of
capability for upcoming FAPI 1 Advanced request-object and client-assertion
signing work.
"""

import json
from base64 import urlsafe_b64decode

import pytest
from joserfc import jwk, jwt


def _decode_segment(segment: bytes) -> dict[str, object]:
    """Decode one compact JWS segment into a JSON object.

    Args:
        segment: Base64url-encoded JWS segment bytes.

    Returns:
        Parsed JSON object from the decoded segment.
    """
    padded_segment = segment + b"=" * (-len(segment) % 4)
    decoded_segment = urlsafe_b64decode(padded_segment)
    raw_object = json.loads(decoded_segment)
    assert isinstance(raw_object, dict)
    return raw_object


@pytest.mark.unit
def test_joserfc_ps256_signs_and_verifies_compact_jwt() -> None:
    key = jwk.generate_key("RSA", 2048, auto_kid=True)
    claims = {
        "iss": "client-id",
        "sub": "client-id",
        "aud": "https://as.example.com/token",
        "exp": 1_900_000_000,
        "iat": 1_899_999_940,
        "jti": "proof-token-id",
    }

    public_key_data = key.as_dict(private=False)
    public_key = jwk.import_key(public_key_data)
    token = jwt.encode({"alg": "PS256", "kid": public_key_data["kid"]}, claims, key, algorithms=["PS256"])
    decoded_token = jwt.decode(token, public_key, algorithms=["PS256"])

    token_bytes = token.encode("utf-8")
    header_segment, payload_segment, signature_segment = token_bytes.split(b".")
    header = _decode_segment(header_segment)
    payload = _decode_segment(payload_segment)

    assert header["alg"] == "PS256"
    assert header["kid"] == public_key_data["kid"]
    assert signature_segment
    assert payload == claims
    assert decoded_token.claims == claims
