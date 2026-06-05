import json
import math
from pathlib import Path

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.json_types import JsonValue
from conformance.model_bank_config import (
    ConfigError,
    SuiteSelection,
    SuiteSpecVersion,
    load_model_bank_config,
    parse_model_bank_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-example.json"


def _write_approved_release_policy(tmp_path: Path, *, versions: list[str] | None = None) -> Path:
    """Write an approved-release policy fixture.

    Args:
        tmp_path: Temporary directory used for the policy file.
        versions: Optional list of approved tool versions to write.

    Returns:
        Path to the written policy JSON file.
    """
    policy_path = tmp_path / "approved-releases.json"
    policy_path.write_text(
        json.dumps(
            {
                "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
                "approvedToolVersions": versions or ["1.2.3"],
            }
        ),
        encoding="utf-8",
    )
    return policy_path


@pytest.mark.unit
def test_example_model_bank_config_is_valid_json_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    config = load_model_bank_config(EXAMPLE_CONFIG_PATH)

    assert config.environment == "ozone-model-bank"
    assert config.discovery_url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert config.follow_up_mode == "discovery_only"
    assert config.result_output_path == tmp_path / "out" / "test-results.json"
    assert config.test_suite is None


@pytest.mark.unit
def test_parse_model_bank_config_defaults_result_output_to_out_dir(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )

    assert config.result_output_path == tmp_path / "out" / "test-results.json"


@pytest.mark.unit
def test_load_model_bank_config_reads_json_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        """
        {
          "environment": "ozone-model-bank",
          "discoveryUrl": "https://example.com/.well-known/openid-configuration",
          "timeoutSeconds": 3,
          "followUp": {"mode": "discovery_only"},
          "resultOutputPath": "results/model-bank.json"
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    config = load_model_bank_config(config_path)

    assert config.environment == "ozone-model-bank"
    assert config.discovery_url == "https://example.com/.well-known/openid-configuration"
    assert config.timeout_seconds == 3.0
    assert config.follow_up_mode == "discovery_only"
    assert config.result_output_path == tmp_path / "results" / "model-bank.json"


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_parse_model_bank_config_accepts_supported_test_suite(spec_version: SuiteSpecVersion, tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": spec_version,
                "profile": "fapi1-advanced",
                "suite": "discovery-jwks",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite="discovery-jwks",
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0"])
def test_parse_model_bank_config_accepts_psu_auth_starter_suite(spec_version: SuiteSpecVersion, tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": spec_version,
                "profile": "fapi1-advanced",
                "suite": "psu-auth-starter",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite="psu-auth-starter",
    )
    assert config.oauth is not None
    assert config.oauth.client_id == "my-client-id"
    assert config.oauth.redirect_uri == "https://example.com/callback"


@pytest.mark.unit
def test_parse_model_bank_config_keeps_test_suite_optional_for_smoke_checks(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.test_suite is None


@pytest.mark.unit
def test_parse_model_bank_config_loads_approved_release_policy(tmp_path: Path) -> None:
    policy_path = _write_approved_release_policy(tmp_path, versions=["1.2.3", "4.5.6"])

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "approvedReleasePolicyPath": policy_path.name,
        },
        base_dir=tmp_path,
    )

    assert config.approved_release_policy is not None
    assert config.approved_release_policy.approved_tool_versions == ("1.2.3", "4.5.6")


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_string_approved_release_policy_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="approvedReleasePolicyPath must be a non-empty string"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "approvedReleasePolicyPath": 42,
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_missing_approved_release_policy_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="approvedReleasePolicyPath must point to an existing file"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "approvedReleasePolicyPath": "missing.json",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_approved_release_policy_path_escape(tmp_path: Path) -> None:
    outside_policy = tmp_path.parent / "approved-releases-outside.json"
    outside_policy.write_text(
        json.dumps(
            {
                "schemaVersion": APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
                "approvedToolVersions": ["1.2.3"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="approvedReleasePolicyPath must resolve inside the config root"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "approvedReleasePolicyPath": "../approved-releases-outside.json",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_wraps_malformed_approved_release_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "approved-releases.json"
    policy_path.write_text("{", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid approved-release policy: Invalid JSON approved-release policy"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "approvedReleasePolicyPath": policy_path.name,
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_object_test_suite(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="testSuite must be a JSON object"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": "discovery-jwks",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_missing_test_suite_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="testSuite.specVersion must be a non-empty string"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "ob-read-write",
                    "profile": "fapi1-advanced",
                    "suite": "discovery-jwks",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_test_suite_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"Unknown testSuite field\(s\): label"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "ob-read-write",
                    "specVersion": "v4.0",
                    "profile": "fapi1-advanced",
                    "suite": "discovery-jwks",
                    "label": "Open Banking Read/Write discovery",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value", "expected_message"),
    [
        ("standard", "ob-business-banking", "testSuite.standard must be one of: ob-read-write"),
        ("specVersion", "v3.1.10", "testSuite.specVersion must be one of: v3.1.11, v4.0"),
        ("profile", "fapi2-security-profile", "testSuite.profile must be one of: fapi1-advanced"),
        ("suite", "full-read-write", "testSuite.suite must be one of: discovery-jwks, psu-auth-starter"),
    ],
)
def test_parse_model_bank_config_rejects_unsupported_test_suite_values(
    field_name: str,
    value: str,
    expected_message: str,
    tmp_path: Path,
) -> None:
    test_suite: dict[str, JsonValue] = {
        "standard": "ob-read-write",
        "specVersion": "v4.0",
        "profile": "fapi1-advanced",
        "suite": "discovery-jwks",
    }
    test_suite[field_name] = value

    with pytest.raises(ConfigError, match=expected_message):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": test_suite,
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_load_model_bank_config_rejects_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        """
        {
          "environment": "ozone-model-bank",
          "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Invalid JSON config"):
        load_model_bank_config(config_path)


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_https_discovery_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must be an HTTPS URL"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": "http://example.com/discovery"},
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "discovery_url",
    [
        "https://:443/discovery",
        "https://example.com:abc/discovery",
        "https://example.com:0/discovery",
    ],
)
def test_parse_model_bank_config_rejects_invalid_discovery_url(discovery_url: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must be (a valid HTTPS URL|an HTTPS URL)"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": discovery_url},
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_discovery_url_userinfo(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must not include credentials"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": "https://client@example.com/discovery"},
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize("timeout_seconds", [math.nan, math.inf, -math.inf])
def test_parse_model_bank_config_rejects_non_finite_timeout_seconds(timeout_seconds: float, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="timeoutSeconds must be a positive number"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "timeoutSeconds": timeout_seconds,
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_fields(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown config field"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "unsupportedField": "nope",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_follow_up_mode(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="followUp.mode must be one of"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "followUp": {"mode": "token"},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_requires_client_cert_and_key_together(tmp_path: Path) -> None:
    cert_root = tmp_path / "certs"
    cert_root.mkdir()
    cert_path = cert_root / "client.pem"
    cert_path.write_text("certificate", encoding="utf-8")

    with pytest.raises(ConfigError, match="must be supplied together"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "tls": {
                    "certificatePathRoot": "certs",
                    "clientCertificatePath": "client.pem",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_certificate_path_traversal(tmp_path: Path) -> None:
    cert_root = tmp_path / "certs"
    cert_root.mkdir()
    outside_cert = tmp_path / "outside.pem"
    outside_cert.write_text("certificate", encoding="utf-8")

    with pytest.raises(ConfigError, match="must resolve inside certificatePathRoot"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "tls": {
                    "certificatePathRoot": "certs",
                    "caBundlePath": "../outside.pem",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "discovery_url",
    [
        "https://127.0.0.1/.well-known/openid-configuration",
        "https://[::1]/.well-known/openid-configuration",
    ],
)
def test_parse_model_bank_config_rejects_ip_literal_discovery_url(discovery_url: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must use a DNS hostname, not an IP literal"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": discovery_url},
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "discovery_url",
    [
        "https://bad_host.example/.well-known/openid-configuration",
        "https://-leading-dash.example/.well-known/openid-configuration",
    ],
)
def test_parse_model_bank_config_rejects_malformed_hostname_discovery_url(discovery_url: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must be a valid HTTPS URL"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": discovery_url},
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_defaults_execution_log_path(tmp_path: Path) -> None:
    """``executionLogPath`` defaults to ``out/execution-log.ndjson`` under output_base_dir."""
    config = parse_model_bank_config(
        {
            "environment": "env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )
    assert config.execution_log_path == tmp_path / "out" / "execution-log.ndjson"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_explicit_execution_log_path(tmp_path: Path) -> None:
    """An explicit ``executionLogPath`` overrides the default."""
    config = parse_model_bank_config(
        {
            "environment": "env",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "executionLogPath": "logs/run.ndjson",
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )
    assert config.execution_log_path == tmp_path / "logs" / "run.ndjson"


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    """Unknown top-level keys are still rejected after adding executionLogPath."""
    with pytest.raises(ConfigError, match="Unknown config field"):
        parse_model_bank_config(
            {
                "environment": "env",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "executionLog": "logs/run.ndjson",
            },
            base_dir=tmp_path,
            output_base_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# Packet C — narrow OAuth participant config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_model_bank_config_accepts_oauth_section(tmp_path: Path) -> None:
    """A valid ``oauth`` object with clientId and redirectUri is accepted."""
    from conformance.model_bank_config import OAuthConfig

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "clientId": "my-client-001",
                "redirectUri": "https://app.example.com/callback",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth == OAuthConfig(
        client_id="my-client-001",
        redirect_uri="https://app.example.com/callback",
    )


@pytest.mark.unit
def test_parse_model_bank_config_keeps_oauth_optional(tmp_path: Path) -> None:
    """Config without ``oauth`` section must produce ``oauth=None``."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.oauth is None


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_object_oauth(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth must be a JSON object"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": "my-client-001",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_oauth_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"Unknown oauth field\(s\): clientSecret"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://app.example.com/callback",
                    "clientSecret": "should-not-be-here",  # pragma: allowlist secret
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_missing_oauth_client_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.clientId must be a non-empty string"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "redirectUri": "https://app.example.com/callback",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_missing_oauth_redirect_uri(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.redirectUri must be a non-empty string"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_http_oauth_redirect_uri(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.redirectUri must be an HTTPS URL"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "http://app.example.com/callback",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_ip_literal_oauth_redirect_uri(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.redirectUri must use a DNS hostname"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://127.0.0.1/callback",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_oauth_redirect_uri_with_credentials(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.redirectUri must not include credentials"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://user@app.example.com/callback",
                },
            },
            base_dir=tmp_path,
        )
