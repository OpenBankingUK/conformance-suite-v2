import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from conformance.context import ExecutionContext, RuntimeConfig, build_runtime_test_values, resolve_placeholders
from conformance.json_types import JsonValue
from conformance.manifest import ManifestError, ManifestStep, PsuAuthorizationStep, parse_manifest
from conformance.model_bank_config import ConfigError, parse_model_bank_config


def _manifest_with_test_value_profiles() -> dict[str, JsonValue]:
    """Build a minimal v1 manifest that declares test-value profiles.

    Returns:
        Raw manifest object suitable for parsing in unit tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "Test values",
        "testValueProfiles": {
            "defaultProfileId": "sandbox",
            "profiles": [
                {
                    "id": "sandbox",
                    "label": "Sandbox",
                    "values": {
                        "paymentId": "pmnt-001",
                        "scheme": "UK.OBIE.SortCodeAccountNumber",
                    },
                    "generatedKeys": {"endToEndId": "per-run-uuid"},
                },
                {
                    "id": "uat",
                    "label": "UAT",
                    "values": {
                        "paymentId": "pmnt-uat",
                        "scheme": "UK.OBIE.IBAN",
                    },
                },
            ],
            "allowedOverrideKeys": ["paymentId", "customReference"],
            "nonSecretKeys": ["scheme", "customReference"],
        },
        "steps": [
            {
                "id": "payments",
                "name": "Payments",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/payments/${testValues.paymentId}",
                    "body": {
                        "Data": {
                            "EndToEndIdentification": "${testValues.endToEndId}",
                            "Initiation": {"LocalInstrument": "${testValues.scheme}"},
                        }
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
                "selectionMetadata": {
                    "conditionId": "payments-supported",
                    "conditionLabel": "Payments supported",
                    "conditional": True,
                    "requiredTestValueKeys": ["paymentId", "customReference"],
                },
            },
            {
                "id": "psu",
                "name": "PSU",
                "kind": "psu-authorization",
                "mode": "manual",
                "authorizationEndpoint": "https://auth.example.com/authorize",
                "clientId": "client-123",
                "redirectUri": "https://app.example.com/callback",
                "selectionMetadata": {
                    "conditionLabel": "Needs PSU",
                    "requiredTestValueKeys": ["paymentId"],
                },
            },
        ],
    }


def _minimal_manifest_without_profiles() -> dict[str, JsonValue]:
    """Build a backward-compatible v1 manifest with no test-value metadata.

    Returns:
        Raw manifest object suitable for parsing in unit tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "No profiles",
        "steps": [
            {
                "id": "health",
                "name": "Health",
                "request": {"method": "GET", "url": "https://example.com/health"},
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }


@pytest.mark.unit
def test_manifest_parses_test_value_profiles_and_selection_metadata() -> None:
    manifest = parse_manifest(_manifest_with_test_value_profiles())

    assert manifest.test_value_profiles is not None
    assert manifest.test_value_profiles.default_profile_id == "sandbox"
    assert manifest.test_value_profiles.allowed_override_keys == frozenset({"paymentId", "customReference"})
    assert manifest.test_value_profiles.non_secret_keys == frozenset({"scheme", "customReference"})
    assert manifest.test_value_profiles.profiles[0].generated_keys["endToEndId"] == "per-run-uuid"

    http_step = cast(ManifestStep, manifest.steps[0])
    assert http_step.selection_metadata is not None
    assert http_step.selection_metadata.condition_id == "payments-supported"
    assert http_step.selection_metadata.required_test_value_keys == ("paymentId", "customReference")
    assert http_step.request.url == "https://example.com/payments/${testValues.paymentId}"

    psu_step = cast(PsuAuthorizationStep, manifest.steps[1])
    assert psu_step.selection_metadata is not None
    assert psu_step.selection_metadata.condition_label == "Needs PSU"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda raw: raw.__setitem__("testValueProfiles", "bad"),
            r"manifest\.testValueProfiles must be a JSON object",
        ),
        (
            lambda raw: cast(dict[str, JsonValue], raw["testValueProfiles"]).__setitem__("defaultProfileId", "missing"),
            r"manifest\.testValueProfiles\.defaultProfileId must match one of the declared profiles",
        ),
        (
            lambda raw: cast(
                dict[str, JsonValue],
                cast(
                    dict[str, JsonValue],
                    cast(list[JsonValue], cast(dict[str, JsonValue], raw["testValueProfiles"])["profiles"])[0],
                )["values"],
            ).__setitem__("1bad", "x"),
            r"values key '1bad' is invalid",
        ),
        (
            lambda raw: cast(
                dict[str, JsonValue],
                cast(
                    dict[str, JsonValue],
                    cast(list[JsonValue], cast(dict[str, JsonValue], raw["testValueProfiles"])["profiles"])[0],
                )["generatedKeys"],
            ).__setitem__("paymentId", "per-run-uuid"),
            r"generatedKeys\.paymentId duplicates .*values\.paymentId",
        ),
        (
            lambda raw: cast(
                dict[str, JsonValue],
                cast(
                    dict[str, JsonValue],
                    cast(list[JsonValue], cast(dict[str, JsonValue], raw["testValueProfiles"])["profiles"])[0],
                )["generatedKeys"],
            ).__setitem__("traceId", "bad-kind"),
            r"generatedKeys\.traceId must be 'per-run-uuid' or 'per-run-compact-uuid'",
        ),
    ],
)
def test_manifest_rejects_invalid_test_value_profile_schemas(
    mutator: Callable[[dict[str, JsonValue]], None], message: str
) -> None:
    raw = _manifest_with_test_value_profiles()
    mutator(raw)

    with pytest.raises(ManifestError, match=message):
        parse_manifest(raw)


@pytest.mark.unit
def test_manifest_rejects_selection_metadata_for_undeclared_key() -> None:
    raw = _manifest_with_test_value_profiles()
    step = cast(dict[str, JsonValue], cast(list[JsonValue], raw["steps"])[0])
    selection = cast(dict[str, JsonValue], step["selectionMetadata"])
    selection["requiredTestValueKeys"] = ["missingKey"]

    with pytest.raises(ManifestError, match=r"references undeclared test-value key 'missingKey'"):
        parse_manifest(raw)


@pytest.mark.unit
def test_manifest_rejects_undeclared_test_values_placeholder() -> None:
    raw = _manifest_with_test_value_profiles()
    request = cast(dict[str, JsonValue], cast(dict[str, JsonValue], cast(list[JsonValue], raw["steps"])[0])["request"])
    request["url"] = "https://example.com/${testValues.missingKey}"

    with pytest.raises(ManifestError, match=r"contains undeclared testValues key: \$\{testValues\.missingKey\}"):
        parse_manifest(raw)


@pytest.mark.unit
def test_manifest_rejects_test_values_placeholder_when_profiles_are_absent() -> None:
    raw = _minimal_manifest_without_profiles()
    request = cast(dict[str, JsonValue], cast(dict[str, JsonValue], cast(list[JsonValue], raw["steps"])[0])["request"])
    request["url"] = "https://example.com/${testValues.paymentId}"

    with pytest.raises(ManifestError, match=r"no testValueProfiles declared in this manifest"):
        parse_manifest(raw)


@pytest.mark.unit
def test_parse_model_bank_config_accepts_test_values_profile_and_overrides(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testValues": {
                "profile": "uat",
                "overrides": {
                    "paymentId": "pmnt-override",
                    "customReference": "ref-123",
                },
            },
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )

    assert config.test_values is not None
    assert config.test_values.profile == "uat"
    assert dict(config.test_values.overrides) == {"paymentId": "pmnt-override", "customReference": "ref-123"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_test_values",
    [
        {"overrides": {"1bad": "value"}},
        {"overrides": {"paymentId": 123}},
        {"profile": "   "},
    ],
)
def test_parse_model_bank_config_rejects_invalid_test_values(
    tmp_path: Path, raw_test_values: dict[str, JsonValue]
) -> None:
    with pytest.raises(ConfigError):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testValues": raw_test_values,
            },
            base_dir=tmp_path,
            output_base_dir=tmp_path,
        )


@pytest.mark.unit
def test_resolve_placeholders_reads_test_values_from_runtime_config() -> None:
    context = ExecutionContext(
        config=RuntimeConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            environment="sandbox",
            test_values={"paymentId": "pmnt-123"},
        )
    )

    assert resolve_placeholders("id=${testValues.paymentId}", context) == "id=pmnt-123"


@pytest.mark.unit
def test_build_runtime_test_values_merges_selected_profile_overrides_and_generated_keys(tmp_path: Path) -> None:
    manifest = parse_manifest(_manifest_with_test_value_profiles())
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testValues": {
                "profile": "uat",
                "overrides": {
                    "paymentId": "pmnt-override",
                    "customReference": "ref-456",
                },
            },
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )

    effective = build_runtime_test_values(manifest, config.test_values)

    assert effective["paymentId"] == "pmnt-override"
    assert effective["scheme"] == "UK.OBIE.IBAN"
    assert effective["customReference"] == "ref-456"

    default_effective = build_runtime_test_values(manifest, None)
    assert default_effective["paymentId"] == "pmnt-001"
    generated_value = default_effective["endToEndId"]
    assert generated_value == default_effective["endToEndId"]
    assert str(uuid.UUID(generated_value)) == generated_value


@pytest.mark.unit
def test_build_runtime_test_values_rejects_disallowed_override_keys(tmp_path: Path) -> None:
    manifest = parse_manifest(_manifest_with_test_value_profiles())
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testValues": {"overrides": {"notAllowed": "value"}},
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )

    with pytest.raises(ValueError, match=r"disallowed key\(s\): notAllowed"):
        build_runtime_test_values(manifest, config.test_values)


@pytest.mark.unit
def test_backward_compatibility_manifest_without_test_value_profiles_still_parses() -> None:
    manifest = parse_manifest(_minimal_manifest_without_profiles())

    assert manifest.test_value_profiles is None
    assert build_runtime_test_values(manifest, None) == {}


def _manifest_with_compact_uuid_profile() -> dict[str, JsonValue]:
    """Build a minimal v1 manifest using the ``per-run-compact-uuid`` generated kind.

    Returns:
        Raw manifest object suitable for parsing in unit tests.
    """
    return {
        "schemaVersion": "v1",
        "name": "Compact UUID test",
        "testValueProfiles": {
            "defaultProfileId": "default",
            "profiles": [
                {
                    "id": "default",
                    "label": "Default",
                    "values": {"amount": "1.00"},
                    "generatedKeys": {
                        "instructionId": "per-run-compact-uuid",
                        "e2eId": "per-run-compact-uuid",
                    },
                }
            ],
            "allowedOverrideKeys": [],
            "nonSecretKeys": [],
        },
        "steps": [
            {
                "id": "payment",
                "name": "Payment",
                "request": {
                    "method": "POST",
                    "url": "https://example.com/payments",
                    "body": {
                        "InstructionIdentification": "${testValues.instructionId}",
                        "EndToEndIdentification": "${testValues.e2eId}",
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }


@pytest.mark.unit
def test_manifest_parses_compact_uuid_generated_kind() -> None:
    """``per-run-compact-uuid`` is accepted as a valid generated-value kind."""
    manifest = parse_manifest(_manifest_with_compact_uuid_profile())

    assert manifest.test_value_profiles is not None
    assert manifest.test_value_profiles.profiles[0].generated_keys["instructionId"] == "per-run-compact-uuid"
    assert manifest.test_value_profiles.profiles[0].generated_keys["e2eId"] == "per-run-compact-uuid"


@pytest.mark.unit
def test_build_runtime_test_values_compact_uuid_is_32_hex_chars() -> None:
    """Runtime ``per-run-compact-uuid`` generates a 32-character lowercase hex string.

    The Open Banking PIS schema limits ``InstructionIdentification`` and
    ``EndToEndIdentification`` to 35 characters, so the compact UUID (32 chars)
    fits within that constraint while remaining unique per run.
    """
    manifest = parse_manifest(_manifest_with_compact_uuid_profile())

    effective = build_runtime_test_values(manifest, None)

    instruction_id = effective["instructionId"]
    e2e_id = effective["e2eId"]
    assert len(instruction_id) == 32  # noqa: PLR2004 — 32 hex chars from uuid4().hex
    assert len(e2e_id) == 32  # noqa: PLR2004
    assert instruction_id.islower()
    assert all(c in "0123456789abcdef" for c in instruction_id)
    # Each run generates a fresh value; different keys should not collide
    assert instruction_id != e2e_id
    # Must be parseable as a UUID (reinsert hyphens)
    reparsed = uuid.UUID(instruction_id)
    assert reparsed.hex == instruction_id
