"""Load and validate model-bank smoke-check configuration files."""

from __future__ import annotations

import json
import math
import os
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from conformance.approved_releases import (
    ApprovedReleasePolicy,
    ApprovedReleasePolicyError,
    load_approved_release_policy,
)
from conformance.dcr.credentials import DcrCredentialPaths
from conformance.dcr.transport import DcrTokenEndpointAuthMethod, DcrTransportConfig
from conformance.json_types import JsonValue
from conformance.target_config import TestTargetConfig, TestTargetConfigError, parse_test_target_config
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

_TEST_VALUES_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
"""Pattern for valid test-value key names accepted in ``testValues.overrides``."""

_DCR_TLS_SKIP_VERIFY_WARNING = (
    "dcr.tlsSkipVerify is enabled: TLS server certificate verification is "
    "disabled. This is unsafe and must never be used against real ASPSP "
    "infrastructure or in certification runs."
)
"""Warning text emitted when ``dcr.tlsSkipVerify`` is ``True`` in a participant config."""


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
    """

    client_id: str
    redirect_uri: str
    authorization_endpoint: str | None = None
    open_banking_intent_id: str | None = None
    resource_base_url: str | None = None


@dataclass(frozen=True)
class TlsConfig:
    """Transport TLS file paths for outbound model-bank requests.

    Attributes:
        ca_bundle_path: Optional participant-supplied CA bundle appended to the
            default system roots and bundled Open Banking CA roots.
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
        certificate_path_root: Deprecated internal compatibility root derived
            from the exact signing certificate and private-key paths.
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
        signature_issuer: Optional Open Banking detached-JWS issuer identifier
            placed in the ``http://openbanking.org.uk/iss`` JOSE protected
            header when signing PIS/AIS write requests. Required together with
            ``signature_trust_anchor``; omit both to use the minimal header.
        signature_trust_anchor: Optional Open Banking trust-anchor domain
            placed in the ``http://openbanking.org.uk/tan`` JOSE protected
            header. Required together with ``signature_issuer``; omit both to
            use the minimal header.
    """

    certificate_path_root: Path
    signing_certificate_path: Path
    signing_private_key_path: Path
    key_id: str
    client_assertion_issuer: str
    client_assertion_subject: str
    token_endpoint_auth_method: TokenEndpointClientAuthMode
    signature_issuer: str | None = None
    signature_trust_anchor: str | None = None


@dataclass(frozen=True)
class TestValuesConfig:
    """Participant-supplied test-value profile selection and overrides.

    Controls which named profile the executor uses when resolving
    ``${testValues.<key>}`` placeholders and which individual key values the
    participant overrides relative to that profile's defaults.

    Security boundary: only string key/value pairs are accepted. The key set
    is validated against ``testValueProfiles.allowedOverrideKeys`` declared in
    the manifest where possible, but config-level parsing cannot always perform
    this cross-validation because the manifest is loaded separately. Callers
    that have both the config and the manifest must perform the cross-validation
    explicitly.

    Attributes:
        profile: Optional profile identifier to select. When ``None`` the
            manifest's declared ``defaultProfileId`` is used. Must match one of
            the profile ids declared in the manifest's ``testValueProfiles``.
        overrides: Immutable mapping of key names to override string values.
            Each key must match the test-value key pattern and must be listed in
            the manifest's ``testValueProfiles.allowedOverrideKeys``.
    """

    profile: str | None
    overrides: Mapping[str, str]


@dataclass(frozen=True)
class TestDataConfig:
    """Participant-supplied test data values for selected suite execution.

    Provides environment/persona/payment values used to make a run executable
    against a target ASPSP. These override the suite's generic baseline values
    where present and allowed.

    Keys must match entries in the suite manifest's ``testValues.allowedCustomKeys``.
    Same-as-baseline values are normalised away and treated as absent.

    Attributes:
        values: Immutable mapping of test-data key names to string values.
    """

    values: Mapping[str, str]


@dataclass(frozen=True)
class DcrConfig:
    """DCR-specific credential and transport configuration.

    Packages validated DCR credential file paths together with mTLS and
    token-endpoint transport options.  Both sub-objects are validated by
    :func:`_parse_dcr_config` before this dataclass is constructed.

    Attributes:
        credential_paths: Validated file-backed DCR credential paths.
        transport: mTLS transport and token-endpoint auth options.
    """

    credential_paths: DcrCredentialPaths
    transport: DcrTransportConfig


@dataclass(frozen=True)
class ModelBankConfig:
    """Validated inputs needed to run the current model-bank smoke check.

    Attributes:
        environment: Optional legacy human-readable environment name. Targeted
            participant configs no longer require or export it.
        discovery_url: HTTPS OpenID Provider discovery document URL.
        timeout_seconds: Per-request timeout for model-bank HTTP calls.
        follow_up_mode: Whether to fetch JWKS after discovery succeeds.
        tls: Transport TLS settings for the HTTP client.
        result_output_path: Path where the structured JSON result should be written.
        execution_log_path: Path where the NDJSON execution log should be
            written. Defaults to ``out/execution-log.ndjson`` resolved under
            the output base directory (typically the process CWD),
            independently of ``result_output_path``.
        test_target: Optional target-oriented conformance selection describing
            what the participant intends to test (standard, specification,
            security profile, version, and resource groups).  Specified via the
            ``testTarget`` key in participant config JSON.  ``None`` when the
            config omits ``testTarget``.
        dcr: Optional DCR credential and transport configuration.  Required
            for Dynamic Client Registration runs; ``None`` for Read/Write and
            smoke-check runs.
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
            exact absolute credential paths.
        test_values: Optional participant test-value profile selection and key
            overrides. When absent, the manifest's default profile is used with
            no overrides.
        test_data: Optional participant test-data values keyed by the suite
            manifest's ``testValues.allowedCustomKeys`` contract. When absent,
            execution falls back to the suite manifest baseline values.
        open_banking: Optional Open Banking institution metadata, including the
            ``x-fapi-financial-id`` header value injected for resource-server
            write requests. When absent, no financial-id header is added.
    """

    environment: str | None
    discovery_url: str
    timeout_seconds: float = 10.0
    follow_up_mode: FollowUpMode = "jwks"
    tls: TlsConfig = field(default_factory=TlsConfig)
    result_output_path: Path = Path("out/test-results.json")
    execution_log_path: Path = Path("out/execution-log.ndjson")
    test_target: TestTargetConfig | None = None
    dcr: DcrConfig | None = None
    approved_release_policy: ApprovedReleasePolicy | None = None
    oauth: OAuthConfig | None = None
    fapi_signing: FapiSigningConfig | None = None
    test_values: TestValuesConfig | None = None
    test_data: TestDataConfig | None = None
    open_banking: OpenBankingConfig | None = None


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
            "testTarget",
            "dcr",
            "approvedReleasePolicyPath",
            "oauth",
            "testValues",
            "testData",
            "openBanking",
        },
        location="config",
    )

    environment = _optional_string_at(raw_config, "environment", location="config")
    discovery_url = _required_https_url(raw_config, "discoveryUrl")
    timeout_seconds = _optional_positive_number(raw_config, "timeoutSeconds", default=10.0)
    follow_up_mode = _parse_follow_up(raw_config)
    tls = _parse_tls_config(raw_config)
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
    test_target = _parse_test_target_from_config(raw_config)
    dcr = _parse_dcr_config(raw_config, base_dir=base_dir)
    approved_release_policy = _optional_approved_release_policy(raw_config, root=base_dir)
    oauth = _parse_oauth_config(raw_config)
    fapi_signing = _parse_fapi_signing_config(raw_config, base_dir=base_dir)
    test_values = _parse_test_values_config(raw_config)
    test_data = _parse_test_data_config(raw_config)
    open_banking = _parse_open_banking_config(raw_config)

    return ModelBankConfig(
        environment=environment,
        discovery_url=discovery_url,
        timeout_seconds=timeout_seconds,
        follow_up_mode=follow_up_mode,
        tls=tls,
        result_output_path=result_output_path,
        execution_log_path=execution_log_path,
        test_target=test_target,
        dcr=dcr,
        approved_release_policy=approved_release_policy,
        oauth=oauth,
        fapi_signing=fapi_signing,
        test_values=test_values,
        test_data=test_data,
        open_banking=open_banking,
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


def _parse_test_target_from_config(raw_config: dict[str, JsonValue]) -> TestTargetConfig | None:
    """Parse the optional ``testTarget`` section of a participant config.

    Delegates to :func:`~conformance.target_config.parse_test_target_config` for
    structural validation of the target coordinates.

    Args:
        raw_config: Top-level raw configuration dictionary.

    Returns:
        The validated :class:`~conformance.target_config.TestTargetConfig`, or
        ``None`` when the config omits ``testTarget``.

    Raises:
        ConfigError: If ``testTarget`` is present but structurally invalid.
    """
    raw_target = raw_config.get("testTarget")
    if raw_target is None:
        return None
    try:
        return parse_test_target_config(raw_target)
    except TestTargetConfigError as error:
        raise ConfigError(f"testTarget is invalid: {error}") from error


def _parse_dcr_config(raw_config: dict[str, JsonValue], *, base_dir: Path) -> DcrConfig | None:
    """Parse the optional ``dcr`` section of a participant config.

    Validates credential file paths as exact absolute paths.  Constructs
    :class:`~conformance.dcr.credentials.DcrCredentialPaths` and
    :class:`~conformance.dcr.transport.DcrTransportConfig` and bundles them
    into a :class:`DcrConfig`.

    Args:
        raw_config: Top-level raw configuration dictionary.
        base_dir: Directory of the config file.  Kept for API symmetry; DCR
            credential paths must be absolute and are not resolved relative to
            it.

    Returns:
        A validated :class:`DcrConfig`, or ``None`` when the config omits the
        ``dcr`` section.

    Raises:
        ConfigError: If ``dcr`` is present but structurally invalid, contains
            unknown keys, omits required credential paths, supplies relative
            credential paths, or names an unsupported ``tokenEndpointAuthMethod``.
    """
    raw_dcr = raw_config.get("dcr")
    if raw_dcr is None:
        return None
    if not isinstance(raw_dcr, dict):
        raise ConfigError("dcr must be a JSON object")

    _reject_unknown_keys(
        raw_dcr,
        allowed_keys={
            "ssaPath",
            "signingPrivateKeyPath",
            "signingCertificatePath",
            "transportCertificatePath",
            "transportPrivateKeyPath",
            "caBundlePath",
            "tokenEndpointAuthMethod",
            "disableKeepAlives",
            "tlsSkipVerify",
            "timeoutSeconds",
        },
        location="dcr",
    )

    ssa_path = _optional_absolute_path(raw_dcr, "ssaPath")
    signing_key_path = _optional_absolute_path(raw_dcr, "signingPrivateKeyPath")
    signing_cert_path = _optional_absolute_path(raw_dcr, "signingCertificatePath")
    transport_cert_path = _optional_absolute_path(raw_dcr, "transportCertificatePath")
    transport_key_path = _optional_absolute_path(raw_dcr, "transportPrivateKeyPath")
    ca_bundle_path = _optional_absolute_path(raw_dcr, "caBundlePath")

    missing_paths: list[str] = []
    if ssa_path is None:
        missing_paths.append("ssaPath")
    if signing_key_path is None:
        missing_paths.append("signingPrivateKeyPath")
    if signing_cert_path is None:
        missing_paths.append("signingCertificatePath")
    if transport_cert_path is None:
        missing_paths.append("transportCertificatePath")
    if transport_key_path is None:
        missing_paths.append("transportPrivateKeyPath")
    if missing_paths:
        joined = ", ".join(missing_paths)
        raise ConfigError(f"dcr requires the following paths: {joined}")
    # mypy narrowing: the guard above raises when any required path is None.
    if (
        ssa_path is None
        or signing_key_path is None
        or signing_cert_path is None
        or transport_cert_path is None
        or transport_key_path is None
    ):
        raise ConfigError("dcr required paths must not be null")  # pragma: no cover

    raw_auth = raw_dcr.get("tokenEndpointAuthMethod")
    if raw_auth is None:
        auth_method: DcrTokenEndpointAuthMethod = "tls_client_auth"
    elif not isinstance(raw_auth, str) or raw_auth not in {"tls_client_auth", "private_key_jwt"}:
        raise ConfigError("dcr.tokenEndpointAuthMethod must be one of: tls_client_auth, private_key_jwt")
    else:
        auth_method = cast(DcrTokenEndpointAuthMethod, raw_auth)

    disable_keep_alives = _optional_bool(raw_dcr, "disableKeepAlives", default=False, location="dcr")
    tls_skip_verify = _optional_bool(raw_dcr, "tlsSkipVerify", default=False, location="dcr")
    timeout_seconds = _optional_positive_number(raw_dcr, "timeoutSeconds", default=30.0)

    if tls_skip_verify:
        warnings.warn(_DCR_TLS_SKIP_VERIFY_WARNING, stacklevel=4)

    credential_paths = DcrCredentialPaths(
        credential_path_root=_credential_compatibility_root(
            ssa_path,
            signing_key_path,
            signing_cert_path,
            transport_cert_path,
            transport_key_path,
            ca_bundle_path,
            fallback=base_dir,
        ),
        ssa_path=ssa_path,
        signing_private_key_path=signing_key_path,
        signing_certificate_path=signing_cert_path,
        transport_certificate_path=transport_cert_path,
        transport_private_key_path=transport_key_path,
        ca_bundle_path=ca_bundle_path,
    )
    transport = DcrTransportConfig(
        token_endpoint_auth_method=auth_method,
        disable_keep_alives=disable_keep_alives,
        tls_skip_verify=tls_skip_verify,
        connection_timeout_seconds=timeout_seconds,
        read_timeout_seconds=timeout_seconds,
    )
    return DcrConfig(credential_paths=credential_paths, transport=transport)


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
            "openBankingIntentId",
            "resourceBaseUrl",
        },
        location="oauth",
    )

    client_id = _required_string_at(raw_oauth, "clientId", location="oauth")
    redirect_uri_str = _required_string_at(raw_oauth, "redirectUri", location="oauth")
    authorization_endpoint = _optional_https_url_at(raw_oauth, "authorizationEndpoint", location="oauth")
    open_banking_intent_id = _optional_string_at(raw_oauth, "openBankingIntentId", location="oauth")
    resource_base_url = _optional_https_url_at(raw_oauth, "resourceBaseUrl", location="oauth")
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
    )


def _parse_test_values_config(raw_config: dict[str, JsonValue]) -> TestValuesConfig | None:
    """Parse the optional ``testValues`` section of a participant config.

    Accepts a ``profile`` string and an ``overrides`` object mapping key names
    to string values. The key name character set is validated here; cross-
    validation against manifest-declared ``allowedOverrideKeys`` is deferred to
    callers that have both the config and the loaded manifest.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed ``TestValuesConfig``, or ``None`` when the config omits the
        ``testValues`` section.

    Raises:
        ConfigError: If ``testValues`` is not a JSON object, contains unknown
            keys, ``profile`` is not a non-empty string, ``overrides`` is not a
            JSON object, or any override key or value fails validation.
    """
    raw_tv = raw_config.get("testValues")
    if raw_tv is None:
        return None
    if not isinstance(raw_tv, dict):
        raise ConfigError("testValues must be a JSON object")

    _reject_unknown_keys(
        raw_tv,
        allowed_keys={"profile", "overrides"},
        location="testValues",
    )

    profile: str | None = None
    raw_profile = raw_tv.get("profile")
    if raw_profile is not None:
        if not isinstance(raw_profile, str) or not raw_profile.strip():
            raise ConfigError("testValues.profile must be a non-empty string when present")
        profile = raw_profile.strip()

    overrides: dict[str, str] = {}
    raw_overrides = raw_tv.get("overrides")
    if raw_overrides is not None:
        if not isinstance(raw_overrides, dict):
            raise ConfigError("testValues.overrides must be a JSON object when present")
        for key, value in raw_overrides.items():
            if not key or _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
                raise ConfigError(f"testValues.overrides key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)")
            if not isinstance(value, str):
                raise ConfigError(f"testValues.overrides.{key} must be a string value")
            overrides[key] = value

    return TestValuesConfig(
        profile=profile,
        overrides=MappingProxyType(overrides),
    )


def _parse_test_data_config(raw_config: dict[str, JsonValue]) -> TestDataConfig | None:
    """Parse the optional ``testData`` section of a participant config.

    Accepts a ``values`` object mapping key names to string values.
    The key name character set is validated here.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed ``TestDataConfig``, or ``None`` when the config omits the
        ``testData`` section.

    Raises:
        ConfigError: If ``testData`` is not a JSON object, contains unknown
            keys, ``values`` is not a JSON object, or any value key or value
            fails validation.
    """
    raw_test_data = raw_config.get("testData")
    if raw_test_data is None:
        return None
    if not isinstance(raw_test_data, dict):
        raise ConfigError("testData must be a JSON object")

    _reject_unknown_keys(raw_test_data, allowed_keys={"values"}, location="testData")

    raw_values = raw_test_data.get("values")
    if not isinstance(raw_values, dict):
        raise ConfigError("testData.values must be a JSON object")
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        if not key or _TEST_VALUES_KEY_PATTERN.fullmatch(key) is None:
            raise ConfigError(f"testData.values key {key!r} is invalid (must match [A-Za-z][A-Za-z0-9_-]*)")
        if not isinstance(value, str):
            raise ConfigError(f"testData.values.{key} must be a string value")
        values[key] = value

    return TestDataConfig(values=MappingProxyType(values))


@dataclass(frozen=True)
class OpenBankingConfig:
    """Open Banking institution metadata for outbound resource-server requests.

    Attributes:
        financial_id: Financial institution identifier sent as the
            ``x-fapi-financial-id`` request header on Open Banking
            resource-server write requests. Sourced from the participant
            config ``openBanking.financialId`` field and masked in
            execution logs and result evidence by the masking layer.
    """

    financial_id: str


def _parse_fapi_signing_config(raw_config: dict[str, JsonValue], *, base_dir: Path) -> FapiSigningConfig | None:
    """Parse the optional ``fapiSigning`` section of a participant config.

    This section is intentionally separate from ``oauth`` so bundled manifest
    placeholders cannot traverse into signing paths, JOSE metadata, or future
    client-auth credentials.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.
        base_dir: Directory of the config file. Kept for API symmetry; signing
            credential paths must be absolute and are not resolved relative to
            it.

    Returns:
        Parsed ``FapiSigningConfig``, or ``None`` when the config omits the
        ``fapiSigning`` section.

    Raises:
        ConfigError: If ``fapiSigning`` is not a JSON object, contains unknown
            keys, omits required values, specifies an unsupported token-endpoint
            auth method, or supplies relative signing credential paths.
    """
    raw_fapi_signing = raw_config.get("fapiSigning")
    if raw_fapi_signing is None:
        return None
    if not isinstance(raw_fapi_signing, dict):
        raise ConfigError("fapiSigning must be a JSON object")

    _reject_unknown_keys(
        raw_fapi_signing,
        allowed_keys={
            "signingCertificatePath",
            "signingPrivateKeyPath",
            "kid",
            "clientAssertionIssuer",
            "clientAssertionSubject",
            "tokenEndpointAuthMethod",
            "signatureIssuer",
            "signatureTrustAnchor",
        },
        location="fapiSigning",
    )

    signing_certificate_path = _optional_absolute_path(raw_fapi_signing, "signingCertificatePath")
    signing_private_key_path = _optional_absolute_path(raw_fapi_signing, "signingPrivateKeyPath")
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

    signature_issuer = _optional_string_at(raw_fapi_signing, "signatureIssuer", location="fapiSigning")
    signature_trust_anchor = _optional_string_at(raw_fapi_signing, "signatureTrustAnchor", location="fapiSigning")
    if (signature_issuer is None) != (signature_trust_anchor is None):
        raise ConfigError("fapiSigning.signatureIssuer and fapiSigning.signatureTrustAnchor must be supplied together")

    return FapiSigningConfig(
        certificate_path_root=_credential_compatibility_root(
            signing_certificate_path,
            signing_private_key_path,
            fallback=base_dir,
        ),
        signing_certificate_path=signing_certificate_path,
        signing_private_key_path=signing_private_key_path,
        key_id=key_id,
        client_assertion_issuer=client_assertion_issuer,
        client_assertion_subject=client_assertion_subject,
        token_endpoint_auth_method=cast(TokenEndpointClientAuthMode, token_endpoint_auth_method),
        signature_issuer=signature_issuer,
        signature_trust_anchor=signature_trust_anchor,
    )


def _parse_open_banking_config(raw_config: dict[str, JsonValue]) -> OpenBankingConfig | None:
    """Parse the optional ``openBanking`` section of a participant config.

    Accepts the ``financialId`` string used to inject the
    ``x-fapi-financial-id`` header on outbound Open Banking resource-server
    write requests. This field is institution-level metadata and is not a
    secret, but it is masked in execution logs and result evidence by the
    masking layer's existing ``x-fapi-financial-id`` rule.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file or API request.

    Returns:
        Parsed ``OpenBankingConfig``, or ``None`` when the config omits the
        ``openBanking`` section.

    Raises:
        ConfigError: If ``openBanking`` is not a JSON object, contains unknown
            keys, or omits the required ``financialId`` field.
    """
    raw_ob = raw_config.get("openBanking")
    if raw_ob is None:
        return None
    if not isinstance(raw_ob, dict):
        raise ConfigError("openBanking must be a JSON object")

    _reject_unknown_keys(
        raw_ob,
        allowed_keys={"financialId"},
        location="openBanking",
    )

    financial_id = _required_string_at(raw_ob, "financialId", location="openBanking")
    return OpenBankingConfig(financial_id=financial_id)


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


def _parse_tls_config(raw_config: dict[str, JsonValue]) -> TlsConfig:
    """Parse the optional ``tls`` section of a model bank config dict.

    If the key is absent a zero-value ``TlsConfig`` (no custom TLS) is
    returned. Certificate paths must be exact absolute file paths.
    ``clientCertificatePath`` and ``clientPrivateKeyPath`` must be supplied
    together or not at all.

    Args:
        raw_config: Top-level raw configuration dictionary.

    Returns:
        A populated ``TlsConfig`` dataclass.

    Raises:
        ConfigError: If ``tls`` is not a JSON object, contains unknown keys,
            supplies relative paths, specifies paths that do not exist, or supplies only one of the client
            certificate / private key pair.
    """
    raw_tls = raw_config.get("tls")
    if raw_tls is None:
        return TlsConfig()
    if not isinstance(raw_tls, dict):
        raise ConfigError("tls must be a JSON object")

    _reject_unknown_keys(
        raw_tls,
        allowed_keys={"caBundlePath", "clientCertificatePath", "clientPrivateKeyPath"},
        location="tls",
    )

    ca_bundle_path = _optional_existing_absolute_file_path(raw_tls, "caBundlePath")
    client_certificate_path = _optional_existing_absolute_file_path(raw_tls, "clientCertificatePath")
    client_private_key_path = _optional_existing_absolute_file_path(raw_tls, "clientPrivateKeyPath")

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


def _optional_bool(raw_config: dict[str, JsonValue], key: str, *, default: bool, location: str) -> bool:
    """Extract an optional boolean value from a raw config dict.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a boolean.
        default: Value to return when the key is absent.
        location: Dot-path prefix used in validation error messages.

    Returns:
        The boolean value or the supplied default.

    Raises:
        ConfigError: If the key is present but the value is not a boolean.
    """
    value = raw_config.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be a boolean when present")
    return value


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


def _optional_absolute_path(raw_config: dict[str, JsonValue], key: str) -> Path | None:
    """Extract an optional exact absolute path without checking file contents.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.

    Returns:
        Absolute resolved ``Path`` when the key is present, or ``None``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string or is not an absolute path.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")

    raw_path = Path(value.strip())
    if not raw_path.is_absolute():
        raise ConfigError(f"{key} must be an absolute file path")
    return raw_path.resolve()


def _optional_existing_absolute_file_path(raw_config: dict[str, JsonValue], key: str) -> Path | None:
    """Extract an optional exact absolute path that must point to an existing file.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.

    Returns:
        Absolute resolved ``Path`` when the key is present, or ``None``.

    Raises:
        ConfigError: If the key is present but invalid, relative, or not an
            existing file.
    """
    resolved_path = _optional_absolute_path(raw_config, key)
    if resolved_path is None:
        return None
    if not resolved_path.is_file():
        raise ConfigError(f"{key} must point to an existing file")
    return resolved_path


def _credential_compatibility_root(*paths: Path | None, fallback: Path) -> Path:
    """Derive an internal compatibility root from exact credential paths.

    Args:
        *paths: Optional absolute credential paths supplied by the participant.
        fallback: Directory used when no paths are present.

    Returns:
        Common parent directory for the supplied paths, or ``fallback`` when no
        credential paths are available.
    """
    parent_paths = [path.resolve().parent for path in paths if path is not None]
    if not parent_paths:
        return fallback.resolve()
    return Path(os.path.commonpath([str(parent) for parent in parent_paths])).resolve()


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
