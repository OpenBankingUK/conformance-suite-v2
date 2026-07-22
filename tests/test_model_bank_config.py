import json
import math
from pathlib import Path

import pytest

from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION
from conformance.json_types import JsonValue
from conformance.model_bank_config import (
    ConfigError,
    FapiSigningConfig,
    OpenBankingConfig,
    load_model_bank_config,
    parse_model_bank_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-example.json"
EXAMPLE_PIS_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-pis-domestic-payment-starter-example.json"
"""Committed example config for the bundled PIS domestic-payment starter suite."""
EXAMPLE_AIS_BASELINE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-ais-certification-baseline-example.json"
"""Committed generic AIS certification baseline example config (targets v4.0.1)."""
EXAMPLE_AIS_BASELINE_V4_0_1_CONFIG_PATH = (
    REPO_ROOT / "config" / "model-bank-ais-certification-baseline-v4.0.1-example.json"
)
"""Committed explicit v4.0.1 AIS certification baseline example config."""


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
def test_parse_model_bank_config_defaults_result_output_to_out_dir(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
        output_base_dir=tmp_path,
    )

    assert config.environment is None
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
def test_parse_model_bank_config_accepts_fapi_signing_ob_metadata(tmp_path: Path) -> None:
    """fapiSigning.signatureIssuer and signatureTrustAnchor should be parsed into FapiSigningConfig."""
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "signingCertificatePath": str(certificate_path),
                "signingPrivateKeyPath": str(private_key_path),
                "kid": "ob-signing-key",
                "clientAssertionIssuer": "my-client-id",
                "clientAssertionSubject": "my-client-id",
                "tokenEndpointAuthMethod": "private_key_jwt",
                "signatureIssuer": "0015800001041RbAAI/WznYcRurtfGGuhfqzGeH00",
                "signatureTrustAnchor": "openbanking.org.uk",
            },
        },
        base_dir=tmp_path,
    )

    assert config.fapi_signing is not None
    assert config.fapi_signing.signature_issuer == "0015800001041RbAAI/WznYcRurtfGGuhfqzGeH00"
    assert config.fapi_signing.signature_trust_anchor == "openbanking.org.uk"


@pytest.mark.unit
def test_parse_model_bank_config_rejects_fapi_signing_ob_metadata_partial(tmp_path: Path) -> None:
    """Supplying only one of signatureIssuer/signatureTrustAnchor must be rejected."""
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)
    with pytest.raises(
        ConfigError,
        match="fapiSigning.signatureIssuer and fapiSigning.signatureTrustAnchor must be supplied together",
    ):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": str(certificate_path),
                    "signingPrivateKeyPath": str(private_key_path),
                    "kid": "ob-key",
                    "clientAssertionIssuer": "client-id",
                    "clientAssertionSubject": "client-id",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                    "signatureIssuer": "0015800001041RbAAI/WznYcRurtfGGuhfqzGeH00",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_accepts_open_banking_section(tmp_path: Path) -> None:
    """openBanking.financialId should be parsed into OpenBankingConfig."""
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "openBanking": {
                "financialId": "0015800001041RHAAY",
            },
        },
        base_dir=tmp_path,
    )

    assert config.open_banking is not None
    assert config.open_banking == OpenBankingConfig(financial_id="0015800001041RHAAY")


@pytest.mark.unit
def test_parse_model_bank_config_open_banking_absent_yields_none(tmp_path: Path) -> None:
    """Omitting openBanking should leave open_banking as None."""
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )

    assert config.open_banking is None


@pytest.mark.unit
def test_parse_model_bank_config_rejects_open_banking_unknown_keys(tmp_path: Path) -> None:
    """openBanking with unknown keys must raise ConfigError."""
    with pytest.raises(ConfigError, match="Unknown openBanking field"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "openBanking": {
                    "financialId": "0015800001041RHAAY",
                    "unknownField": "value",
                },
            },
            base_dir=tmp_path,
        )


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
                    "clientCertificatePath": str(cert_path),
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_relative_certificate_path(tmp_path: Path) -> None:
    """TLS credential paths must be exact absolute files."""
    with pytest.raises(ConfigError, match="caBundlePath must be an absolute file path"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "tls": {
                    "caBundlePath": "relative-ca.pem",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_accepts_exact_absolute_ca_bundle_path(tmp_path: Path) -> None:
    """TLS CA bundle paths are accepted as exact absolute file paths."""
    ca_bundle = tmp_path / "openbanking-preprod-ca-bundle.pem"
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
    from conformance.model_bank_config import OAuthConfig

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "oauth": {
                "clientId": "my-client-001",
                "redirectUri": "https://app.example.com/callback",
                "openBankingIntentId": "consent-789",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.oauth == OAuthConfig(
        client_id="my-client-001",
        redirect_uri="https://app.example.com/callback",
        open_banking_intent_id="consent-789",
        resource_base_url="https://rs.example.com",
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
def test_parse_model_bank_config_rejects_blank_open_banking_intent_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oauth.openBankingIntentId must be a non-empty string"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "oauth": {
                    "clientId": "my-client-001",
                    "redirectUri": "https://app.example.com/callback",
                    "openBankingIntentId": "   ",
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
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

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
        certificate_path_root=certificate_root,
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
    assert config.fapi_signing.certificate_path_root == certificate_root
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
    _, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match=r"Unknown fapiSigning field\(s\): jwk"):
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
                    "tokenEndpointAuthMethod": "private_key_jwt",
                    "jwk": "should-not-be-here",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_requires_signing_cert_and_key_together(tmp_path: Path) -> None:
    _, certificate_path, _ = _write_signing_material(tmp_path)

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
def test_parse_model_bank_config_rejects_fapi_signing_relative_path(tmp_path: Path) -> None:
    """FAPI signing paths must be exact absolute paths."""
    _, _, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match="signingCertificatePath must be an absolute file path"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "signingCertificatePath": "relative.crt",
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


# ---------------------------------------------------------------------------
# testTarget parsing
# ---------------------------------------------------------------------------


def _write_dcr_material(tmp_path: Path) -> dict[str, str]:
    """Create fixture files for the five required DCR credential paths.

    Args:
        tmp_path: pytest tmp directory.

    Returns:
        Mapping of DCR credential key to exact absolute path string.
    """
    files = {
        "ssaPath": "software-statement.jwt",
        "signingPrivateKeyPath": "signing.key",  # pragma: allowlist secret
        "signingCertificatePath": "signing.crt",
        "transportCertificatePath": "transport.crt",
        "transportPrivateKeyPath": "transport.key",  # pragma: allowlist secret
    }
    for name in files.values():
        (tmp_path / name).write_text("fixture", encoding="utf-8")
    return {key: str(tmp_path / name) for key, name in files.items()}


@pytest.mark.unit
def test_parse_model_bank_config_accepts_test_target(tmp_path: Path) -> None:
    """A well-formed ``testTarget`` section populates ``config.test_target``."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testTarget": {
                "standard": "obl",
                "specification": "read-write",
                "securityProfile": "fapi1-advanced",
                "specificationVersion": "v4.0.1",
                "resourceGroups": ["ais"],
            },
        },
        base_dir=tmp_path,
    )
    assert config.test_target is not None
    assert config.test_target.specification == "read-write"
    assert config.test_target.resource_groups == ("ais",)


@pytest.mark.unit
def test_parse_model_bank_config_rejects_invalid_test_target(tmp_path: Path) -> None:
    """A malformed ``testTarget`` section raises :class:`ConfigError`."""
    with pytest.raises(ConfigError, match="testTarget is invalid"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testTarget": {"standard": "obl"},  # missing required fields
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_test_target_absent_yields_none(tmp_path: Path) -> None:
    """Absent ``testTarget`` yields ``config.test_target is None``."""
    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        },
        base_dir=tmp_path,
    )
    assert config.test_target is None


# ---------------------------------------------------------------------------
# dcr section parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_model_bank_config_accepts_full_dcr_section(tmp_path: Path) -> None:
    """A valid ``dcr`` section populates ``config.dcr`` with credential + transport."""
    files = _write_dcr_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "dcr": {
                **files,
                "tokenEndpointAuthMethod": "private_key_jwt",
                "disableKeepAlives": True,
                "timeoutSeconds": 15,
            },
        },
        base_dir=tmp_path,
    )
    assert config.dcr is not None
    assert config.dcr.transport.token_endpoint_auth_method == "private_key_jwt"  # noqa: S105
    assert config.dcr.transport.disable_keep_alives is True
    assert config.dcr.transport.connection_timeout_seconds == 15
    assert config.dcr.credential_paths.ssa_path.name == "software-statement.jwt"


@pytest.mark.unit
def test_parse_model_bank_config_dcr_defaults_auth_method(tmp_path: Path) -> None:
    """Missing ``tokenEndpointAuthMethod`` defaults to ``tls_client_auth``."""
    files = _write_dcr_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "sandbox",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "dcr": {**files},
        },
        base_dir=tmp_path,
    )
    assert config.dcr is not None
    assert config.dcr.transport.token_endpoint_auth_method == "tls_client_auth"  # noqa: S105
    assert config.dcr.transport.disable_keep_alives is False
    assert config.dcr.transport.tls_skip_verify is False


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_non_object(tmp_path: Path) -> None:
    """``dcr`` must be a JSON object; scalars are rejected."""
    with pytest.raises(ConfigError, match="dcr must be a JSON object"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": "invalid",
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_missing_required_paths(tmp_path: Path) -> None:
    """Missing required DCR credential paths produce a clear error message."""
    (tmp_path / "ssa.jwt").write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="dcr requires the following paths:"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": {"ssaPath": str(tmp_path / "ssa.jwt")},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_unknown_key(tmp_path: Path) -> None:
    """Unknown keys under ``dcr`` are rejected."""
    files = _write_dcr_material(tmp_path)
    with pytest.raises(ConfigError, match="dcr"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": {**files, "unknown": "field"},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_unknown_auth_method(tmp_path: Path) -> None:
    """``tokenEndpointAuthMethod`` outside the allowed set is rejected."""
    files = _write_dcr_material(tmp_path)
    with pytest.raises(ConfigError, match="tokenEndpointAuthMethod"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": {**files, "tokenEndpointAuthMethod": "client_secret_basic"},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_non_bool_disable_keep_alives(tmp_path: Path) -> None:
    """``disableKeepAlives`` must be a boolean."""
    files = _write_dcr_material(tmp_path)
    with pytest.raises(ConfigError, match="disableKeepAlives"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": {**files, "disableKeepAlives": "yes"},
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_dcr_tls_skip_verify(tmp_path: Path) -> None:
    """``tlsSkipVerify=true`` is rejected instead of disabling verification."""
    files = _write_dcr_material(tmp_path)
    with pytest.raises(ConfigError, match="certificate verification"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "dcr": {**files, "tlsSkipVerify": True},
            },
            base_dir=tmp_path,
        )
