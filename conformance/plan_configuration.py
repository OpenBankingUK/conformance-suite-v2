"""Typed shared and DCR-specific canonical test-plan configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError, TlsConfig
from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri

type ClientAuthMethod = Literal[
    "tls_client_auth",
    "private_key_jwt",
    "client_secret_jwt",
    "client_secret_basic",
]
"""DCR 3.4 token-endpoint client authentication methods executable by the runtime."""

_CLIENT_AUTH_METHODS = frozenset({"tls_client_auth", "private_key_jwt", "client_secret_jwt", "client_secret_basic"})
"""Canonical client-auth methods accepted by shared plan configuration."""


@dataclass(frozen=True)
class SharedSigningConfig:
    """Signing references shared by Read/Write and DCR plans.

    Attributes:
        private_key_path: Absolute private-key file reference.
        key_id: JOSE key identifier.
        algorithm: Optional discovery-selected client-auth signing algorithm.
    """

    private_key_path: Path | None
    key_id: str | None
    algorithm: str | None


@dataclass(frozen=True)
class ReportingMetadataConfig:
    """Non-secret reporting metadata shared by specification families.

    Attributes:
        aspsp_name: Optional ASPSP display name.
        brand_name: Optional brand display name.
        environment_name: Optional environment display name.
    """

    aspsp_name: str | None
    brand_name: str | None
    environment_name: str | None


@dataclass(frozen=True)
class SharedPlanConfiguration:
    """Typed canonical security and reporting concepts shared by plan families.

    Attributes:
        discovery_url: Optional HTTPS OpenID discovery document URL.
        mtls: Shared mTLS certificate, private-key, and CA-bundle references.
        signing: Shared signing private-key, key-id, and algorithm references.
        client_auth_method: Optional executable token client-auth method.
        metadata: Shared non-secret reporting metadata.
    """

    discovery_url: str | None
    mtls: TlsConfig
    signing: SharedSigningConfig
    client_auth_method: ClientAuthMethod | None
    metadata: ReportingMetadataConfig


@dataclass(frozen=True)
class DynamicClientRegistrationConfig:
    """Typed configuration owned only by Open Banking DCR 3.4.

    Attributes:
        software_statement_assertion_path: Absolute SSA file reference.
        registration_audience: Required Base62 ASPSP audience identifier.
        registration_issuer_override: Optional registration JWT issuer override.
        redirect_uris_override: Optional HTTPS redirect URI overrides.
        signing_certificate_path: Optional certificate used to derive signing claims.
        transport_certificate_subject_dn_override: Optional RFC 2253-style subject DN.
        use_numeric_oid_subject_dn: Whether derived subject DNs use numeric OIDs.
        disable_keep_alive: Explicit transport interoperability override.
    """

    software_statement_assertion_path: Path
    registration_audience: str
    registration_issuer_override: str | None
    redirect_uris_override: tuple[str, ...]
    signing_certificate_path: Path | None
    transport_certificate_subject_dn_override: str | None
    use_numeric_oid_subject_dn: bool
    disable_keep_alive: bool


@dataclass(frozen=True)
class DcrPlanConfiguration:
    """Complete typed configuration boundary for an Open Banking DCR plan.

    Attributes:
        shared: Security and reporting concepts reused across specifications.
        dynamic_client_registration: Narrow DCR-owned configuration.
    """

    shared: SharedPlanConfiguration
    dynamic_client_registration: DynamicClientRegistrationConfig


def dcr_execution_runtime_inputs(config: DcrPlanConfiguration) -> dict[str, JsonValue]:
    """Flatten typed DCR configuration for the catalogue execution adapter.

    Args:
        config: Validated DCR plan configuration.

    Returns:
        Canonical dotted-path values needed only at DCR execution time.
    """
    shared = config.shared
    dcr = config.dynamic_client_registration
    values: dict[str, JsonValue] = {
        "securityEnvironment.discoveryUrl": shared.discovery_url,
        "securityEnvironment.mtls.certificatePath": str(shared.mtls.client_certificate_path),
        "securityEnvironment.mtls.privateKeyPath": str(shared.mtls.client_private_key_path),
        "securityEnvironment.signingPrivateKeyPath": str(shared.signing.private_key_path),
        "securityEnvironment.signingKeyId": shared.signing.key_id,
        "securityEnvironment.clientAuthMethod": shared.client_auth_method,
        "dynamicClientRegistration.softwareStatementAssertionPath": str(dcr.software_statement_assertion_path),
        "dynamicClientRegistration.registrationAudience": dcr.registration_audience,
        "dynamicClientRegistration.useNumericOidSubjectDn": dcr.use_numeric_oid_subject_dn,
        "dynamicClientRegistration.disableKeepAlive": dcr.disable_keep_alive,
    }
    optional_values: tuple[tuple[str, JsonValue | Path | None], ...] = (
        ("securityEnvironment.mtls.caBundlePath", shared.mtls.ca_bundle_path),
        ("securityEnvironment.clientAuthSigningAlgorithm", shared.signing.algorithm),
        ("dynamicClientRegistration.registrationIssuerOverride", dcr.registration_issuer_override),
        ("dynamicClientRegistration.signingCertificatePath", dcr.signing_certificate_path),
        (
            "dynamicClientRegistration.transportCertificateSubjectDnOverride",
            dcr.transport_certificate_subject_dn_override,
        ),
    )
    for key, value in optional_values:
        if value is not None:
            values[key] = str(value) if isinstance(value, Path) else value
    if dcr.redirect_uris_override:
        values["dynamicClientRegistration.redirectUrisOverride"] = list(dcr.redirect_uris_override)
    return values


def parse_dcr_execution_runtime_inputs(runtime_inputs: Mapping[str, JsonValue]) -> DcrPlanConfiguration:
    """Parse dotted catalogue runtime inputs into typed DCR configuration.

    Args:
        runtime_inputs: Runtime values prepared from a canonical DCR plan.

    Returns:
        Validated DCR configuration for execution.

    Raises:
        ConfigError: If a required execution value is missing or malformed.
    """
    security: dict[str, JsonValue] = {}
    mtls: dict[str, JsonValue] = {}
    dcr: dict[str, JsonValue] = {}
    for key, value in runtime_inputs.items():
        if key.startswith("securityEnvironment.mtls."):
            mtls[key.removeprefix("securityEnvironment.mtls.")] = value
        elif key.startswith("securityEnvironment."):
            security[key.removeprefix("securityEnvironment.")] = value
        elif key.startswith("dynamicClientRegistration."):
            dcr[key.removeprefix("dynamicClientRegistration.")] = value
    if mtls:
        security["mtls"] = mtls
    return parse_dcr_plan_configuration(security, dcr, {})


def validate_dcr_file_references(config: DcrPlanConfiguration) -> None:
    """Validate every configured DCR credential reference against the filesystem.

    Args:
        config: Parsed DCR configuration to validate before execution.

    Raises:
        ConfigError: If a required or configured reference is not an existing file.
    """
    paths = {
        "securityEnvironment.mtls.certificatePath": config.shared.mtls.client_certificate_path,
        "securityEnvironment.mtls.privateKeyPath": config.shared.mtls.client_private_key_path,
        "securityEnvironment.mtls.caBundlePath": config.shared.mtls.ca_bundle_path,
        "securityEnvironment.signingPrivateKeyPath": config.shared.signing.private_key_path,
        "dynamicClientRegistration.softwareStatementAssertionPath": (
            config.dynamic_client_registration.software_statement_assertion_path
        ),
        "dynamicClientRegistration.signingCertificatePath": (
            config.dynamic_client_registration.signing_certificate_path
        ),
    }
    for location, path in paths.items():
        if path is not None and not path.is_file():
            raise ConfigError(f"{location} must reference an existing file")


def parse_shared_plan_configuration(
    security_environment: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
) -> SharedPlanConfiguration:
    """Parse reusable canonical security and reporting configuration.

    Args:
        security_environment: Canonical ``securityEnvironment`` object.
        metadata: Canonical non-secret ``metadata`` object.

    Returns:
        Typed shared plan configuration.

    Raises:
        ConfigError: If a configured URL, path, auth method, or metadata value is invalid.
    """
    discovery_url = _optional_https_url(security_environment, "discoveryUrl", "securityEnvironment")
    mtls_value = security_environment.get("mtls")
    if mtls_value is not None and not isinstance(mtls_value, dict):
        raise ConfigError("securityEnvironment.mtls must be a JSON object")
    mtls = cast(Mapping[str, JsonValue], mtls_value) if isinstance(mtls_value, dict) else {}
    certificate_path = _optional_absolute_path(mtls, "certificatePath", "securityEnvironment.mtls")
    private_key_path = _optional_absolute_path(mtls, "privateKeyPath", "securityEnvironment.mtls")
    if (certificate_path is None) != (private_key_path is None):
        raise ConfigError("securityEnvironment.mtls.certificatePath and privateKeyPath must be supplied together")

    method_value = _optional_string(security_environment, "clientAuthMethod", "securityEnvironment")
    if method_value is not None and method_value not in _CLIENT_AUTH_METHODS:
        supported = ", ".join(sorted(_CLIENT_AUTH_METHODS))
        raise ConfigError(f"securityEnvironment.clientAuthMethod must be one of: {supported}")
    return SharedPlanConfiguration(
        discovery_url=discovery_url,
        mtls=TlsConfig(
            ca_bundle_path=_optional_absolute_path(mtls, "caBundlePath", "securityEnvironment.mtls"),
            client_certificate_path=certificate_path,
            client_private_key_path=private_key_path,
        ),
        signing=SharedSigningConfig(
            private_key_path=_optional_absolute_path(
                security_environment,
                "signingPrivateKeyPath",
                "securityEnvironment",
            ),
            key_id=_optional_string(security_environment, "signingKeyId", "securityEnvironment"),
            algorithm=_optional_string(
                security_environment,
                "clientAuthSigningAlgorithm",
                "securityEnvironment",
            ),
        ),
        client_auth_method=cast(ClientAuthMethod | None, method_value),
        metadata=ReportingMetadataConfig(
            aspsp_name=_optional_string(metadata, "aspspName", "metadata"),
            brand_name=_optional_string(metadata, "brandName", "metadata"),
            environment_name=_optional_string(metadata, "environmentName", "metadata"),
        ),
    )


def parse_dcr_plan_configuration(
    security_environment: Mapping[str, JsonValue],
    dynamic_client_registration: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
) -> DcrPlanConfiguration:
    """Parse and validate the canonical Open Banking DCR 3.4 configuration.

    Args:
        security_environment: Canonical shared security configuration.
        dynamic_client_registration: Canonical DCR-only configuration.
        metadata: Canonical shared reporting metadata.

    Returns:
        Complete typed DCR plan configuration.

    Raises:
        ConfigError: If required shared/DCR references are missing or malformed.
    """
    shared = parse_shared_plan_configuration(security_environment, metadata)
    if shared.discovery_url is None:
        raise ConfigError("securityEnvironment.discoveryUrl is required for DCR")
    if shared.mtls.client_certificate_path is None or shared.mtls.client_private_key_path is None:
        raise ConfigError("securityEnvironment.mtls certificatePath and privateKeyPath are required for DCR")
    if shared.signing.private_key_path is None:
        raise ConfigError("securityEnvironment.signingPrivateKeyPath is required for DCR")
    if shared.signing.key_id is None:
        raise ConfigError("securityEnvironment.signingKeyId is required for DCR")
    if shared.client_auth_method is None:
        raise ConfigError("securityEnvironment.clientAuthMethod is required for DCR")

    ssa_path = _required_absolute_path(
        dynamic_client_registration,
        "softwareStatementAssertionPath",
        "dynamicClientRegistration",
    )
    audience = _optional_string(
        dynamic_client_registration,
        "registrationAudience",
        "dynamicClientRegistration",
    )
    if audience is None or re.fullmatch(r"[0-9A-Za-z]{1,18}", audience) is None:
        raise ConfigError(
            "dynamicClientRegistration.registrationAudience must be a 1 to 18 character Base62 ASPSP identifier"
        )
    issuer = _optional_string(
        dynamic_client_registration,
        "registrationIssuerOverride",
        "dynamicClientRegistration",
    )
    redirect_uris = _optional_redirect_uris(dynamic_client_registration)
    subject_dn = _optional_string(
        dynamic_client_registration,
        "transportCertificateSubjectDnOverride",
        "dynamicClientRegistration",
    )
    if subject_dn is not None and (
        len(subject_dn) > 512 or "=" not in subject_dn or any(ord(char) < 32 for char in subject_dn)
    ):
        raise ConfigError(
            "dynamicClientRegistration.transportCertificateSubjectDnOverride must be an RFC 2253-style "
            "subject DN no longer than 512 characters"
        )
    return DcrPlanConfiguration(
        shared=shared,
        dynamic_client_registration=DynamicClientRegistrationConfig(
            software_statement_assertion_path=ssa_path,
            registration_audience=audience,
            registration_issuer_override=issuer,
            redirect_uris_override=redirect_uris,
            signing_certificate_path=_optional_absolute_path(
                dynamic_client_registration,
                "signingCertificatePath",
                "dynamicClientRegistration",
            ),
            transport_certificate_subject_dn_override=subject_dn,
            use_numeric_oid_subject_dn=_optional_boolean(
                dynamic_client_registration,
                "useNumericOidSubjectDn",
                "dynamicClientRegistration",
                default=False,
            ),
            disable_keep_alive=_optional_boolean(
                dynamic_client_registration,
                "disableKeepAlive",
                "dynamicClientRegistration",
                default=False,
            ),
        ),
    )


def _optional_string(config: Mapping[str, JsonValue], key: str, location: str) -> str | None:
    """Read an optional non-empty string.

    Args:
        config: JSON object containing the value.
        key: Field key to read.
        location: Parent location used in errors.

    Returns:
        Stripped string, or ``None`` when omitted.

    Raises:
        ConfigError: If the value is not a non-empty string.
    """
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string when present")
    return value.strip()


def _optional_absolute_path(config: Mapping[str, JsonValue], key: str, location: str) -> Path | None:
    """Read an optional absolute filesystem path.

    Args:
        config: JSON object containing the path.
        key: Field key to read.
        location: Parent location used in errors.

    Returns:
        Absolute path, or ``None`` when omitted.

    Raises:
        ConfigError: If the value is not an absolute path string.
    """
    value = _optional_string(config, key, location)
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError(f"{location}.{key} must be an absolute path")
    return path


def _required_absolute_path(config: Mapping[str, JsonValue], key: str, location: str) -> Path:
    """Read a required absolute filesystem path.

    Args:
        config: JSON object containing the path.
        key: Required field key.
        location: Parent location used in errors.

    Returns:
        Validated absolute path.

    Raises:
        ConfigError: If the field is missing or is not an absolute path.
    """
    value = _optional_absolute_path(config, key, location)
    if value is None:
        raise ConfigError(f"{location}.{key} is required")
    return value


def _optional_https_url(config: Mapping[str, JsonValue], key: str, location: str) -> str | None:
    """Read an optional validated HTTPS URL.

    Args:
        config: JSON object containing the URL.
        key: Field key to read.
        location: Parent location used in errors.

    Returns:
        Validated HTTPS URL, or ``None`` when omitted.

    Raises:
        ConfigError: If the URL is invalid.
    """
    value = _optional_string(config, key, location)
    if value is None:
        return None
    try:
        validate_https_url(value, label=f"{location}.{key}")
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error
    return value


def _optional_redirect_uris(config: Mapping[str, JsonValue]) -> tuple[str, ...]:
    """Read optional DCR redirect URI overrides.

    Args:
        config: Canonical DCR-only configuration object.

    Returns:
        Validated redirect URI tuple.

    Raises:
        ConfigError: If the field is not a non-empty string array or a URI is invalid.
    """
    value = config.get("redirectUrisOverride")
    if value is None:
        return ()
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError("dynamicClientRegistration.redirectUrisOverride must be a non-empty string array")
    redirects = tuple(cast(str, item).strip() for item in value)
    try:
        for redirect in redirects:
            validate_oauth_redirect_uri(redirect, label="dynamicClientRegistration.redirectUrisOverride")
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error
    return redirects


def _optional_boolean(
    config: Mapping[str, JsonValue],
    key: str,
    location: str,
    *,
    default: bool,
) -> bool:
    """Read an optional JSON boolean.

    Args:
        config: JSON object containing the value.
        key: Field key to read.
        location: Parent location used in errors.
        default: Value returned when the key is absent.

    Returns:
        Parsed boolean.

    Raises:
        ConfigError: If a present value is not a JSON boolean.
    """
    value = config.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be a JSON boolean")
    return value
