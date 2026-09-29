"""Model-bank config parsing: discovery, transport, output paths, and release policy."""

import json
from pathlib import Path

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.model_bank_config import (
    ConfigError,
    load_model_bank_config,
    parse_model_bank_config,
)

pytestmark = pytest.mark.unit


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


def test_discovery_only_model_bank_config_is_valid_json_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        json.dumps(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
                "followUp": {"mode": "discovery_only"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_model_bank_config(config_path)

    assert config.discovery_url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert config.follow_up_mode == "discovery_only"
    assert config.result_output_path == tmp_path / "out" / "test-results.json"


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


def test_parse_model_bank_config_allows_missing_discovery_url(tmp_path: Path) -> None:
    """Discovery URL is optional for manually configured compiled plans."""
    config = parse_model_bank_config(
        {"environment": "ozone-model-bank"},
        base_dir=tmp_path,
    )

    assert config.discovery_url is None


def test_load_model_bank_config_reads_json_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "model-bank.json"
    config_path.write_text(
        """
        {
          "environment": "ozone-model-bank",
          "discoveryUrl": "https://example.com/.well-known/openid-configuration",
          "followUp": {"mode": "discovery_only"},
          "resultOutputPath": "results/model-bank.json"
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    config = load_model_bank_config(config_path)
    assert config.discovery_url == "https://example.com/.well-known/openid-configuration"
    assert config.discovery_url == "https://example.com/.well-known/openid-configuration"
    assert config.follow_up_mode == "discovery_only"
    assert config.result_output_path == tmp_path / "results" / "model-bank.json"


def test_parse_model_bank_config_keeps_plan_spec_external_to_config(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.discovery_url == "https://example.com/.well-known/openid-configuration"


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


def test_parse_model_bank_config_rejects_removed_test_suite_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown config field"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {"suite": "removed-suite"},
            },
            base_dir=tmp_path,
        )


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


def test_parse_model_bank_config_rejects_non_https_discovery_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must be an HTTPS URL"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": "http://example.com/discovery"},
            base_dir=tmp_path,
        )


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


def test_parse_model_bank_config_rejects_discovery_url_userinfo(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="discoveryUrl must not include credentials"):
        parse_model_bank_config(
            {"environment": "ozone-model-bank", "discoveryUrl": "https://client@example.com/discovery"},
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_rejects_timeout_seconds(tmp_path: Path) -> None:
    """Participant config no longer accepts configurable HTTP timeouts."""
    with pytest.raises(ConfigError, match="Unknown config field\\(s\\): timeoutSeconds"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "timeoutSeconds": 3,
            },
            base_dir=tmp_path,
        )


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
                    "clientCertificatePath": str(cert_path),
                },
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_rejects_relative_certificate_path(tmp_path: Path) -> None:
    cert_root = tmp_path / "certs"
    cert_root.mkdir()
    ca_bundle = cert_root / "openbanking-preprod-ca-bundle.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")

    with pytest.raises(ConfigError, match="caBundlePath must be an absolute file path"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "tls": {
                    "caBundlePath": str(ca_bundle.relative_to(tmp_path)),
                },
            },
            base_dir=tmp_path,
        )


def test_parse_model_bank_config_accepts_absolute_tls_ca_bundle_path(tmp_path: Path) -> None:
    """TLS certificate paths are supplied as full absolute paths."""
    cert_root = tmp_path / "local-config" / "certs"
    cert_root.mkdir(parents=True)
    ca_bundle = cert_root / "openbanking-preprod-ca-bundle.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "tls": {
                "caBundlePath": str(ca_bundle),
            },
        },
        base_dir=tmp_path,
    )

    assert config.tls.ca_bundle_path == ca_bundle.resolve()


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
