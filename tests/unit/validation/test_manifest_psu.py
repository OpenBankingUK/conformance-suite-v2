"""Parsing of PSU authorisation steps, which drive the OAuth 2.0 redirect handoff."""

import json
import re
from pathlib import Path
from typing import NamedTuple, cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    FormBody,
    GeneratedRequestObject,
    ManifestError,
    ManifestStep,
    PsuAuthorizationStep,
    TokenEndpointAuthPolicy,
    load_manifest,
    parse_manifest,
)
from tests.support.manifest_documents import psu_manifest, psu_manifest_with

pytestmark = pytest.mark.unit


def test_parse_v1_manifest_loads_psu_authorization_file(tmp_path: Path) -> None:
    """A v1 PSU manifest file parses and exposes the PSU step."""
    manifest_path = tmp_path / "psu-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "v1",
                "name": "PSU authorization flow",
                "steps": [
                    {
                        "id": "discovery",
                        "name": "Discovery",
                        "request": {"method": "GET", "url": "${config.discoveryUrl}"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                    {
                        "kind": "psu-authorization",
                        "id": "psu-authorization",
                        "name": "PSU authorization",
                        "mode": "manual",
                        "authorizationEndpoint": "${steps.discovery.response.body.authorization_endpoint}",
                        "clientId": "${config.oauth.clientId}",
                        "redirectUri": "${config.oauth.redirectUri}",
                        "requestObject": {
                            "source": "fapi-signing",
                            "audience": "${steps.discovery.response.body.issuer}",
                        },
                        "mandatory": True,
                    },
                    {
                        "id": "token-exchange",
                        "name": "Token exchange",
                        "request": {
                            "method": "POST",
                            "url": "${steps.discovery.response.body.token_endpoint}",
                            "body": {
                                "encoding": "form",
                                "fields": {
                                    "grant_type": "authorization_code",
                                    "code": "${steps.psu-authorization.response.body.code}",
                                    "client_id": "${config.oauth.clientId}",
                                    "redirect_uri": "${config.oauth.redirectUri}",
                                },
                            },
                        },
                        "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)
    assert manifest.schema_version == "v1"
    assert len(manifest.steps) == 3
    discovery_step = manifest.steps[0]
    psu_step = manifest.steps[1]
    token_step = manifest.steps[2]
    assert isinstance(discovery_step, ManifestStep)
    assert discovery_step.request.url == "${config.discoveryUrl}"
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.id == "psu-authorization"
    assert psu_step.mode == "manual"
    assert psu_step.client_id == "${config.oauth.clientId}"
    assert psu_step.redirect_uri == "${config.oauth.redirectUri}"
    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.discovery.response.body.issuer}",
    )
    assert psu_step.mandatory is True
    assert isinstance(token_step, ManifestStep)
    assert token_step.token_endpoint_auth_policy == TokenEndpointAuthPolicy(source="fapi-signing")
    assert isinstance(token_step.request.body, FormBody)
    assert dict(token_step.request.body.fields) == {
        "grant_type": "authorization_code",
        "code": "${steps.psu-authorization.response.body.code}",
        "client_id": "${config.oauth.clientId}",
        "redirect_uri": "${config.oauth.redirectUri}",
    }


def test_parse_v1_psu_step_applies_defaults() -> None:
    """Optional fields fall back to their documented defaults."""
    manifest = parse_manifest(psu_manifest())
    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.response_type == "code id_token"
    assert psu_step.scope == "openid"
    assert psu_step.state is None
    assert psu_step.nonce is None
    assert psu_step.request_object is None
    assert psu_step.mandatory is False
    assert psu_step.optional is False
    assert psu_step.group == "default"
    assert psu_step.phase == "execution"


def test_parse_v1_psu_step_accepts_all_fields() -> None:
    """Every optional PSU field round-trips when populated."""
    raw = psu_manifest()
    step = cast("list[dict[str, JsonValue]]", raw["steps"])[0]
    step["responseType"] = "code"
    step["scope"] = "openid accounts"
    step["state"] = "x" * 64
    step["nonce"] = "n" * 64
    step["requestObject"] = "eyJhbGciOiJQUzI1NiJ9.synthetic.signature"
    step["mandatory"] = True
    step["group"] = "consent"
    step["phase"] = "setup"
    manifest = parse_manifest(raw)
    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.response_type == "code"
    assert psu_step.scope == "openid accounts"
    assert psu_step.state == "x" * 64
    assert psu_step.nonce == "n" * 64
    assert psu_step.request_object == "eyJhbGciOiJQUzI1NiJ9.synthetic.signature"
    assert psu_step.mandatory is True
    assert psu_step.group == "consent"
    assert psu_step.phase == "setup"


def test_parse_v1_psu_step_accepts_generated_request_object_directive() -> None:
    """PSU steps may request a runtime-generated JAR request object."""
    raw = psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["requestObject"] = {
        "source": "fapi-signing",
        "audience": "https://auth.example.com",
    }

    manifest = parse_manifest(raw)

    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert isinstance(psu_step.request_object, GeneratedRequestObject)
    assert psu_step.request_object.source == "fapi-signing"
    assert psu_step.request_object.audience == "https://auth.example.com"


def test_parse_v1_psu_step_accepts_placeholder_in_authorization_endpoint() -> None:
    """Authorisation endpoint may be sourced from an earlier step's response."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "with-discovery",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {"method": "GET", "url": "https://auth.example.com/.well-known/openid-configuration"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "headless",
                "authorizationEndpoint": "${steps.discovery.response.body.authorization_endpoint}",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    psu_step = manifest.steps[1]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.mode == "headless"


def test_parse_v1_psu_step_accepts_config_redirect_uri_placeholder() -> None:
    """PSU redirectUri may use the narrow participant config placeholder."""
    raw = psu_manifest()
    cast("list[dict[str, JsonValue]]", raw["steps"])[0]["redirectUri"] = "${config.oauth.redirectUri}"

    manifest = parse_manifest(raw)

    psu_step = manifest.steps[0]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.redirect_uri == "${config.oauth.redirectUri}"


def test_parse_v1_psu_step_accepts_placeholder_state_below_min_length() -> None:
    """A placeholder-bearing state is exempt from the parse-time length check."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "with-state-placeholder",
        "steps": [
            {
                "id": "make-state",
                "name": "Make state",
                "request": {"method": "GET", "url": "https://example.com/state"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
                "state": "${steps.make-state.response.body.value}",
            },
        ],
    }
    manifest = parse_manifest(raw_manifest)
    psu_step = manifest.steps[1]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.state == "${steps.make-state.response.body.value}"


def test_parse_v1_psu_step_accepts_generated_request_object_openbanking_intent_id_placeholder() -> None:
    """Generated request objects may declare an intent-id placeholder for later runtime signing."""
    raw: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "request-object-intent-id",
        "steps": [
            {
                "id": "discovery",
                "name": "Discovery",
                "request": {
                    "method": "GET",
                    "url": "https://auth.example.com/.well-known/openid-configuration",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "id": "account-access-consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://rs.example.com/account-access-consents",
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
            {
                "kind": "psu-authorization",
                "id": "psu",
                "name": "PSU",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "client-id",
                "redirectUri": "https://conformance.example.com/callback",
                "requestObject": {
                    "source": "fapi-signing",
                    "audience": "${steps.discovery.response.body.issuer}",
                    "openbankingIntentId": "${steps.account-access-consent.response.body.Data.ConsentId}",
                },
            },
        ],
    }

    manifest = parse_manifest(raw)

    psu_step = manifest.steps[2]
    assert isinstance(psu_step, PsuAuthorizationStep)
    assert psu_step.request_object == GeneratedRequestObject(
        source="fapi-signing",
        audience="${steps.discovery.response.body.issuer}",
        openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
    )


@pytest.mark.parametrize(
    "missing_key",
    ["id", "name", "mode", "authorizationEndpoint", "clientId", "redirectUri"],
)
def test_parse_v1_psu_step_rejects_missing_required_field(missing_key: str) -> None:
    """Each required PSU step field fails fast when omitted."""
    raw = psu_manifest()
    step = cast("list[dict[str, JsonValue]]", raw["steps"])[0]
    del step[missing_key]
    with pytest.raises(ManifestError, match=re.escape(f"steps[0].{missing_key}")):
        parse_manifest(raw)


def test_parse_v1_psu_step_rejects_duplicate_id() -> None:
    """A PSU step id collision with an earlier step is rejected."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "dup-id",
        "steps": [
            {
                "id": "shared",
                "name": "First",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
            },
            {
                "kind": "psu-authorization",
                "id": "shared",
                "name": "PSU",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "c",
                "redirectUri": "https://conformance.example.com/callback",
            },
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[1\]\.id 'shared' is a duplicate"):
        parse_manifest(raw_manifest)


class PsuRejection(NamedTuple):
    """Malformed PSU authorisation step fields and the parse error they must raise.

    Attributes:
        step_fields: Raw PSU step fields merged over a valid PSU step.
        message: Regular expression the ``ManifestError`` message must match.
    """

    step_fields: dict[str, JsonValue]
    message: str


PSU_REJECTIONS = (
    pytest.param(
        PsuRejection(
            step_fields={"phase": "parallel"},
            message=r"steps\[0\]\.phase must be one of: setup, execution",
        ),
        id="invalid-phase",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"group": "bad.group"},
            message=r"steps\[0\]\.group 'bad\.group' contains invalid characters",
        ),
        id="invalid-group",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"request": {"method": "GET", "url": "https://example.com/x"}},
            message=r"Unknown steps\[0\] field\(s\): request",
        ),
        id="http-only-request-field",
    ),
    pytest.param(
        PsuRejection(step_fields={"mode": "auto"}, message=r"steps\[0\]\.mode must be one of"),
        id="unknown-mode",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"authorizationEndpoint": "http://auth.example.com/authorize"},
            message=r"steps\[0\]\.authorizationEndpoint",
        ),
        id="non-https-authorization-endpoint",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"redirectUri": "http://conformance.example.com/callback"},
            message=r"steps\[0\]\.redirectUri",
        ),
        id="non-https-redirect-uri",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"redirectUri": "${config.discoveryUrl}"},
            message=r"steps\[0\]\.redirectUri may only use",
        ),
        id="unsupported-redirect-uri-placeholder",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"responseType": "${steps.x.response.body.type}"},
            message=r"steps\[0\]\.responseType must not contain placeholders",
        ),
        id="placeholder-in-response-type",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"scope": "${steps.x.response.body.scope}"},
            message=r"steps\[0\]\.scope must not contain placeholders",
        ),
        id="placeholder-in-scope",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"state": "too-short"},
            message=r"steps\[0\]\.state must be at least 32 characters",
        ),
        id="short-literal-state",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"requestObject": "   "},
            message=r"steps\[0\]\.requestObject must be a non-empty string",
        ),
        id="blank-request-object",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"requestObject": {"source": "hand-rolled"}},
            message=r"steps\[0\]\.requestObject\.source must be 'fapi-signing'",
        ),
        id="unknown-generated-request-object-source",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"requestObject": {"source": "fapi-signing", "kid": "not-allowed-here"}},
            message=r"Unknown steps\[0\]\.requestObject field\(s\): kid",
        ),
        id="unknown-generated-request-object-key",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"requestObject": {"source": "fapi-signing", "openbankingIntentId": "   "}},
            message=r"steps\[0\]\.requestObject\.openbankingIntentId must be a non-empty string when present",
        ),
        id="blank-generated-request-object-intent-id",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"requestObject": {"source": "${config.oauth.clientSecret}"}},
            message=r"steps\[0\]\.requestObject\.source contains unsupported config placeholder",
        ),
        id="secret-bearing-placeholder-in-request-object-source",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"timeoutSeconds": 60},
            message=r"Unknown steps\[0\] field\(s\): timeoutSeconds",
        ),
        id="participant-configurable-timeout",
    ),
    pytest.param(
        PsuRejection(
            step_fields={"mandatory": True, "optional": True},
            message=r"steps\[0\]: 'mandatory' and 'optional' must not both be true",
        ),
        id="mandatory-and-optional-both-true",
    ),
    pytest.param(
        PsuRejection(step_fields={"nonsense": True}, message=r"Unknown steps\[0\] field\(s\): nonsense"),
        id="unknown-key",
    ),
)
"""PSU step documents the v1 parser rejects, with the message each must produce.

The PSU step drives the OAuth 2.0 / OIDC redirect handoff, so the closed-shape
rules here are what stop a manifest from widening the authorisation request,
weakening ``state`` entropy, or sourcing signing material from participant
secrets.
"""


@pytest.mark.parametrize("case", PSU_REJECTIONS)
def test_parse_v1_psu_step_rejects_invalid_field(case: PsuRejection) -> None:
    """Malformed PSU step fields fail parse with a field-specific message.

    Args:
        case: PSU step fields under test and the message they must produce.
    """
    with pytest.raises(ManifestError, match=case.message):
        parse_manifest(psu_manifest_with(**case.step_fields))
