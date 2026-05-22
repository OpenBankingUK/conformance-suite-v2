import math
from pathlib import Path

import pytest

from conformance.model_bank_config import ConfigError, load_model_bank_config, parse_model_bank_config

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
