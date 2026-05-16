from pathlib import Path
from typing import cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import ManifestError, load_manifest, parse_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST_PATH = REPO_ROOT / "config" / "manifest-v0-openid-jwks-example.json"


def valid_manifest() -> dict[str, JsonValue]:
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


def first_test(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    tests = cast("list[JsonValue]", raw_manifest["tests"])
    return cast("dict[str, JsonValue]", tests[0])


def request_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", first_test(raw_manifest)["request"])


def assertion_configs(raw_manifest: dict[str, JsonValue]) -> list[JsonValue]:
    return cast("list[JsonValue]", first_test(raw_manifest)["assertions"])


def follow_up_config(raw_manifest: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", first_test(raw_manifest)["followUp"])


@pytest.mark.unit
def test_load_example_manifest_returns_typed_manifest() -> None:
    manifest = load_manifest(EXAMPLE_MANIFEST_PATH)

    assert manifest.schema_version == "v0"
    assert manifest.name == "Ozone OpenID discovery and JWKS smoke check"
    assert len(manifest.tests) == 1
    test = manifest.tests[0]
    assert test.id == "openid-discovery"
    assert test.request.method == "GET"
    assert test.request.url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert test.follow_up is not None
    assert test.follow_up.type == "jwks"
    assert test.follow_up.url_source == "response.body.jwks_uri"


@pytest.mark.unit
def test_load_manifest_rejects_malformed_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schemaVersion": "v0",', encoding="utf-8")

    with pytest.raises(ManifestError, match="Invalid JSON manifest"):
        load_manifest(manifest_path)


@pytest.mark.unit
def test_load_manifest_rejects_non_object_root(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ManifestError, match="Manifest root must be a JSON object"):
        load_manifest(manifest_path)


@pytest.mark.unit
def test_parse_manifest_accepts_valid_minimal_discovery_manifest() -> None:
    raw_manifest = valid_manifest()
    first_test(raw_manifest).pop("followUp")

    manifest = parse_manifest(raw_manifest)

    assert manifest.schema_version == "v0"
    assert manifest.tests[0].follow_up is None
    assert [assertion.type for assertion in manifest.tests[0].assertions] == [
        "http_status",
        "json_field",
        "json_field",
    ]


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_schema_version() -> None:
    raw_manifest = valid_manifest()
    raw_manifest["schemaVersion"] = "v1"

    with pytest.raises(ManifestError, match="schemaVersion must be v0"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_missing_required_fields() -> None:
    raw_manifest = valid_manifest()
    raw_manifest.pop("tests")

    with pytest.raises(ManifestError, match="tests must be a non-empty array"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unknown_fields() -> None:
    raw_manifest = valid_manifest()
    raw_manifest["unexpected"] = "nope"

    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_https_request_url() -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["url"] = "http://example.com/.well-known/openid-configuration"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.url must be an HTTPS URL"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://example .com/.well-known/openid-configuration",
        "https://example.com\n.evil.test/.well-known/openid-configuration",
        "https://bad_host.example/.well-known/openid-configuration",
        "https://-example.com/.well-known/openid-configuration",
    ],
)
def test_parse_manifest_rejects_malformed_https_request_url(url: str) -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["url"] = url

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.url must be a valid HTTPS URL"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_get_request_method() -> None:
    raw_manifest = valid_manifest()
    request_config(raw_manifest)["method"] = "POST"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.request\.method must be GET"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_assertion_type() -> None:
    raw_manifest = valid_manifest()
    assertion_configs(raw_manifest).append({"type": "token_claim", "claim": "iss"})

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[3\]\.type must be one of"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_json_field_rule() -> None:
    raw_manifest = valid_manifest()
    json_field_assertion = cast("dict[str, JsonValue]", assertion_configs(raw_manifest)[1])
    json_field_assertion["rule"] = "non_empty"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[1\]\.rule must be one of"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
@pytest.mark.parametrize("expected", [True, 99, 600])
def test_parse_manifest_rejects_invalid_http_status_code(expected: JsonValue) -> None:
    raw_manifest = valid_manifest()
    http_status_assertion = cast("dict[str, JsonValue]", assertion_configs(raw_manifest)[0])
    http_status_assertion["expected"] = expected

    with pytest.raises(ManifestError, match=r"tests\[0\]\.assertions\[0\]\.expected must be an HTTP status code"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_follow_up_shape() -> None:
    raw_manifest = valid_manifest()
    follow_up_config(raw_manifest)["type"] = "token_endpoint"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.type must be jwks"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_unsupported_follow_up_url_source() -> None:
    raw_manifest = valid_manifest()
    follow_up_config(raw_manifest)["urlSource"] = "response.body.issuer"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.urlSource must be response\.body\.jwks_uri"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_null_follow_up() -> None:
    raw_manifest = valid_manifest()
    first_test(raw_manifest)["followUp"] = None

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp must be a JSON object"):
        parse_manifest(raw_manifest)


@pytest.mark.unit
def test_parse_manifest_rejects_non_get_follow_up_request_method() -> None:
    raw_manifest = valid_manifest()
    follow_up_request = cast("dict[str, JsonValue]", follow_up_config(raw_manifest)["request"])
    follow_up_request["method"] = "POST"

    with pytest.raises(ManifestError, match=r"tests\[0\]\.followUp\.request\.method must be GET"):
        parse_manifest(raw_manifest)
