"""Unit tests for sensitive-data masking helpers."""

from __future__ import annotations

import pytest

from conformance.json_types import JsonObject
from conformance.masking import (
    MASKED_VALUE,
    SENSITIVE_HEADER_NAMES,
    SENSITIVE_JSON_KEYS,
    mask_form_fields,
    mask_headers,
    mask_json_value,
)


@pytest.mark.unit
class TestMaskJsonValue:
    """Behaviour of :func:`mask_json_value` for typical FAPI/OAuth payloads."""

    def test_scalars_pass_through_unchanged(self) -> None:
        """Strings, numbers, booleans, and None are returned unchanged."""
        assert mask_json_value("hello") == "hello"
        assert mask_json_value(42) == 42
        assert mask_json_value(True) is True
        assert mask_json_value(None) is None

    def test_token_response_masks_known_credential_keys(self) -> None:
        """A representative token-endpoint response has all token fields masked."""
        body: JsonObject = {
            "access_token": "eyJraWQ...",
            "refresh_token": "abc.def.ghi",
            "id_token": "eyJ0eXAi...",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "accounts payments",
        }
        masked = mask_json_value(body)
        assert masked == {
            "access_token": MASKED_VALUE,
            "refresh_token": MASKED_VALUE,
            "id_token": MASKED_VALUE,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "accounts payments",
        }

    def test_sensitive_key_match_is_case_insensitive(self) -> None:
        """Mixed-case key names still trigger masking."""
        masked = mask_json_value({"Access_Token": "secret", "CLIENT_SECRET": "shh"})
        assert masked == {"Access_Token": MASKED_VALUE, "CLIENT_SECRET": MASKED_VALUE}

    def test_nested_objects_are_recursively_masked(self) -> None:
        """Credential keys inside nested objects are masked too."""
        body: JsonObject = {
            "data": {
                "credentials": {"client_secret": "shh", "client_id": "abc"},
                "items": [1, 2],
            }
        }
        masked = mask_json_value(body)
        assert masked == {
            "data": {
                "credentials": {"client_secret": MASKED_VALUE, "client_id": "abc"},
                "items": [1, 2],
            }
        }

    def test_arrays_of_objects_are_masked_per_element(self) -> None:
        """Each object element in an array is independently masked."""
        body: JsonObject = {"tokens": [{"access_token": "a"}, {"access_token": "b", "name": "n"}]}
        masked = mask_json_value(body)
        assert masked == {"tokens": [{"access_token": MASKED_VALUE}, {"access_token": MASKED_VALUE, "name": "n"}]}

    def test_input_object_is_not_mutated(self) -> None:
        """Masking returns a new object — the caller's input is preserved."""
        body: dict[str, object] = {"access_token": "original", "nested": {"client_secret": "x"}}
        mask_json_value(body)  # type: ignore[arg-type]
        assert body == {"access_token": "original", "nested": {"client_secret": "x"}}

    def test_masked_value_length_is_constant(self) -> None:
        """The replacement does not preserve original length — no entropy leak."""
        short = mask_json_value({"access_token": "a"})
        long = mask_json_value({"access_token": "a" * 10_000})
        assert short == long == {"access_token": MASKED_VALUE}


@pytest.mark.unit
class TestMaskHeaders:
    """Behaviour of :func:`mask_headers` for HTTP request/response headers."""

    def test_authorization_header_is_masked(self) -> None:
        """Bearer tokens in the ``Authorization`` header are replaced."""
        masked = mask_headers({"Authorization": "Bearer eyJ...", "Accept": "application/json"})
        assert masked == {"Authorization": MASKED_VALUE, "Accept": "application/json"}

    def test_header_name_match_is_case_insensitive(self) -> None:
        """``authorization`` and ``AUTHORIZATION`` are both treated as sensitive."""
        masked = mask_headers({"authorization": "x", "AUTHORIZATION": "y"})
        assert masked == {"authorization": MASKED_VALUE, "AUTHORIZATION": MASKED_VALUE}

    def test_original_header_casing_is_preserved(self) -> None:
        """Header name casing is unchanged; only the value is replaced."""
        masked = mask_headers({"X-Api-Key": "k"})
        assert "X-Api-Key" in masked
        assert masked["X-Api-Key"] == MASKED_VALUE

    def test_non_sensitive_headers_unchanged(self) -> None:
        """Headers not in the sensitive set are preserved verbatim."""
        masked = mask_headers({"Content-Type": "application/json", "Accept": "*/*"})
        assert masked == {"Content-Type": "application/json", "Accept": "*/*"}


@pytest.mark.unit
class TestMaskFormFields:
    """Behaviour of :func:`mask_form_fields` for OAuth 2.0 form bodies."""

    def test_token_exchange_form_body_masks_credentials(self) -> None:
        """A token-exchange form body has its secret fields masked."""
        fields = {
            "grant_type": "authorization_code",
            "code": "abc123",
            "client_id": "test-client",
            "client_secret": "shh",
            "redirect_uri": "https://example/cb",
        }
        masked = mask_form_fields(fields)
        assert masked == {
            "grant_type": "authorization_code",
            "code": MASKED_VALUE,
            "client_id": "test-client",
            "client_secret": MASKED_VALUE,
            "redirect_uri": "https://example/cb",
        }

    def test_field_name_match_is_case_insensitive(self) -> None:
        """Field name comparison ignores case."""
        masked = mask_form_fields({"CLIENT_SECRET": "x"})
        assert masked == {"CLIENT_SECRET": MASKED_VALUE}


@pytest.mark.unit
class TestSensitiveKeySets:
    """Sanity checks on the published sensitive-key constants."""

    def test_json_key_set_is_lowercase(self) -> None:
        """Sensitive JSON keys are stored lowercase for case-insensitive lookup."""
        assert all(key == key.lower() for key in SENSITIVE_JSON_KEYS)

    def test_header_name_set_is_lowercase(self) -> None:
        """Sensitive header names are stored lowercase for case-insensitive lookup."""
        assert all(name == name.lower() for name in SENSITIVE_HEADER_NAMES)
