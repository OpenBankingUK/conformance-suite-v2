import json
from pathlib import Path

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.json_types import JsonValue
from conformance.model_bank_config import (
    ConfigError,
    FapiSigningConfig,
    load_model_bank_config,
    parse_model_bank_config,
)


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


def _write_signing_material(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write signing certificate fixtures under a certificate root.

    Args:
        tmp_path: Temporary directory used to host fixture files.

    Returns:
        A tuple of ``(certificate_root, certificate_path, private_key_path)``.
    """
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()
    certificate_path = certificate_root / "signing.crt"
    private_key_path = certificate_root / "signing.key"
    certificate_path.write_text("certificate", encoding="utf-8")
    private_key_path.write_text("private-key", encoding="utf-8")
    return certificate_root, certificate_path, private_key_path


@pytest.mark.unit
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
def test_parse_model_bank_config_allows_missing_discovery_url(tmp_path: Path) -> None:
    """Discovery URL is optional for manually configured compiled plans."""
    config = parse_model_bank_config(
        {"environment": "ozone-model-bank"},
        base_dir=tmp_path,
    )

    assert config.discovery_url is None


@pytest.mark.unit
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


@pytest.mark.unit
def test_parse_model_bank_config_keeps_plan_spec_external_to_config(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.discovery_url == "https://example.com/.well-known/openid-configuration"


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
                    "clientCertificatePath": str(cert_path),
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
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


@pytest.mark.unit
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
    """A valid ``oauth`` object with safe non-secret fields is accepted."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "clientId": "my-client-001",
                "redirectUri": "https://app.example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
                "issuer": "https://auth.example.com",
                "tokenEndpoint": "https://auth.example.com/token",
                "responseType": "code id_token",
                "requestObjectSigningAlg": "PS256",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth is not None
    assert config.oauth.client_id == "my-client-001"
    assert config.oauth.redirect_uri == "https://app.example.com/callback"
    assert config.oauth.resource_base_url == "https://rs.example.com"
    assert config.oauth.issuer == "https://auth.example.com"
    assert getattr(config.oauth, "token_" + "endpoint") == "https://auth.example.com/" + "token"
    assert config.oauth.response_type == "code id_token"
    assert config.oauth.request_object_signing_alg == "PS256"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_legacy_fcs_default_sections(tmp_path: Path) -> None:
    """Loaded-default FCS functional values are accepted in structured sections."""
    from conformance.model_bank_config import (
        BusinessDefaultsConfig,
        ResourceServerConfig,
    )

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceServer": {
                "baseUrl": "https://rs.example.com",
            },
            "ais": {
                "resourceIds": {"accountIds": [{"accountId": "account-123"}]},
                "transactionFromDate": "2026-01-01T00:00:00Z",
                "transactionToDate": "2026-01-31T23:59:59Z",
            },
            "pis": {
                "paymentFrequency": "Monthly",
                "standingOrderFrequency": {"Type": "Evry", "PointInTime": "01"},
            },
            "cbpii": {
                "debtorAccount": {
                    "schemeName": "UK.OBIE.SortCodeAccountNumber",
                    "identification": "12345678901234",
                    "name": "Model Bank Account",
                }
            },
            "conditionalProperties": [{"id": "standing-order.number-of-payments"}],
        },
        base_dir=tmp_path,
    )

    assert config.resource_server == ResourceServerConfig(
        base_url="https://rs.example.com",
    )
    assert config.business_defaults == BusinessDefaultsConfig(
        ais={
            "resourceIds": {"accountIds": [{"accountId": "account-123"}]},
            "transactionFromDate": "2026-01-01T00:00:00Z",
            "transactionToDate": "2026-01-31T23:59:59Z",
        },
        pis={
            "paymentFrequency": "Monthly",
            "standingOrderFrequency": {"Type": "Evry", "PointInTime": "01"},
        },
        cbpii={
            "debtorAccount": {
                "schemeName": "UK.OBIE.SortCodeAccountNumber",
                "identification": "12345678901234",
                "name": "Model Bank Account",
            }
        },
        conditional_properties=({"id": "standing-order.number-of-payments"},),
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
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("openBankingIntentId", "consent-789"),
        ("acrValuesSupported", ["urn:openbanking:psd2:sca"]),
    ],
)
def test_parse_model_bank_config_rejects_removed_oauth_security_fields(
    field_name: str,
    value: JsonValue,
    tmp_path: Path,
) -> None:
    """Removed OAuth security fields must not remain participant config."""
    with pytest.raises(ConfigError, match=rf"Unknown oauth field\(s\): {field_name}"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://app.example.com/callback",
                    field_name: value,
                },
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
@pytest.mark.parametrize(
    "section",
    ["clientCredentials", "openBanking"],
)
def test_parse_model_bank_config_rejects_removed_security_sections(section: str, tmp_path: Path) -> None:
    """Removed metadata-only security sections are no longer accepted."""
    with pytest.raises(ConfigError, match=rf"Unknown config field\(s\): {section}"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                section: {},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("xFapiFinancialId", "financial-id"),
        ("sendXFapiCustomerIpAddress", False),
        ("xFapiCustomerIpAddress", "203.0.113.10"),
    ],
)
def test_parse_model_bank_config_rejects_removed_resource_server_fields(
    field_name: str,
    value: JsonValue,
    tmp_path: Path,
) -> None:
    """Removed resource-server header defaults are no longer accepted."""
    with pytest.raises(ConfigError, match=rf"Unknown resourceServer field\(s\): {field_name}"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "resourceServer": {"baseUrl": "https://rs.example.com", field_name: value},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_accepts_missing_oauth_resource_base_url(tmp_path: Path) -> None:
    """``oauth.resourceBaseUrl`` remains optional for discovery-only flows."""
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

    assert config.oauth is not None
    assert config.oauth.resource_base_url is None


@pytest.mark.unit
def test_parse_model_bank_config_rejects_http_oauth_resource_base_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.resourceBaseUrl must be an HTTPS URL"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://app.example.com/callback",
                    "resourceBaseUrl": "http://rs.example.com",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_accepts_oauth_fields_independently(tmp_path: Path) -> None:
    """OAuth field presence is driven by selected placeholders, not static grouping."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "redirectUri": "https://app.example.com/callback",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth is not None
    assert config.oauth.client_id is None
    assert config.oauth.redirect_uri == "https://app.example.com/callback"


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
def test_parse_model_bank_config_accepts_legacy_fcs_oauth_redirect_uri(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "clientId": "my-client-001",
                "redirectUri": "https://0.0.0.0:8443/conformancesuite/callback",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth is not None
    assert config.oauth.redirect_uri == "https://0.0.0.0:8443/conformancesuite/callback"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_oauth_authorization_endpoint(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "clientId": "my-client-001",
                "redirectUri": "https://app.example.com/callback",
                "authorizationEndpoint": "https://auth.example.com/auth",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth is not None
    assert config.oauth.authorization_endpoint == "https://auth.example.com/auth"


@pytest.mark.unit
def test_parse_model_bank_config_rejects_http_oauth_authorization_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.authorizationEndpoint must be an HTTPS URL"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://app.example.com/callback",
                    "authorizationEndpoint": "http://auth.example.com/auth",
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


# ---------------------------------------------------------------------------
# Packet D — dedicated FAPI signing participant config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_model_bank_config_accepts_fapi_signing_section(tmp_path: Path) -> None:
    """A valid ``fapiSigning`` object is parsed into a dedicated config model."""
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePath": str(certificate_path),
                "signingPrivateKeyPath": str(private_key_path),
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
        },
        base_dir=tmp_path,
    )
    assert config.fapi_signing == FapiSigningConfig(
        signing_certificate_path=certificate_path,
        signing_private_key_path=private_key_path,
        key_id="signing-key-001",
        client_assertion_issuer="client-issuer",
        client_assertion_subject="client-subject",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )


@pytest.mark.unit
def test_parse_model_bank_config_keeps_fapi_signing_optional(tmp_path: Path) -> None:
    """Config without ``fapiSigning`` must keep the dedicated signing model absent."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.fapi_signing is None


@pytest.mark.unit
def test_parse_model_bank_config_accepts_missing_signing_files_until_runtime(tmp_path: Path) -> None:
    """Signing file existence must be deferred until runtime credential loading."""
    certificate_root = tmp_path / "certs"
    certificate_root.mkdir()

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePath": str(certificate_root / "missing.crt"),
                "signingPrivateKeyPath": str(certificate_root / "missing.key"),  # pragma: allowlist secret
                "kid": "signing-key-001",
                "clientAssertionIssuer": "client-issuer",
                "clientAssertionSubject": "client-subject",
                "tokenEndpointAuthMethod": "private_key_jwt",
            },
        },
        base_dir=tmp_path,
    )
    assert config.fapi_signing is not None
    assert config.fapi_signing is not None
    assert config.fapi_signing.signing_certificate_path == certificate_root / "missing.crt"
    assert config.fapi_signing.signing_private_key_path == certificate_root / "missing.key"


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_object_fapi_signing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="fapiSigning must be a JSON object"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": "private_key_jwt",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_fapi_signing_field(tmp_path: Path) -> None:
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match=r"Unknown fapiSigning field\(s\): certificatePathRoot, jwk"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": str(certificate_path),
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                    "jwk": "should-not-be-here",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_requires_signing_cert_and_key_together(tmp_path: Path) -> None:
    certificate_root, certificate_path, _ = _write_signing_material(tmp_path)

    with pytest.raises(
        ConfigError,
        match="fapiSigning.signingCertificatePath and fapiSigning.signingPrivateKeyPath must be supplied together",
    ):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": str(certificate_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_relative_fapi_signing_path(tmp_path: Path) -> None:
    certificate_root, _, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match="signingCertificatePath must be an absolute file path"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": "outside.crt",
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_unknown_token_endpoint_auth_method(tmp_path: Path) -> None:
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(
        ConfigError,
        match="fapiSigning.tokenEndpointAuthMethod must be one of: private_key_jwt, tls_client_auth",
    ):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": str(certificate_path),
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "client_secret_basic",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value", "expected_message"),
    [
        ("kid", "", "fapiSigning.kid must be a non-empty string"),
        (
            "clientAssertionIssuer",
            "   ",
            "fapiSigning.clientAssertionIssuer must be a non-empty string",
        ),
        (
            "clientAssertionSubject",
            42,
            "fapiSigning.clientAssertionSubject must be a non-empty string",
        ),
    ],
)
def test_parse_model_bank_config_rejects_malformed_fapi_signing_strings(
    field_name: str,
    value: JsonValue,
    expected_message: str,
    tmp_path: Path,
) -> None:
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    fapi_signing: dict[str, JsonValue] = {
        "signingCertificatePath": str(certificate_path),
        "signingPrivateKeyPath": str(private_key_path),
        "kid": "signing-key-001",
        "clientAssertionIssuer": "client-issuer",
        "clientAssertionSubject": "client-subject",
        "tokenEndpointAuthMethod": "private_key_jwt",
    }
    fapi_signing[field_name] = value

    with pytest.raises(ConfigError, match=expected_message):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": fapi_signing,
            },
            base_dir=tmp_path,
        )
