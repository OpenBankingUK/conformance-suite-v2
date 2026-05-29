"""Sensitive-data masking for request/response evidence in result files.

The PRD requires that sensitive fields (tokens, keys, credentials) be
masked by default in the structured result file so participants can share
reports with OBL without exposing live credentials. Unmasking is reserved
for a future developer-mode toggle and must never be enabled in release
builds.

This module is intentionally domain-agnostic and operates on a fixed,
case-insensitive set of well-known credential keys and HTTP headers that
appear in FAPI/OAuth 2.0 message exchanges. Open Banking domain-specific
masking (e.g. account numbers, sort codes) is a separate concern and is
not implemented here.

Masked values are replaced with the literal ``"***"`` — original length is
not preserved to avoid leaking entropy about the underlying secret.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from conformance.json_types import JsonObject, JsonValue

MASKED_VALUE: Final[str] = "***"
"""Literal placeholder written in place of any masked value."""

SENSITIVE_JSON_KEYS: Final[frozenset[str]] = frozenset(
    {
        # OAuth 2.0 / OIDC token & credential fields
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "code",
        "client_assertion",
        "assertion",
        # Generic credential fields that may appear in form/JSON payloads
        "password",
        "private_key",
    }
)
"""JSON object keys whose values must be masked, compared case-insensitively."""

SENSITIVE_HEADER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-fapi-financial-id",
    }
)
"""HTTP header names whose values must be masked, compared case-insensitively.

``x-fapi-financial-id`` is included because in some ASPSP deployments it
acts as a tenant identifier that should not appear in shared reports.
"""


def mask_json_value(value: JsonValue) -> JsonValue:
    """Return a deep-copied JSON value with sensitive keys masked.

    Recursively walks objects and arrays. Any object key matching
    :data:`SENSITIVE_JSON_KEYS` (case-insensitive) has its value replaced
    with :data:`MASKED_VALUE`. Non-object, non-array values are returned
    unchanged.

    Args:
        value: Arbitrary JSON value (scalar, list, or object).

    Returns:
        A new JSON value safe to embed in shared result files.
    """
    if isinstance(value, dict):
        return _mask_object(value)
    if isinstance(value, list):
        return [mask_json_value(item) for item in value]
    return value


def _mask_object(obj: Mapping[str, JsonValue]) -> JsonObject:
    """Mask sensitive keys in a JSON object, recursing into nested values.

    Args:
        obj: JSON object whose keys may match :data:`SENSITIVE_JSON_KEYS`.

    Returns:
        New object with sensitive values replaced and other values deep-masked.
    """
    masked: JsonObject = {}
    for key, value in obj.items():
        if key.lower() in SENSITIVE_JSON_KEYS:
            masked[key] = MASKED_VALUE
        else:
            masked[key] = mask_json_value(value)
    return masked


def mask_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a new header map with sensitive header values masked.

    Args:
        headers: HTTP header name → value mapping.

    Returns:
        New mapping with values for headers in :data:`SENSITIVE_HEADER_NAMES`
        replaced by :data:`MASKED_VALUE`. Header names are preserved verbatim
        (original casing).
    """
    return {
        name: (MASKED_VALUE if name.lower() in SENSITIVE_HEADER_NAMES else value) for name, value in headers.items()
    }


def mask_form_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """Return a new form-field map with sensitive field values masked.

    OAuth 2.0 token exchanges send credentials as ``application/x-www-form-
    urlencoded`` fields (e.g. ``client_secret``, ``code``), so the same key
    list used for JSON bodies applies.

    Args:
        fields: Form field name → value mapping.

    Returns:
        New mapping with values for fields in :data:`SENSITIVE_JSON_KEYS`
        replaced by :data:`MASKED_VALUE`.
    """
    return {name: (MASKED_VALUE if name.lower() in SENSITIVE_JSON_KEYS else value) for name, value in fields.items()}
