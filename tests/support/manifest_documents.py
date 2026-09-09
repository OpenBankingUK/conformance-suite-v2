"""Raw manifest documents shared by the manifest parser test modules.

The parser is the trust boundary for participant-supplied conformance
manifests, so the tests work from complete, valid documents and vary one
field at a time. Building those documents in one place keeps the acceptance
and rejection matrices focused on the field under test.
"""

from __future__ import annotations

from typing import cast

from conformance.json_types import JsonValue

HTTP_STATUS_OK_ASSERTION: JsonValue = {"type": "http_status", "expected": 200}
"""The single assertion used by documents whose subject is the request shape."""


def v0_manifest() -> dict[str, JsonValue]:
    """Build the valid v0 discovery/JWKS manifest document.

    Returns:
        A complete v0 manifest that ``parse_manifest`` accepts unchanged.
    """
    return {
        "schemaVersion": "v0",
        "name": "Ozone OpenID discovery and JWKS smoke check",
        "tests": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "issuer", "rule": "https_url"},
                    {"type": "json_field", "path": "jwks_uri", "rule": "https_url"},
                ],
                "followUp": {
                    "type": "jwks",
                    "urlSource": "response.body.jwks_uri",
                    "request": {"method": "GET"},
                    "assertions": [
                        {"type": "http_status", "expected": 200},
                        {"type": "json_field", "path": "keys", "rule": "array"},
                    ],
                },
            }
        ],
    }


def v0_first_test(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the first test entry of a v0 manifest document.

    Args:
        raw_manifest: Raw v0 manifest document.

    Returns:
        The mutable first ``tests`` entry.
    """
    tests = cast("list[JsonValue]", raw_manifest["tests"])
    return cast("dict[str, JsonValue]", tests[0])


def v0_request_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the request document of the first v0 test.

    Args:
        raw_manifest: Raw v0 manifest document.

    Returns:
        The mutable ``request`` document.
    """
    return cast("dict[str, JsonValue]", v0_first_test(raw_manifest)["request"])


def v0_assertion_configs(raw_manifest: dict[str, JsonValue]) -> list[JsonValue]:
    """Return the assertion list of the first v0 test.

    Args:
        raw_manifest: Raw v0 manifest document.

    Returns:
        The mutable ``assertions`` list.
    """
    return cast("list[JsonValue]", v0_first_test(raw_manifest)["assertions"])


def v0_follow_up_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the follow-up document of the first v0 test.

    Args:
        raw_manifest: Raw v0 manifest document.

    Returns:
        The mutable ``followUp`` document.
    """
    return cast("dict[str, JsonValue]", v0_first_test(raw_manifest)["followUp"])


def v1_manifest() -> dict[str, JsonValue]:
    """Build the valid two-step v1 discovery/JWKS manifest document.

    The second step consumes a ``${steps...}`` placeholder from the first, so
    this document also carries the minimal step-dependency shape.

    Returns:
        A complete v1 manifest that ``parse_manifest`` accepts unchanged.
    """
    return {
        "schemaVersion": "v1",
        "name": "Ozone OpenID discovery and JWKS (v1)",
        "steps": [
            {
                "id": "openid-discovery",
                "name": "OpenID discovery document",
                "request": {
                    "method": "GET",
                    "url": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "jwks_uri", "rule": "https_url"},
                ],
            },
            {
                "id": "jwks-fetch",
                "name": "JWKS endpoint",
                "request": {
                    "method": "GET",
                    "url": "${steps.openid-discovery.response.body.jwks_uri}",
                },
                "assertions": [
                    {"type": "http_status", "expected": 200},
                    {"type": "json_field", "path": "keys", "rule": "array"},
                ],
            },
        ],
    }


def v1_single_step_manifest(
    *,
    request: dict[str, JsonValue],
    assertions: JsonValue | None = None,
    **step_fields: JsonValue,
) -> dict[str, JsonValue]:
    """Build a one-step v1 manifest around the supplied request document.

    Every parse error for this document is reported against ``steps[0]``, so
    the matrices can state the expected message without restating the
    surrounding document.

    Args:
        request: Raw ``request`` document for the single step.
        assertions: Optional assertion list. Defaults to a 200 status check.
        **step_fields: Extra raw step fields (for example ``mandatory`` or
            ``tokenEndpointAuthPolicy``) merged into the step document.

    Returns:
        A one-step v1 manifest document.
    """
    step: dict[str, JsonValue] = {
        "id": "step-a",
        "name": "Step A",
        "request": request,
        "assertions": [HTTP_STATUS_OK_ASSERTION] if assertions is None else assertions,
    }
    step.update(step_fields)
    return {"schemaVersion": "v1", "name": "Single step", "steps": [step]}


def v1_two_step_manifest(
    *,
    first_request: dict[str, JsonValue],
    second_request: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Build a two-step v1 manifest with ids ``step-a`` and ``step-b``.

    Args:
        first_request: Raw ``request`` document for ``step-a``.
        second_request: Raw ``request`` document for ``step-b``.

    Returns:
        A two-step v1 manifest document.
    """
    return {
        "schemaVersion": "v1",
        "name": "Two steps",
        "steps": [
            {
                "id": "step-a",
                "name": "Step A",
                "request": first_request,
                "assertions": [HTTP_STATUS_OK_ASSERTION],
            },
            {
                "id": "step-b",
                "name": "Step B",
                "request": second_request,
                "assertions": [HTTP_STATUS_OK_ASSERTION],
            },
        ],
    }


def psu_step() -> dict[str, JsonValue]:
    """Build a minimally-valid raw PSU authorisation step entry.

    Returns:
        Raw ``psu-authorization`` step document.
    """
    return {
        "kind": "psu-authorization",
        "id": "psu",
        "name": "PSU authorisation",
        "mode": "manual",
        "authorizationEndpoint": "https://auth.example.com/authorize",
        "clientId": "synthetic-client-id-00000000",
        "redirectUri": "https://conformance.example.com/callback",
    }


def psu_manifest() -> dict[str, JsonValue]:
    """Build a minimally-valid v1 manifest containing a single PSU step.

    Returns:
        Raw v1 manifest document whose only step is a PSU authorisation step.
    """
    return {
        "schemaVersion": "v1",
        "name": "PSU authorisation only",
        "steps": [psu_step()],
    }


def psu_manifest_with(**step_fields: JsonValue) -> dict[str, JsonValue]:
    """Build a single-PSU-step manifest with extra or overridden step fields.

    Args:
        **step_fields: Raw PSU step fields merged over the valid defaults.

    Returns:
        Raw v1 manifest document whose only step carries ``step_fields``.
    """
    document = psu_manifest()
    cast("list[dict[str, JsonValue]]", document["steps"])[0].update(step_fields)
    return document
