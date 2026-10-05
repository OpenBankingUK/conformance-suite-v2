"""Participant connection and security config a compiled Read/Write plan needs to run.

The executor turns selected catalogue cases into HTTP, OAuth 2.0 token, and
PSU authorisation steps that read participant config at run time: OAuth client
values through ``${config.oauth.*}`` placeholders, the FAPI signing group for
``private_key_jwt`` client assertions, JAR request objects and Open Banking
detached JWS (``x-jws-signature``), the protected-resource base URL, and the
OpenID discovery URL for discovery fetches and response-signature checks.
:func:`conformance.executor.compiled_plan_run_config_requirements` derives
which of those a compiled plan uses; this module names them and checks a raw
plan config for the values that are still missing, so the builder, review,
API, and CLI all apply one rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from conformance.json_types import JsonValue

type RunConfigRequirement = Literal[
    "discoveryUrl",
    "oauth.clientId",
    "oauth.redirectUri",
    "oauth.authorizationEndpoint",
    "oauth.issuer",
    "oauth.tokenEndpoint",
    "resourceBaseUrl",
    "fapiSigning",
]
"""Runner dependency derived from the steps a compiled plan will execute."""

type RunConfigKey = Literal[
    "discoveryUrl",
    "oauth.clientId",
    "oauth.redirectUri",
    "oauth.authorizationEndpoint",
    "oauth.issuer",
    "oauth.tokenEndpoint",
    "resourceBaseUrl",
    "fapiSigning.signingCertificate",
    "fapiSigning.signingPrivateKey",
    "fapiSigning.kid",
    "fapiSigning.clientAssertionIssuer",
    "fapiSigning.clientAssertionSubject",
    "fapiSigning.tokenEndpointAuthMethod",
    "tls.clientCertificate",
    "tls.clientPrivateKey",
]
"""One participant config value that can be required to run a plan."""

_REQUIREMENT_ORDER: tuple[RunConfigRequirement, ...] = (
    "discoveryUrl",
    "oauth.clientId",
    "oauth.redirectUri",
    "oauth.authorizationEndpoint",
    "oauth.issuer",
    "oauth.tokenEndpoint",
    "resourceBaseUrl",
    "fapiSigning",
)

_FAPI_SIGNING_KEYS: tuple[RunConfigKey, ...] = (
    "fapiSigning.signingCertificate",
    "fapiSigning.signingPrivateKey",
    "fapiSigning.kid",
    "fapiSigning.clientAssertionIssuer",
    "fapiSigning.clientAssertionSubject",
    "fapiSigning.tokenEndpointAuthMethod",
)

_MTLS_KEYS: tuple[RunConfigKey, ...] = ("tls.clientCertificate", "tls.clientPrivateKey")

type _ValueSource = tuple[Literal["config", "securityEnvironment"], tuple[str, ...]]
"""Where one config value may be supplied: plan config or canonical security environment."""

_VALUE_SOURCES: Mapping[RunConfigKey, tuple[_ValueSource, ...]] = {
    "discoveryUrl": (("securityEnvironment", ("discoveryUrl",)), ("config", ("discoveryUrl",))),
    "oauth.clientId": (("securityEnvironment", ("clientId",)), ("config", ("oauth", "clientId"))),
    "oauth.redirectUri": (("securityEnvironment", ("redirectUri",)), ("config", ("oauth", "redirectUri"))),
    "oauth.authorizationEndpoint": (
        ("securityEnvironment", ("authorizationEndpoint",)),
        ("config", ("oauth", "authorizationEndpoint")),
    ),
    "oauth.issuer": (("securityEnvironment", ("issuer",)), ("config", ("oauth", "issuer"))),
    "oauth.tokenEndpoint": (("securityEnvironment", ("tokenEndpoint",)), ("config", ("oauth", "tokenEndpoint"))),
    "resourceBaseUrl": (
        ("securityEnvironment", ("resourceBaseUrl",)),
        ("config", ("resourceServer", "baseUrl")),
        ("config", ("oauth", "resourceBaseUrl")),
    ),
    "fapiSigning.signingCertificate": (
        ("securityEnvironment", ("signingCertificatePath",)),
        ("securityEnvironment", ("signingCertificatePem",)),
        ("config", ("fapiSigning", "signingCertificatePath")),
        ("config", ("fapiSigning", "signingCertificatePem")),
    ),
    "fapiSigning.signingPrivateKey": (
        ("securityEnvironment", ("signingPrivateKeyPath",)),
        ("securityEnvironment", ("signingPrivateKeyPem",)),
        ("config", ("fapiSigning", "signingPrivateKeyPath")),
        ("config", ("fapiSigning", "signingPrivateKeyPem")),
    ),
    "fapiSigning.kid": (("securityEnvironment", ("signingKeyId",)), ("config", ("fapiSigning", "kid"))),
    "fapiSigning.clientAssertionIssuer": (
        ("securityEnvironment", ("clientAssertionIssuer",)),
        ("config", ("fapiSigning", "clientAssertionIssuer")),
    ),
    "fapiSigning.clientAssertionSubject": (
        ("securityEnvironment", ("clientAssertionSubject",)),
        ("config", ("fapiSigning", "clientAssertionSubject")),
    ),
    "fapiSigning.tokenEndpointAuthMethod": (
        ("securityEnvironment", ("clientAuthMethod",)),
        ("config", ("fapiSigning", "tokenEndpointAuthMethod")),
    ),
    "tls.clientCertificate": (
        ("securityEnvironment", ("mtls", "certificatePath")),
        ("securityEnvironment", ("mtls", "certificatePem")),
        ("config", ("tls", "clientCertificatePath")),
        ("config", ("tls", "clientCertificatePem")),
    ),
    "tls.clientPrivateKey": (
        ("securityEnvironment", ("mtls", "privateKeyPath")),
        ("securityEnvironment", ("mtls", "privateKeyPem")),
        ("config", ("tls", "clientPrivateKeyPath")),
        ("config", ("tls", "clientPrivateKeyPem")),
    ),
}
"""Accepted locations for each value; the first is named in messages."""

_LOCATION_LABELS: Mapping[RunConfigKey, str] = {
    "fapiSigning.signingCertificate": "securityEnvironment.signingCertificatePath (or signingCertificatePem)",
    "fapiSigning.signingPrivateKey": "securityEnvironment.signingPrivateKeyPath (or signingPrivateKeyPem)",  # pragma: allowlist secret - config key name, not a value
    "tls.clientCertificate": "securityEnvironment.mtls.certificatePath (or certificatePem)",
    "tls.clientPrivateKey": "securityEnvironment.mtls.privateKeyPath (or privateKeyPem)",  # pragma: allowlist secret - config key name, not a value
}
"""Message labels for values that accept a path or inline PEM."""


RUN_CONFIG_REASONS: Mapping[RunConfigKey, str] = {
    "discoveryUrl": "the selected tests fetch OpenID discovery metadata or validate response signatures",
    "oauth.clientId": "the selected tests request OAuth 2.0 access tokens",
    "oauth.redirectUri": "the selected tests include PSU consent authorisation",
    "oauth.authorizationEndpoint": "the selected tests include PSU consent authorisation",
    "oauth.issuer": "the selected tests sign PSU authorisation request objects for this audience",
    "oauth.tokenEndpoint": "the selected tests request OAuth 2.0 access tokens",
    "resourceBaseUrl": "the selected tests call protected Open Banking resource endpoints",
    "fapiSigning.signingCertificate": "the selected tests sign client assertions, request objects or payloads",
    "fapiSigning.signingPrivateKey": "the selected tests sign client assertions, request objects or payloads",  # pragma: allowlist secret - config key name, not a value
    "fapiSigning.kid": "the selected tests sign client assertions, request objects or payloads",
    "fapiSigning.clientAssertionIssuer": "the selected tests authenticate to the token endpoint",
    "fapiSigning.clientAssertionSubject": "the selected tests authenticate to the token endpoint",
    "fapiSigning.tokenEndpointAuthMethod": "the selected tests authenticate to the token endpoint",
    "tls.clientCertificate": "the token endpoint auth method is tls_client_auth",
    "tls.clientPrivateKey": "the token endpoint auth method is tls_client_auth",  # pragma: allowlist secret - config key name, not a value
}
"""Short participant-facing reason each config value is required to run."""


@dataclass(frozen=True)
class MissingRunConfig:
    """One required run config value that a plan does not supply.

    Attributes:
        key: Config value that is missing.
        message: Participant-facing explanation naming the config location.
    """

    key: RunConfigKey
    message: str


def required_run_config_keys(
    requirements: Iterable[RunConfigRequirement],
    config: Mapping[str, JsonValue],
    *,
    security_environment: Mapping[str, JsonValue] | None = None,
) -> tuple[RunConfigKey, ...]:
    """Expand runner dependencies into the individual config values they need.

    The FAPI signing group expands to every value the runner's signing config
    needs; the mTLS client certificate and key are added only when the
    configured token endpoint auth method is ``tls_client_auth`` (RFC 8705).

    Args:
        requirements: Runner dependencies derived from a compiled plan.
        config: Raw plan config holding any values already supplied.
        security_environment: Canonical security environment, consulted for
            the token endpoint auth method.

    Returns:
        Required config values in a stable display order.
    """
    selected = set(requirements)
    keys: list[RunConfigKey] = []
    for requirement in _REQUIREMENT_ORDER:
        if requirement not in selected:
            continue
        if requirement == "fapiSigning":
            keys.extend(_FAPI_SIGNING_KEYS)
            if "tls_client_auth" in {
                _string_at(config, "fapiSigning", "tokenEndpointAuthMethod"),
                _string_at(security_environment or {}, "clientAuthMethod"),
            }:
                keys.extend(_MTLS_KEYS)
        else:
            keys.append(requirement)
    return tuple(keys)


def missing_run_config(
    requirements: Iterable[RunConfigRequirement],
    config: Mapping[str, JsonValue],
    *,
    security_environment: Mapping[str, JsonValue] | None = None,
    ignore: Iterable[RunConfigKey] = (),
) -> tuple[MissingRunConfig, ...]:
    """Return required run config values absent from a plan config.

    Only presence is checked here; format and whole-group consistency stay with
    :func:`conformance.model_bank_config.parse_model_bank_config`.

    Args:
        requirements: Runner dependencies derived from a compiled plan.
        config: Raw plan config (``planSpec.config`` / builder draft config).
        security_environment: Canonical ``securityEnvironment``; a value
            supplied there or in ``config`` counts as present.
        ignore: Values the caller already reports elsewhere.

    Returns:
        One entry per missing value, in display order.
    """
    skipped = set(ignore)
    missing: list[MissingRunConfig] = []
    for key in required_run_config_keys(requirements, config, security_environment=security_environment):
        if key in skipped or _has_value(key, config, security_environment or {}):
            continue
        missing.append(
            MissingRunConfig(
                key=key,
                message=f"{_location_label(key)} is required to run because {RUN_CONFIG_REASONS[key]}.",
            )
        )
    return tuple(missing)


def _location_label(key: RunConfigKey) -> str:
    """Return the canonical plan location named for a missing value.

    Args:
        key: Required config value.

    Returns:
        ``securityEnvironment`` path participants use in exported plans.
    """
    if key in _LOCATION_LABELS:
        return _LOCATION_LABELS[key]
    _source, path = _VALUE_SOURCES[key][0]
    return "securityEnvironment." + ".".join(path)


def _has_value(
    key: RunConfigKey, config: Mapping[str, JsonValue], security_environment: Mapping[str, JsonValue]
) -> bool:
    """Return whether one required config value is supplied anywhere it is accepted.

    Args:
        key: Required config value.
        config: Raw plan config.
        security_environment: Canonical security environment.

    Returns:
        True when any accepted location holds a non-empty string.
    """
    containers = {"config": config, "securityEnvironment": security_environment}
    return any(_string_at(containers[source], *path) for source, path in _VALUE_SOURCES[key])


def _string_at(config: Mapping[str, JsonValue], *path: str) -> str:
    """Return the stripped string at a nested config path.

    Args:
        config: Raw config mapping.
        *path: Keys to follow.

    Returns:
        The string value, or an empty string when absent or not a string.
    """
    value: JsonValue = dict(config)
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value.strip() if isinstance(value, str) else ""
