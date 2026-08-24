"""Load and validate model-bank smoke-check configuration files."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from conformance.approved_releases import (
    ApprovedReleasePolicy,
    ApprovedReleasePolicyError,
    load_approved_release_policy,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri


class ConfigError(ValueError):
    """Raised when a model-bank config file cannot be loaded or validated."""


FollowUpMode = Literal["jwks", "discovery_only"]
"""Smoke-check follow-up strategy after fetching OpenID discovery.

``"jwks"`` fetches the JWKS document referenced by ``jwks_uri``;
``"discovery_only"`` stops after the discovery document itself.
"""

TokenEndpointClientAuthMode = Literal["private_key_jwt", "tls_client_auth"]
"""Supported FAPI token-endpoint client authentication modes."""


@dataclass(frozen=True)
class OAuthConfig:
    """Narrow set of non-secret OAuth participant config values for manifest placeholders.

    Only non-secret, non-path values are permitted here. Client secrets,
    private keys, TLS paths, and certificate material must never appear in
    this dataclass. This constraint ensures that ``${config.oauth.*}``
    placeholders remain safe for use in bundled manifests distributed with
    the tool.

    Attributes:
        client_id: OAuth client identifier registered with the authorisation
            server. Not a secret; used by FAPI flows in the authorisation
            request.
        redirect_uri: HTTPS redirect URI registered with the authorisation
            server. Must use the HTTPS scheme with a valid DNS hostname.
        authorization_endpoint: Optional HTTPS authorisation endpoint override
            for environments whose client registration targets a legacy
            endpoint instead of the endpoint published by discovery.
        open_banking_intent_id: Optional pre-existing Open Banking consent id
            exposed to starter manifests as
            ``${config.oauth.openBankingIntentId}``.
        resource_base_url: Optional HTTPS AIS protected-resource base URL used
            by bundled manifests before manifest-owned Open Banking API paths.
            Callers must not include the ``/open-banking/...`` path prefix.
        issuer: Optional HTTPS issuer identifier from the OpenID Provider.
        token_endpoint: Optional HTTPS token endpoint override.
        response_type: Optional OAuth response type used for PSU
            authorisation requests.
        acr_values_supported: ACR values advertised or selected for PSU
            authorisation requests.
        request_object_signing_alg: Optional JAR request-object signing
            algorithm value.
    """

    client_id: str
    redirect_uri: str
    authorization_endpoint: str | None = None
    open_banking_intent_id: str | None = None
    resource_base_url: str | None = None
    issuer: str | None = None
    token_endpoint: str | None = None
    response_type: str | None = None
    acr_values_supported: tuple[str, ...] = ()
    request_object_signing_alg: str | None = None


@dataclass(frozen=True)
class ResourceServerConfig:
    """Resource-server defaults carried by legacy FCS-style config.

    Attributes:
        base_url: HTTPS protected-resource base URL used by catalogue API
            runtime inputs.
        x_fapi_financial_id: Optional ``x-fapi-financial-id`` header value.
        send_x_fapi_customer_ip_address: Whether resource requests should send
            ``x-fapi-customer-ip-address`` when a value is configured.
        x_fapi_customer_ip_address: Optional customer IP header value.
    """

    base_url: str | None = None
    x_fapi_financial_id: str | None = None
    send_x_fapi_customer_ip_address: bool = False
    x_fapi_customer_ip_address: str | None = None


@dataclass(frozen=True)
class ClientCredentialsConfig:
    """Secret client-auth values preserved outside safe OAuth placeholders.

    Attributes:
        client_secret: Optional OAuth client secret for legacy
            client-secret-based flows. The current FAPI runner does not expose
            this through ``${config.oauth.*}`` placeholders.
    """

    client_secret: str | None = None


@dataclass(frozen=True)
class OpenBankingSignatureConfig:
    """Open Banking detached-signature metadata from legacy FCS config.

    Attributes:
        tpp_signature_issuer: Optional Open Banking organisation or SSA issuer
            value used by detached JWS profiles.
        tpp_signature_tan: Optional trust anchor name used by detached JWS
            profiles.
    """

    tpp_signature_issuer: str | None = None
    tpp_signature_tan: str | None = None


@dataclass(frozen=True)
class TlsConfig:
    """Transport TLS file paths for outbound model-bank requests.

    Attributes:
        ca_bundle_path: Optional CA bundle used to verify the model bank.
        client_certificate_path: Optional client certificate for mTLS.
        client_private_key_path: Optional private key paired with the client certificate.
    """

    ca_bundle_path: Path | None = None
    client_certificate_path: Path | None = None
    client_private_key_path: Path | None = None


@dataclass(frozen=True)
class FapiSigningConfig:
    """FAPI signing and token client-auth configuration kept out of placeholders.

    Attributes:
        certificate_path_root: Root directory under which signing certificate
            and private-key paths must resolve.
        signing_certificate_path: X.509 certificate path used for PS256 JOSE
            signing operations such as request objects and private-key JWT
            client assertions.
        signing_private_key_path: Private key path paired with
            ``signing_certificate_path``.
        key_id: JOSE ``kid`` header value associated with the signing key.
        client_assertion_issuer: ``iss`` claim value for token-endpoint
            client assertions.
        client_assertion_subject: ``sub`` claim value for token-endpoint
            client assertions.
        token_endpoint_auth_method: Declared client-authentication method for
            the token endpoint.
    """

    certificate_path_root: Path
    signing_certificate_path: Path
    signing_private_key_path: Path
    key_id: str
    client_assertion_issuer: str
    client_assertion_subject: str
    token_endpoint_auth_method: TokenEndpointClientAuthMode


@dataclass(frozen=True)
class BusinessDefaultsConfig:
    """Business-domain defaults used to derive request/runtime values.

    Attributes:
        ais: AIS resource ids and transaction-filter defaults.
        pis: Payment account, amount, execution date, and standing-order
            defaults.
        cbpii: CBPII debtor-account defaults.
        conditional_properties: Optional or conditional request-property
            selections carried by the plan config.
    """

    ais: JsonObject = field(default_factory=dict)
    pis: JsonObject = field(default_factory=dict)
    cbpii: JsonObject = field(default_factory=dict)
    conditional_properties: tuple[JsonValue, ...] = ()


@dataclass(frozen=True)
class ModelBankConfig:
    """Validated inputs needed to run the current model-bank smoke check.

    Attributes:
        discovery_url: HTTPS OpenID Provider discovery document URL.
        timeout_seconds: Per-request timeout for model-bank HTTP calls.
        follow_up_mode: Whether to fetch JWKS after discovery succeeds.
        tls: Transport TLS settings for the HTTP client.
        result_output_path: Path where the structured JSON result should be written.
        execution_log_path: Path where the NDJSON execution log should be
            written. Defaults to ``out/execution-log.ndjson`` resolved under
            the output base directory (typically the process CWD),
            independently of ``result_output_path``.
        approved_release_policy: Optional approved-release policy used for
            participant-side report eligibility self-assessment. When absent,
            generated reports mark the approved-release criterion as not
            supplied.
        oauth: Optional narrow OAuth participant config for
            ``${config.oauth.*}`` placeholder resolution. Contains only
            non-secret values (``clientId``, ``redirectUri``, optional
            ``authorizationEndpoint``, optional ``openBankingIntentId``, and
            optional ``resourceBaseUrl``).
            Absent when the participant config omits an ``oauth`` section.
        fapi_signing: Optional FAPI signing and client-auth configuration kept
            outside the runtime placeholder allow-list. Contains signing key
            metadata and filesystem paths resolved under the configured
            certificate root.
        resource_server: Optional resource-server target/header defaults used
            by catalogue-driven resource calls.
        client_credentials: Optional secret client-auth values preserved
            outside safe placeholder resolution.
        open_banking: Optional Open Banking detached-signature metadata.
        business_defaults: AIS, PIS, CBPII, and conditional-property defaults
            used by the v2 builder and catalogue runtime derivation.
    """

    discovery_url: str
    timeout_seconds: float = 10.0
    follow_up_mode: FollowUpMode = "jwks"
    tls: TlsConfig = field(default_factory=TlsConfig)
    result_output_path: Path = Path("out/test-results.json")
    execution_log_path: Path = Path("out/execution-log.ndjson")
    approved_release_policy: ApprovedReleasePolicy | None = None
    oauth: OAuthConfig | None = None
    fapi_signing: FapiSigningConfig | None = None
    resource_server: ResourceServerConfig | None = None
    client_credentials: ClientCredentialsConfig | None = None
    open_banking: OpenBankingSignatureConfig | None = None
    business_defaults: BusinessDefaultsConfig = field(default_factory=BusinessDefaultsConfig)


def load_model_bank_config(config_path: Path) -> ModelBankConfig:
    """Load a model-bank JSON config file from disk.

    Args:
        config_path: Path to the JSON config file.

    Returns:
        Parsed and validated model-bank config.

    Raises:
        ConfigError: If the file cannot be read, parsed, or validated.
    """
    resolved_config_path = config_path.resolve()
    try:
        raw_config = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON config: {error.msg}") from error
    except OSError as error:
        raise ConfigError(f"Unable to read config file: {error}") from error

    if not isinstance(raw_config, dict):
        raise ConfigError("Config root must be a JSON object")

    return parse_model_bank_config(
        raw_config,
        base_dir=resolved_config_path.parent,
        output_base_dir=Path.cwd(),
    )


def parse_model_bank_config(
    raw_config: dict[str, JsonValue],
    *,
    base_dir: Path,
    output_base_dir: Path | None = None,
) -> ModelBankConfig:
    """Validate raw JSON config data into a typed model-bank config.

    Args:
        raw_config: JSON object loaded from a model-bank config file.
        base_dir: Directory used to resolve certificate and approved-release
            policy paths.
        output_base_dir: Directory used to resolve result output paths.

    Returns:
        Typed model-bank config ready for execution.

    Raises:
        ConfigError: If required fields are missing, unknown fields are present,
            paths are unsafe, or values have invalid types.
    """
    _reject_unknown_keys(
        raw_config,
        allowed_keys={
            "environment",
            "discoveryUrl",
            "timeoutSeconds",
            "followUp",
            "tls",
            "fapiSigning",
            "resultOutputPath",
            "executionLogPath",
            "approvedReleasePolicyPath",
            "oauth",
            "resourceServer",
            "clientCredentials",
            "openBanking",
            "ais",
            "pis",
            "cbpii",
            "conditionalProperties",
        },
        location="config",
    )

    discovery_url = _required_https_url(raw_config, "discoveryUrl")
    timeout_seconds = _optional_positive_number(raw_config, "timeoutSeconds", default=10.0)
    follow_up_mode = _parse_follow_up(raw_config)
    tls = _parse_tls_config(raw_config, base_dir=base_dir)
    result_output_path = _optional_path(
        raw_config,
        "resultOutputPath",
        base_dir=output_base_dir or Path.cwd(),
        default=Path("out/test-results.json"),
    )
    execution_log_path = _optional_path(
        raw_config,
        "executionLogPath",
        base_dir=output_base_dir or Path.cwd(),
        default=Path("out/execution-log.ndjson"),
    )
    approved_release_policy = _optional_approved_release_policy(raw_config, root=base_dir)
    oauth = _parse_oauth_config(raw_config)
    fapi_signing = _parse_fapi_signing_config(raw_config, base_dir=base_dir)
    resource_server = _parse_resource_server_config(raw_config)
    client_credentials = _parse_client_credentials_config(raw_config)
    open_banking = _parse_open_banking_config(raw_config)
    business_defaults = _parse_business_defaults_config(raw_config)

    return ModelBankConfig(
        discovery_url=discovery_url,
        timeout_seconds=timeout_seconds,
        follow_up_mode=follow_up_mode,
        tls=tls,
        result_output_path=result_output_path,
        execution_log_path=execution_log_path,
        approved_release_policy=approved_release_policy,
        oauth=oauth,
        fapi_signing=fapi_signing,
        resource_server=resource_server,
        client_credentials=client_credentials,
        open_banking=open_banking,
        business_defaults=business_defaults,
    )


def _optional_approved_release_policy(raw_config: dict[str, JsonValue], *, root: Path) -> ApprovedReleasePolicy | None:
    """Load an optional approved-release policy referenced by config.

    The policy path is resolved under ``root`` and must point to an existing
    file. This keeps participant-controlled paths inside the configuration
    boundary used by the caller: the config file directory for CLI loads, or
    the process CWD for API/UI JSON parsing.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.
        root: Directory that the resolved policy path must reside inside.

    Returns:
        Parsed approved-release policy, or ``None`` when the config omits
        ``approvedReleasePolicyPath``.

    Raises:
        ConfigError: If the supplied path is not a non-empty string, escapes
            ``root``, does not point to an existing file, or the policy JSON
            is malformed.
    """
    value = raw_config.get("approvedReleasePolicyPath")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("approvedReleasePolicyPath must be a non-empty string when supplied")

    raw_path = Path(value.strip())
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    resolved_root = root.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ConfigError("approvedReleasePolicyPath must resolve inside the config root")
    if not resolved_path.is_file():
        raise ConfigError("approvedReleasePolicyPath must point to an existing file")
    try:
        return load_approved_release_policy(resolved_path)
    except ApprovedReleasePolicyError as error:
        raise ConfigError(f"Invalid approved-release policy: {error}") from error


def _parse_oauth_config(raw_config: dict[str, JsonValue]) -> OAuthConfig | None:
    """Parse the optional ``oauth`` section of a participant config.

    Only the safe, non-secret fields ``clientId``, ``redirectUri``, and the
    optional ``authorizationEndpoint``, ``openBankingIntentId``, and
    ``resourceBaseUrl`` are accepted. Client secrets, private keys, TLS
    paths, and JWS signing material are explicitly excluded from this
    boundary; adding them here would expose credential material through
    ``${config.oauth.*}`` placeholders in bundled manifests.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed ``OAuthConfig``, or ``None`` when the config omits the
        ``oauth`` section.

    Raises:
        ConfigError: If ``oauth`` is not a JSON object, contains unknown
            keys, omits required fields, or one of the URL fields is not a
            valid HTTPS URL.
    """
    raw_oauth = raw_config.get("oauth")
    if raw_oauth is None:
        return None
    if not isinstance(raw_oauth, dict):
        raise ConfigError("oauth must be a JSON object")

    _reject_unknown_keys(
        raw_oauth,
        allowed_keys={
            "clientId",
            "redirectUri",
            "authorizationEndpoint",
            "issuer",
            "tokenEndpoint",
            "openBankingIntentId",
            "resourceBaseUrl",
            "responseType",
            "acrValuesSupported",
            "requestObjectSigningAlg",
        },
        location="oauth",
    )

    client_id = _required_string_at(raw_oauth, "clientId", location="oauth")
    redirect_uri_str = _required_string_at(raw_oauth, "redirectUri", location="oauth")
    authorization_endpoint = _optional_https_url_at(raw_oauth, "authorizationEndpoint", location="oauth")
    issuer = _optional_https_url_at(raw_oauth, "issuer", location="oauth")
    token_endpoint = _optional_https_url_at(raw_oauth, "tokenEndpoint", location="oauth")
    open_banking_intent_id = _optional_string_at(raw_oauth, "openBankingIntentId", location="oauth")
    resource_base_url = _optional_https_url_at(raw_oauth, "resourceBaseUrl", location="oauth")
    response_type = _optional_string_at(raw_oauth, "responseType", location="oauth")
    acr_values_supported = _optional_string_array_at(raw_oauth, "acrValuesSupported", location="oauth")
    request_object_signing_alg = _optional_string_at(raw_oauth, "requestObjectSigningAlg", location="oauth")
    try:
        validate_oauth_redirect_uri(redirect_uri_str, label="oauth.redirectUri")
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error

    return OAuthConfig(
        client_id=client_id,
        redirect_uri=redirect_uri_str,
        authorization_endpoint=authorization_endpoint,
        open_banking_intent_id=open_banking_intent_id,
        resource_base_url=resource_base_url,
        issuer=issuer,
        token_endpoint=token_endpoint,
        response_type=response_type,
        acr_values_supported=acr_values_supported,
        request_object_signing_alg=request_object_signing_alg,
    )


def _parse_resource_server_config(raw_config: dict[str, JsonValue]) -> ResourceServerConfig | None:
    """Parse optional protected-resource target and header defaults.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed resource-server defaults, or ``None`` when the section is
        omitted.

    Raises:
        ConfigError: If ``resourceServer`` is not a JSON object, contains
            unknown keys, or contains invalid field values.
    """
    raw_resource_server = raw_config.get("resourceServer")
    if raw_resource_server is None:
        return None
    if not isinstance(raw_resource_server, dict):
        raise ConfigError("resourceServer must be a JSON object")
    _reject_unknown_keys(
        raw_resource_server,
        allowed_keys={
            "baseUrl",
            "xFapiFinancialId",
            "sendXFapiCustomerIpAddress",
            "xFapiCustomerIpAddress",
        },
        location="resourceServer",
    )

    return ResourceServerConfig(
        base_url=_optional_https_url_at(raw_resource_server, "baseUrl", location="resourceServer"),
        x_fapi_financial_id=_optional_string_at(
            raw_resource_server,
            "xFapiFinancialId",
            location="resourceServer",
        ),
        send_x_fapi_customer_ip_address=_optional_boolean_at(
            raw_resource_server,
            "sendXFapiCustomerIpAddress",
            location="resourceServer",
            default=False,
        ),
        x_fapi_customer_ip_address=_optional_string_at(
            raw_resource_server,
            "xFapiCustomerIpAddress",
            location="resourceServer",
        ),
    )


def _parse_client_credentials_config(raw_config: dict[str, JsonValue]) -> ClientCredentialsConfig | None:
    """Parse optional secret client-credential values.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed client credentials, or ``None`` when the section is omitted.

    Raises:
        ConfigError: If ``clientCredentials`` is not a JSON object, contains
            unknown keys, or contains invalid field values.
    """
    raw_credentials = raw_config.get("clientCredentials")
    if raw_credentials is None:
        return None
    if not isinstance(raw_credentials, dict):
        raise ConfigError("clientCredentials must be a JSON object")
    _reject_unknown_keys(raw_credentials, allowed_keys={"clientSecret"}, location="clientCredentials")

    return ClientCredentialsConfig(
        client_secret=_optional_string_at(raw_credentials, "clientSecret", location="clientCredentials")
    )


def _parse_open_banking_config(raw_config: dict[str, JsonValue]) -> OpenBankingSignatureConfig | None:
    """Parse optional Open Banking signature metadata.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed Open Banking metadata, or ``None`` when the section is omitted.

    Raises:
        ConfigError: If ``openBanking`` is not a JSON object, contains unknown
            keys, or contains invalid field values.
    """
    raw_open_banking = raw_config.get("openBanking")
    if raw_open_banking is None:
        return None
    if not isinstance(raw_open_banking, dict):
        raise ConfigError("openBanking must be a JSON object")
    _reject_unknown_keys(
        raw_open_banking,
        allowed_keys={"tppSignatureIssuer", "tppSignatureTan"},
        location="openBanking",
    )

    return OpenBankingSignatureConfig(
        tpp_signature_issuer=_optional_string_at(
            raw_open_banking,
            "tppSignatureIssuer",
            location="openBanking",
        ),
        tpp_signature_tan=_optional_string_at(raw_open_banking, "tppSignatureTan", location="openBanking"),
    )


def _parse_business_defaults_config(raw_config: dict[str, JsonValue]) -> BusinessDefaultsConfig:
    """Parse AIS, PIS, CBPII, and conditional-property defaults.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed business defaults. Omitted sections are represented as empty
        objects or tuples.

    Raises:
        ConfigError: If a business-default section has the wrong JSON type.
    """
    return BusinessDefaultsConfig(
        ais=_optional_json_object(raw_config, "ais", location="config"),
        pis=_optional_json_object(raw_config, "pis", location="config"),
        cbpii=_optional_json_object(raw_config, "cbpii", location="config"),
        conditional_properties=_optional_json_array(raw_config, "conditionalProperties", location="config"),
    )


def _parse_fapi_signing_config(raw_config: dict[str, JsonValue], *, base_dir: Path) -> FapiSigningConfig | None:
    """Parse the optional ``fapiSigning`` section of a participant config.

    This section is intentionally separate from ``oauth`` so bundled manifest
    placeholders cannot traverse into signing paths, JOSE metadata, or future
    client-auth credentials.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.
        base_dir: Directory of the config file, used as the default
            ``certificatePathRoot`` for signing material.

    Returns:
        Parsed ``FapiSigningConfig``, or ``None`` when the config omits the
        ``fapiSigning`` section.

    Raises:
        ConfigError: If ``fapiSigning`` is not a JSON object, contains unknown
            keys, omits required values, specifies an unsupported token-endpoint
            auth method, or points to paths that escape ``certificatePathRoot``.
    """
    raw_fapi_signing = raw_config.get("fapiSigning")
    if raw_fapi_signing is None:
        return None
    if not isinstance(raw_fapi_signing, dict):
        raise ConfigError("fapiSigning must be a JSON object")

    _reject_unknown_keys(
        raw_fapi_signing,
        allowed_keys={
            "certificatePathRoot",
            "signingCertificatePath",
            "signingPrivateKeyPath",
            "kid",
            "clientAssertionIssuer",
            "clientAssertionSubject",
            "tokenEndpointAuthMethod",
        },
        location="fapiSigning",
    )

    certificate_root = _optional_path(
        raw_fapi_signing,
        "certificatePathRoot",
        base_dir=base_dir,
        default=base_dir,
    )
    signing_certificate_path = _optional_child_path(
        raw_fapi_signing,
        "signingCertificatePath",
        root=certificate_root,
    )
    signing_private_key_path = _optional_child_path(
        raw_fapi_signing,
        "signingPrivateKeyPath",
        root=certificate_root,
    )
    if signing_certificate_path is None or signing_private_key_path is None:
        raise ConfigError(
            "fapiSigning.signingCertificatePath and fapiSigning.signingPrivateKeyPath must be supplied together"
        )

    key_id = _required_string_at(raw_fapi_signing, "kid", location="fapiSigning")
    client_assertion_issuer = _required_string_at(raw_fapi_signing, "clientAssertionIssuer", location="fapiSigning")
    client_assertion_subject = _required_string_at(raw_fapi_signing, "clientAssertionSubject", location="fapiSigning")
    token_endpoint_auth_method = _required_string_at(
        raw_fapi_signing,
        "tokenEndpointAuthMethod",
        location="fapiSigning",
    )
    if token_endpoint_auth_method not in {"private_key_jwt", "tls_client_auth"}:
        raise ConfigError("fapiSigning.tokenEndpointAuthMethod must be one of: private_key_jwt, tls_client_auth")

    return FapiSigningConfig(
        certificate_path_root=certificate_root,
        signing_certificate_path=signing_certificate_path,
        signing_private_key_path=signing_private_key_path,
        key_id=key_id,
        client_assertion_issuer=client_assertion_issuer,
        client_assertion_subject=client_assertion_subject,
        token_endpoint_auth_method=cast(TokenEndpointClientAuthMode, token_endpoint_auth_method),
    )


def _parse_follow_up(raw_config: dict[str, JsonValue]) -> FollowUpMode:
    """Parse the optional ``followUp`` section of a model bank config dict.

    If the key is absent, the default follow-up mode ``"jwks"`` is returned.
    The only currently supported modes are ``"jwks"`` and
    ``"discovery_only"``.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file.

    Returns:
        The resolved ``FollowUpMode`` value.

    Raises:
        ConfigError: If ``followUp`` is present but not a JSON object, contains
            unknown keys, or specifies an unrecognised mode.
    """
    raw_follow_up = raw_config.get("followUp")
    if raw_follow_up is None:
        return "jwks"
    if not isinstance(raw_follow_up, dict):
        raise ConfigError("followUp must be a JSON object")
    _reject_unknown_keys(raw_follow_up, allowed_keys={"mode"}, location="followUp")
    mode = _required_string(raw_follow_up, "mode")
    if mode == "jwks":
        return "jwks"
    if mode == "discovery_only":
        return "discovery_only"
    raise ConfigError("followUp.mode must be one of: jwks, discovery_only")


def _parse_tls_config(raw_config: dict[str, JsonValue], *, base_dir: Path) -> TlsConfig:
    """Parse the optional ``tls`` section of a model bank config dict.

    If the key is absent a zero-value ``TlsConfig`` (no custom TLS) is
    returned.  Relative certificate paths are resolved against ``base_dir``.
    ``clientCertificatePath`` and ``clientPrivateKeyPath`` must be supplied
    together or not at all.

    Args:
        raw_config: Top-level raw configuration dictionary.
        base_dir: Directory of the config file, used as the root for resolving
            relative certificate paths.

    Returns:
        A populated ``TlsConfig`` dataclass.

    Raises:
        ConfigError: If ``tls`` is not a JSON object, contains unknown keys,
            specifies paths that escape ``certificatePathRoot``, specifies
            paths that do not exist, or supplies only one of the client
            certificate / private key pair.
    """
    raw_tls = raw_config.get("tls")
    if raw_tls is None:
        return TlsConfig()
    if not isinstance(raw_tls, dict):
        raise ConfigError("tls must be a JSON object")

    _reject_unknown_keys(
        raw_tls,
        allowed_keys={"certificatePathRoot", "caBundlePath", "clientCertificatePath", "clientPrivateKeyPath"},
        location="tls",
    )

    certificate_root = _optional_path(raw_tls, "certificatePathRoot", base_dir=base_dir, default=base_dir)
    ca_bundle_path = _optional_existing_child_path(raw_tls, "caBundlePath", root=certificate_root)
    client_certificate_path = _optional_existing_child_path(raw_tls, "clientCertificatePath", root=certificate_root)
    client_private_key_path = _optional_existing_child_path(raw_tls, "clientPrivateKeyPath", root=certificate_root)

    if (client_certificate_path is None) != (client_private_key_path is None):
        raise ConfigError("clientCertificatePath and clientPrivateKeyPath must be supplied together")

    return TlsConfig(
        ca_bundle_path=ca_bundle_path,
        client_certificate_path=client_certificate_path,
        client_private_key_path=client_private_key_path,
    )


def _required_string(raw_config: dict[str, JsonValue], key: str) -> str:
    """Extract a required non-empty string value from a raw config dict.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a non-empty string.

    Returns:
        The stripped string value.

    Raises:
        ConfigError: If the key is missing, the value is not a string, or the
            string is blank after stripping whitespace.
    """
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def _required_string_at(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string value from a nested config dict.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a non-empty string.
        location: Dot-path prefix used in validation error messages.

    Returns:
        The stripped string value.

    Raises:
        ConfigError: If the key is missing, the value is not a string, or the
            string is blank after stripping whitespace.
    """
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _required_https_url(raw_config: dict[str, JsonValue], key: str) -> str:
    """Extract and validate a required HTTPS URL from raw config.

    Args:
        raw_config: Raw configuration dictionary.
        key: Configuration key to extract.

    Returns:
        Validated HTTPS URL string.

    Raises:
        ConfigError: If the value is missing, empty, or not a valid HTTPS URL.
    """
    value = _required_string(raw_config, key)
    try:
        validate_https_url(value, label=key)
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error
    return value


def _optional_https_url_at(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str | None:
    """Extract and validate an optional HTTPS URL from a nested config dict.

    Args:
        raw_config: Raw nested configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.

    Returns:
        The validated HTTPS URL string, or ``None`` when the key is absent.

    Raises:
        ConfigError: If the key is present but not a non-empty string or not a
            valid HTTPS URL.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")

    stripped_value = value.strip()
    try:
        validate_https_url(stripped_value, label=f"{location}.{key}")
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error
    return stripped_value


def _optional_string_at(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str | None:
    """Extract an optional non-empty string value from a nested config dict.

    Args:
        raw_config: Raw nested configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.

    Returns:
        The stripped string value, or ``None`` when the key is absent.

    Raises:
        ConfigError: If the key is present but not a non-empty string.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_boolean_at(
    raw_config: dict[str, JsonValue],
    key: str,
    *,
    location: str,
    default: bool,
) -> bool:
    """Extract an optional boolean value from a nested config dict.

    Args:
        raw_config: Raw nested configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.
        default: Value to return when the key is absent.

    Returns:
        The extracted boolean value, or ``default`` when the key is absent.

    Raises:
        ConfigError: If the key is present but is not a JSON boolean.
    """
    value = raw_config.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be a boolean")
    return value


def _optional_string_array_at(raw_config: dict[str, JsonValue], key: str, *, location: str) -> tuple[str, ...]:
    """Extract an optional array of non-empty strings from a nested config dict.

    Args:
        raw_config: Raw nested configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.

    Returns:
        Tuple of stripped string values, or an empty tuple when the key is
        absent.

    Raises:
        ConfigError: If the key is present but is not an array of non-empty
            strings.
    """
    value = raw_config.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{location}.{key} must be a JSON array")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{location}.{key}[{index}] must be a non-empty string")
        values.append(item.strip())
    return tuple(values)


def _optional_json_object(raw_config: dict[str, JsonValue], key: str, *, location: str) -> JsonObject:
    """Extract an optional JSON object section.

    Args:
        raw_config: Raw configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.

    Returns:
        A deep-copied JSON object, or an empty object when the key is absent.

    Raises:
        ConfigError: If the key is present but is not a JSON object.
    """
    value = raw_config.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{location}.{key} must be a JSON object")
    return copy.deepcopy(value)


def _optional_json_array(raw_config: dict[str, JsonValue], key: str, *, location: str) -> tuple[JsonValue, ...]:
    """Extract an optional JSON array section.

    Args:
        raw_config: Raw configuration dictionary.
        key: Configuration key to extract.
        location: Dot-path prefix used in validation error messages.

    Returns:
        A deep-copied tuple of JSON values, or an empty tuple when the key is
        absent.

    Raises:
        ConfigError: If the key is present but is not a JSON array.
    """
    value = raw_config.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{location}.{key} must be a JSON array")
    return tuple(copy.deepcopy(item) for item in value)


def _optional_positive_number(raw_config: dict[str, JsonValue], key: str, *, default: float) -> float:
    """Extract an optional positive finite number from a raw config dict.

    If the key is absent, ``default`` is returned unchanged.  ``bool``
    values are explicitly rejected even though they are a subtype of ``int``
    in Python.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a positive finite number.
        default: Value to return when the key is absent.

    Returns:
        The extracted number as a ``float``, or ``default``.

    Raises:
        ConfigError: If the value is present but is not a positive finite
            number (including bool, negative, zero, or non-finite).
    """
    value = raw_config.get(key)
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return float(value)


def _optional_path(raw_config: dict[str, JsonValue], key: str, *, base_dir: Path, default: Path) -> Path:
    """Extract an optional filesystem path, resolving relative paths safely.

    If the key is absent, ``default`` is resolved against ``base_dir`` (or
    returned as-is if already absolute). If present, an absolute value is used
    as-is. A relative value is normally resolved against ``base_dir``; when
    that path does not exist but the same relative path exists under the
    current working directory, the working-directory path is used instead.
    This lets browser-pasted configs use repo-relative certificate roots while
    file-loaded configs can still use paths relative to their own location.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.
        base_dir: Directory used as the base for resolving relative paths.
        default: Path to return when the key is absent.

    Returns:
        Absolute resolved ``Path``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string.
    """
    value = raw_config.get(key)
    if value is None:
        return (base_dir / default).resolve() if not default.is_absolute() else default.resolve()
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")
    path = Path(value.strip())
    if path.is_absolute():
        return path.resolve()
    base_relative_path = (base_dir / path).resolve()
    cwd_relative_path = (Path.cwd() / path).resolve()
    if not base_relative_path.exists() and cwd_relative_path.exists():
        return cwd_relative_path
    return base_relative_path


def _optional_existing_child_path(raw_config: dict[str, JsonValue], key: str, *, root: Path) -> Path | None:
    """Extract an optional path that must resolve inside ``root`` and point to an existing file.

    Enforces a path-traversal guard: the resolved path must be ``root`` itself
    or a descendant of ``root``.  Both absolute and relative path strings are
    accepted; relative paths are resolved against ``root``.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.
        root: Directory that the resolved path must reside inside (the
            ``certificatePathRoot``).

    Returns:
        Absolute resolved ``Path`` when the key is present, or ``None``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string, the resolved path escapes ``root``, or the path does not
            point to an existing file.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")

    raw_path = Path(value.strip())
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    resolved_root = root.resolve()

    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ConfigError(f"{key} must resolve inside certificatePathRoot")
    if not resolved_path.is_file():
        raise ConfigError(f"{key} must point to an existing file")
    return resolved_path


def _optional_child_path(raw_config: dict[str, JsonValue], key: str, *, root: Path) -> Path | None:
    """Extract an optional path that must resolve inside ``root``.

    Unlike :func:`_optional_existing_child_path`, this helper does not touch
    the filesystem. It is used for config sections whose file existence and PEM
    validity must be deferred until execution time so config parsing can remain
    placeholder-safe without eagerly loading signing material.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.
        root: Directory that the resolved path must reside inside.

    Returns:
        Absolute resolved ``Path`` when the key is present, or ``None``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string or the resolved path escapes ``root``.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")

    raw_path = Path(value.strip())
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    resolved_root = root.resolve()

    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ConfigError(f"{key} must resolve inside certificatePathRoot")
    return resolved_path


def _reject_unknown_keys(raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Raise ``ConfigError`` if ``raw_config`` contains any key not in ``allowed_keys``.

    This prevents silently ignoring typo'd or unsupported configuration fields
    that would otherwise be swallowed without effect.

    Args:
        raw_config: Raw configuration dictionary to validate.
        allowed_keys: Set of recognised field names.
        location: Human-readable config path used in the error message
            (e.g. ``"tls"`` or ``"followUp"``).

    Raises:
        ConfigError: If one or more unrecognised keys are present, listing
            them in sorted order.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ConfigError(f"Unknown {location} field(s): {joined_keys}")
