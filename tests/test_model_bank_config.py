import math
from pathlib import Path

import pytest

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
        ("suite", "full-read-write", "testSuite.suite must be one of: discovery-jwks"),
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
