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
    SuiteSelection,
    SuiteSpecVersion,
    load_model_bank_config,
    parse_model_bank_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-example.json"
EXAMPLE_PIS_CONFIG_PATH = REPO_ROOT / "config" / "model-bank-pis-domestic-payment-starter-example.json"
"""Committed example config for the bundled PIS domestic-payment starter suite."""


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
def test_example_model_bank_config_is_valid_json_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    config = load_model_bank_config(EXAMPLE_CONFIG_PATH)

    assert config.environment == "ozone-model-bank"
    assert config.discovery_url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert config.follow_up_mode == "discovery_only"
    assert config.result_output_path == tmp_path / "out" / "test-results.json"
    assert config.test_suite is None


@pytest.mark.unit
def test_example_pis_domestic_payment_starter_model_bank_config_is_valid_json_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Committed PIS starter example config should parse with bundled signing fixtures.

    Args:
        monkeypatch: Pytest fixture used to isolate the output directory.
        tmp_path: Temporary directory used as the active working directory.
    """
    monkeypatch.chdir(tmp_path)
    expected_method = "private_key_jwt"

    config = load_model_bank_config(EXAMPLE_PIS_CONFIG_PATH)

    assert config.environment == "ozone-model-bank"
    assert config.discovery_url == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    assert config.result_output_path == tmp_path / "out" / "test-results.json"
    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="pis-domestic-payment-starter",
        api="pis",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs1.obie.uk.ozoneapi.io"
    assert config.fapi_signing is not None
    assert config.fapi_signing.token_endpoint_auth_method == expected_method
    assert config.test_values is not None
    assert config.test_values.profile == "ozone-demo"
    assert dict(config.test_values.overrides) == {
        "creditorName": "Ozone Demo Merchant",
        "remittanceInformation": "Ozone domestic payment starter",
    }


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
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0", "v4.0.1"])
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
        api="ais",
    )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v3.1.11", "v4.0", "v4.0.1"])
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
                "openBankingIntentId": "consent-123",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite="psu-auth-starter",
        api="ais",
    )
    assert config.oauth is not None
    assert config.oauth.client_id == "my-client-id"
    assert config.oauth.redirect_uri == "https://example.com/callback"
    assert config.oauth.open_banking_intent_id == "consent-123"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_pis_domestic_payment_starter_suite(tmp_path: Path) -> None:
    """The new PIS domestic-payment starter suite should parse with fapiSigning and testValues."""
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)
    expected_method = "private_key_jwt"

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": "v4.0",
                "api": "pis",
                "profile": "fapi1-advanced",
                "suite": "pis-domestic-payment-starter",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
            "fapiSigning": {
                "certificatePathRoot": str(certificate_root),
                "signingCertificatePath": certificate_path.name,
                "signingPrivateKeyPath": private_key_path.name,
                "kid": "starter-signing-key",
                "clientAssertionIssuer": "my-client-id",
                "clientAssertionSubject": "my-client-id",
                "tokenEndpointAuthMethod": expected_method,
            },
            "testValues": {
                "profile": "ozone-demo",
                "overrides": {
                    "creditorName": "Ozone Demo Merchant",
                    "remittanceInformation": "Ozone domestic payment starter",
                },
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="pis-domestic-payment-starter",
        api="pis",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs.example.com"
    assert config.fapi_signing is not None
    assert config.fapi_signing.certificate_path_root == certificate_root
    assert config.fapi_signing.signing_certificate_path == certificate_path
    assert config.fapi_signing.signing_private_key_path == private_key_path
    assert config.fapi_signing.token_endpoint_auth_method == expected_method
    assert config.test_values is not None
    assert config.test_values.profile == "ozone-demo"
    assert dict(config.test_values.overrides) == {
        "creditorName": "Ozone Demo Merchant",
        "remittanceInformation": "Ozone domestic payment starter",
    }


@pytest.mark.unit
def test_parse_model_bank_config_accepts_fapi_signing_ob_metadata(tmp_path: Path) -> None:
    """fapiSigning.signatureIssuer and signatureTrustAnchor should be parsed into FapiSigningConfig."""
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "fapiSigning": {
                "certificatePathRoot": str(certificate_root),
                "signingCertificatePath": certificate_path.name,
                "signingPrivateKeyPath": private_key_path.name,
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
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)
    with pytest.raises(
        ConfigError,
        match="fapiSigning.signatureIssuer and fapiSigning.signatureTrustAnchor must be supplied together",
    ):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": str(certificate_root),
                    "signingCertificatePath": certificate_path.name,
                    "signingPrivateKeyPath": private_key_path.name,
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
@pytest.mark.parametrize(
    "placeholder_intent_id",
    [
        "replace-with-existing-account-access-consent-id",
        "your-existing-account-access-consent-id",
    ],
)
def test_parse_model_bank_config_rejects_psu_auth_starter_placeholder_intent_id(
    placeholder_intent_id: str,
    tmp_path: Path,
) -> None:
    """Starter suite configs must not send example consent ids to ASPSPs."""
    with pytest.raises(ConfigError, match="oauth.openBankingIntentId must be a real pre-existing account-access"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "ob-read-write",
                    "specVersion": "v4.0",
                    "profile": "fapi1-advanced",
                    "suite": "psu-auth-starter",
                },
                "oauth": {
                    "clientId": "my-client-id",
                    "redirectUri": "https://example.com/callback",
                    "openBankingIntentId": placeholder_intent_id,
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_parse_model_bank_config_accepts_ais_certification_slice_suite(
    spec_version: SuiteSpecVersion,
    tmp_path: Path,
) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": spec_version,
                "profile": "fapi1-advanced",
                "suite": "ais-certification-slice",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite="ais-certification-slice",
        api="ais",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs.example.com"


@pytest.mark.unit
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_parse_model_bank_config_accepts_ais_certification_baseline_suite(
    spec_version: SuiteSpecVersion,
    tmp_path: Path,
) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": spec_version,
                "profile": "fapi1-advanced",
                "suite": "ais-certification-baseline",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version=spec_version,
        profile="fapi1-advanced",
        suite="ais-certification-baseline",
        api="ais",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs.example.com"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_v4_ais_fcs_legacy_benchmark_suite(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": "v4.0",
                "profile": "fapi1-advanced",
                "suite": "ais-fcs-legacy-benchmark",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="ais-fcs-legacy-benchmark",
        api="ais",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs.example.com"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_pis_fcs_legacy_benchmark_suite(tmp_path: Path) -> None:
    """The v4 PIS FCS legacy benchmark suite should parse as a valid PIS suite selection."""
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": "v4.0",
                "api": "pis",
                "profile": "fapi1-advanced",
                "suite": "pis-fcs-legacy-benchmark",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="pis-fcs-legacy-benchmark",
        api="pis",
    )
    assert config.oauth is not None
    assert config.oauth.resource_base_url == "https://rs.example.com"


@pytest.mark.unit
def test_parse_model_bank_config_accepts_explicit_test_suite_api(tmp_path: Path) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": "v4.0",
                "api": "ais",
                "profile": "fapi1-advanced",
                "suite": "ais-certification-baseline",
            },
            "oauth": {
                "clientId": "my-client-id",
                "redirectUri": "https://example.com/callback",
                "resourceBaseUrl": "https://rs.example.com",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite == SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        suite="ais-certification-baseline",
        api="ais",
    )


@pytest.mark.unit
@pytest.mark.parametrize("api", ["pis", "cbpii", "vrp"])
@pytest.mark.parametrize("spec_version", ["v4.0", "v4.0.1"])
def test_parse_model_bank_config_accepts_supported_non_ais_discovery_suite(
    api: str,
    spec_version: SuiteSpecVersion,
    tmp_path: Path,
) -> None:
    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "testSuite": {
                "standard": "ob-read-write",
                "specVersion": spec_version,
                "api": api,
                "profile": "fapi1-advanced",
                "suite": "discovery-jwks",
            },
        },
        base_dir=tmp_path,
    )

    assert config.test_suite is not None
    assert config.test_suite.api == api
    assert config.test_suite.spec_version == spec_version


@pytest.mark.unit
def test_parse_model_bank_config_rejects_cvrp_until_bundled_suite_exists(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="standard=cvrp, specVersion=v4.0, api=cvrp"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "cvrp",
                    "specVersion": "v4.0",
                    "api": "cvrp",
                    "profile": "fapi1-advanced",
                    "suite": "discovery-jwks",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_v3_ais_certification_slice_suite(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="testSuite combination is not supported"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "ob-read-write",
                    "specVersion": "v3.1.11",
                    "profile": "fapi1-advanced",
                    "suite": "ais-certification-slice",
                },
                "oauth": {
                    "clientId": "my-client-id",
                    "redirectUri": "https://example.com/callback",
                    "resourceBaseUrl": "https://rs.example.com",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_v3_ais_certification_baseline_suite(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="testSuite combination is not supported"):
        parse_model_bank_config(
            {
                "environment": "ozone-model-bank",
                "discoveryUrl": "https://example.com/.well-known/openid-configuration",
                "testSuite": {
                    "standard": "ob-read-write",
                    "specVersion": "v3.1.11",
                    "profile": "fapi1-advanced",
                    "suite": "ais-certification-baseline",
                },
                "oauth": {
                    "clientId": "my-client-id",
                    "redirectUri": "https://example.com/callback",
                    "resourceBaseUrl": "https://rs.example.com",
                },
            },
            base_dir=tmp_path,
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
        ("standard", "ob-business-banking", "testSuite.standard must be one of: ob-read-write, cvrp"),
        ("specVersion", "v3.1.10", "testSuite.specVersion must be one of: v3.1.11, v4.0, v4.0.1"),
        ("api", "cards", "testSuite.api must be one of: ais, pis, cbpii, vrp, cvrp"),
        ("profile", "fapi2-security-profile", "testSuite.profile must be one of: fapi1-advanced"),
        (
            "suite",
            "full-read-write",
            (
                "testSuite.suite must be one of: discovery-jwks, psu-auth-starter, "
                "pis-domestic-payment-starter, ais-certification-slice, "
                "ais-certification-baseline, ais-fcs-legacy-benchmark"
            ),
        ),
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
def test_parse_model_bank_config_resolves_existing_certificate_root_from_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repo-relative certificate roots work when config JSON is pasted."""
    config_dir = tmp_path / "local-config" / "configs"
    cert_root = tmp_path / "local-config" / "certs"
    config_dir.mkdir(parents=True)
    cert_root.mkdir(parents=True)
    ca_bundle = cert_root / "openbanking-preprod-ca-bundle.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = parse_model_bank_config(
        {
            "environment": "ozone-model-bank",
            "discoveryUrl": "https://example.com/.well-known/openid-configuration",
            "tls": {
                "certificatePathRoot": "local-config/certs",
                "caBundlePath": "openbanking-preprod-ca-bundle.pem",
            },
        },
        base_dir=config_dir,
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
                "certificatePathRoot": certificate_root.name,
                "signingCertificatePath": certificate_path.name,
                "signingPrivateKeyPath": private_key_path.name,
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
                "certificatePathRoot": certificate_root.name,
                "signingCertificatePath": "missing.crt",
                "signingPrivateKeyPath": "missing.key",  # pragma: allowlist secret
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
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(ConfigError, match=r"Unknown fapiSigning field\(s\): jwk"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": certificate_path.name,
                    "signingPrivateKeyPath": private_key_path.name,
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
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": certificate_path.name,
                    "kid": "signing-key-001",
                    "clientAssertionIssuer": "client-issuer",
                    "clientAssertionSubject": "client-subject",
                    "tokenEndpointAuthMethod": "private_key_jwt",
                },
            },
            base_dir=tmp_path,
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_fapi_signing_path_traversal(tmp_path: Path) -> None:
    certificate_root, _, private_key_path = _write_signing_material(tmp_path)
    outside_certificate = tmp_path / "outside.crt"
    outside_certificate.write_text("certificate", encoding="utf-8")

    with pytest.raises(ConfigError, match="signingCertificatePath must resolve inside certificatePathRoot"):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": "../outside.crt",
                    "signingPrivateKeyPath": private_key_path.name,
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
    certificate_root, certificate_path, private_key_path = _write_signing_material(tmp_path)

    with pytest.raises(
        ConfigError,
        match="fapiSigning.tokenEndpointAuthMethod must be one of: private_key_jwt, tls_client_auth",
    ):
        parse_model_bank_config(
            {
                "environment": "sandbox",
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "fapiSigning": {
                    "certificatePathRoot": certificate_root.name,
                    "signingCertificatePath": certificate_path.name,
                    "signingPrivateKeyPath": private_key_path.name,
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
        "certificatePathRoot": certificate_root.name,
        "signingCertificatePath": certificate_path.name,
        "signingPrivateKeyPath": private_key_path.name,
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
