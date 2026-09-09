"""Model-bank config parsing of the OAuth 2.0 section and legacy FCS defaults."""

from pathlib import Path

import pytest

from conformance.json_types import JsonValue
from conformance.model_bank_config import (
    ConfigError,
    parse_model_bank_config,
)

pytestmark = pytest.mark.unit


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
