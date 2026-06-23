"""Unit tests for consumed ``${testValues.<key>}`` placeholder key tracking."""

from __future__ import annotations

from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import ManifestStep, PsuAuthorizationStep, TestValueReference, parse_manifest
from conformance.test_plan import TestPlan


def _profiles_with_keys(keys: list[str]) -> dict[str, JsonValue]:
    """Build a minimal ``testValueProfiles`` object declaring ``keys``.

    Args:
        keys: Test-value keys to declare in the default profile.

    Returns:
        JSON object suitable for a manifest ``testValueProfiles`` field.
    """
    values: dict[str, JsonValue] = {key: f"{key}-value" for key in keys}
    return {
        "defaultProfileId": "default",
        "profiles": [{"id": "default", "label": "Default", "values": values}],
    }


def _single_step_manifest(step: dict[str, JsonValue], *, test_value_keys: list[str]) -> dict[str, JsonValue]:
    """Build a one-step v1 manifest with declared test-value keys.

    Args:
        step: Raw step object to include in ``steps``.
        test_value_keys: Keys declared in ``testValueProfiles``.

    Returns:
        Minimal manifest dict consumable by ``parse_manifest``.
    """
    manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "Consumed Keys",
        "steps": [step],
    }
    if test_value_keys:
        manifest["testValueProfiles"] = _profiles_with_keys(test_value_keys)
    return manifest


@pytest.mark.unit
def test_manifest_step_without_test_values_placeholders_has_empty_consumed_keys() -> None:
    """HTTP steps with no test-value placeholders report an empty consumed-key set."""
    raw_manifest = _single_step_manifest(
        {
            "id": "step-1",
            "name": "No placeholders",
            "request": {"method": "GET", "url": "https://example.com/accounts"},
            "assertions": [{"type": "http_status", "expected": 200}],
        },
        test_value_keys=[],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(ManifestStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset()


@pytest.mark.unit
def test_manifest_step_collects_key_from_url_placeholder() -> None:
    """HTTP step URL placeholders contribute consumed test-value keys."""
    raw_manifest = _single_step_manifest(
        {
            "id": "step-1",
            "name": "URL placeholder",
            "request": {"method": "GET", "url": "https://example.com/accounts/${testValues.accountId}"},
            "assertions": [{"type": "http_status", "expected": 200}],
        },
        test_value_keys=["accountId"],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(ManifestStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset({"accountId"})


@pytest.mark.unit
def test_manifest_step_collects_keys_across_url_headers_and_json_body() -> None:
    """HTTP step combines consumed keys found in URL, headers, and JSON body leaves."""
    raw_manifest = _single_step_manifest(
        {
            "id": "step-1",
            "name": "Multi field placeholders",
            "request": {
                "method": "POST",
                "url": "https://example.com/${testValues.accountId}",
                "headers": {"x-correlation-id": "${testValues.correlationId}"},
                "body": {
                    "root": "literal",
                    "nested": {"leaf": "${testValues.paymentId}"},
                    "items": ["${testValues.itemId}", 3],
                },
            },
            "assertions": [{"type": "http_status", "expected": 200}],
        },
        test_value_keys=["accountId", "correlationId", "paymentId", "itemId"],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(ManifestStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset({"accountId", "correlationId", "paymentId", "itemId"})
    assert step.test_value_references == (
        TestValueReference(key="itemId", request_area="request-json-body", field_path="request.body.items[0]"),
        TestValueReference(key="paymentId", request_area="request-json-body", field_path="request.body.nested.leaf"),
        TestValueReference(
            key="correlationId",
            request_area="request-header",
            field_path="request.headers.x-correlation-id",
        ),
        TestValueReference(key="accountId", request_area="request-url", field_path="request.url"),
    )


@pytest.mark.unit
def test_manifest_step_scans_form_body_values() -> None:
    """Form body field values are included in consumed-key extraction."""
    raw_manifest = _single_step_manifest(
        {
            "id": "step-1",
            "name": "Form body placeholders",
            "request": {
                "method": "POST",
                "url": "https://example.com/token",
                "body": {
                    "encoding": "form",
                    "fields": {
                        "grant_type": "client_credentials",
                        "scope": "${testValues.scope}",
                        "resource": "${testValues.resource}",
                    },
                },
            },
            "assertions": [{"type": "http_status", "expected": 200}],
        },
        test_value_keys=["scope", "resource"],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(ManifestStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset({"scope", "resource"})


@pytest.mark.unit
def test_psu_authorization_step_scans_string_fields_and_string_request_object() -> None:
    """PSU auth steps collect consumed keys from supported string fields."""
    raw_manifest = _single_step_manifest(
        {
            "kind": "psu-authorization",
            "id": "psu-1",
            "name": "PSU auth",
            "mode": "manual",
            "authorizationEndpoint": "${testValues.authorizationEndpoint}",
            "clientId": "${testValues.clientId}",
            "redirectUri": "${config.oauth.redirectUri}",
            "scope": "openid",
            "state": "${testValues.state}",
            "requestObject": "${testValues.requestJwt}",
        },
        test_value_keys=["authorizationEndpoint", "clientId", "state", "requestJwt"],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(PsuAuthorizationStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset({"authorizationEndpoint", "clientId", "state", "requestJwt"})


@pytest.mark.unit
def test_psu_authorization_step_scans_generated_request_object_fields() -> None:
    """PSU auth generated request objects contribute audience and intent-id keys."""
    raw_manifest = _single_step_manifest(
        {
            "kind": "psu-authorization",
            "id": "psu-1",
            "name": "PSU auth generated request object",
            "mode": "manual",
            "authorizationEndpoint": "https://aspsp.example.com/authorize",
            "clientId": "client-123",
            "redirectUri": "https://client.example.com/callback",
            "requestObject": {
                "source": "fapi-signing",
                "audience": "${testValues.audience}",
                "openbankingIntentId": "${testValues.intentId}",
            },
        },
        test_value_keys=["audience", "intentId"],
    )
    manifest = parse_manifest(raw_manifest)
    step = cast(PsuAuthorizationStep, manifest.steps[0])

    assert step.consumed_test_value_keys == frozenset({"audience", "intentId"})
    assert step.test_value_references == (
        TestValueReference(key="audience", request_area="psu-request-object", field_path="requestObject.audience"),
        TestValueReference(
            key="intentId",
            request_area="psu-request-object",
            field_path="requestObject.openbankingIntentId",
        ),
    )


@pytest.mark.unit
def test_test_plan_entry_carries_step_consumed_keys() -> None:
    """Default plan entries copy consumed-key metadata from parsed steps."""
    raw_manifest = _single_step_manifest(
        {
            "id": "step-1",
            "name": "URL placeholder",
            "request": {"method": "GET", "url": "https://example.com/${testValues.accountId}"},
            "assertions": [{"type": "http_status", "expected": 200}],
        },
        test_value_keys=["accountId"],
    )
    manifest = parse_manifest(raw_manifest)
    plan = TestPlan.default_plan_from_manifest(manifest)

    assert plan.entries[0].consumed_test_value_keys == frozenset({"accountId"})
