"""Environment capability metadata and compatibility helpers for bundled suites."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conformance.model_bank_config import TokenEndpointClientAuthMode
from conformance.suite_catalog import (
    SuiteApiFamily,
    SuiteCatalogKey,
    SuiteMetadata,
    SuiteName,
    SuiteSelection,
    SuiteSpecVersion,
    SuiteStandard,
    list_supported_suites,
)

PsuMode = Literal["manual", "headless", "mobile_qr"]
"""PSU authorisation modes considered by guided compatibility checks."""

CapabilitySupport = Literal["supported", "blocked", "unknown"]
"""Outcome labels for suite/environment compatibility checks."""

CapabilityRequirement = Literal["required", "optional", "not_required"]
"""Requirement level labels for environment configuration dimensions."""

EnvironmentSource = Literal["preset", "custom"]
"""Source labels for known model-bank presets versus participant custom environments."""


@dataclass(frozen=True)
class UnsupportedCombination:
    """Known unsupported suite/auth/environment combination.

    Attributes:
        reason: Human-readable reason shown in launch validation errors.
        standard: Optional suite standard constraint.
        spec_version: Optional suite spec version constraint.
        api: Optional suite API-family constraint.
        suite: Optional suite identifier constraint.
        psu_mode: Optional PSU mode constraint.
        token_endpoint_auth_method: Optional token endpoint auth-method constraint.
    """

    reason: str
    standard: SuiteStandard | None = None
    spec_version: SuiteSpecVersion | None = None
    api: SuiteApiFamily | None = None
    suite: SuiteName | None = None
    psu_mode: PsuMode | None = None
    token_endpoint_auth_method: TokenEndpointClientAuthMode | None = None


@dataclass(frozen=True)
class SuiteEnvironmentCapability:
    """Capability declaration for one bundled suite catalog row.

    Attributes:
        standard: Open Banking standard family accepted by the bundled suite.
        spec_version: Standards specification version accepted by the suite.
        api: API family accepted by the suite.
        suite: Versioned suite identifier.
        supported_psu_modes: PSU modes this suite currently supports.
        supported_token_endpoint_auth_methods: Token endpoint client-auth methods
            this suite currently supports when token exchange applies.
        mtls_requirement: Whether mTLS credentials are required by this suite.
        fapi_signing_requirement: Whether FAPI signing material is required by
            this suite.
        redirect_uri_requirement: Whether redirect URI input is required.
        resource_base_url_requirement: Whether protected-resource base URL input
            is required.
        known_unsupported_combinations: Known blocked combinations tied to this
            suite.
    """

    standard: SuiteStandard
    spec_version: SuiteSpecVersion
    api: SuiteApiFamily
    suite: SuiteName
    supported_psu_modes: frozenset[PsuMode]
    supported_token_endpoint_auth_methods: frozenset[TokenEndpointClientAuthMode]
    mtls_requirement: CapabilityRequirement
    fapi_signing_requirement: CapabilityRequirement
    redirect_uri_requirement: CapabilityRequirement
    resource_base_url_requirement: CapabilityRequirement
    known_unsupported_combinations: tuple[UnsupportedCombination, ...] = ()

    def key(self) -> SuiteCatalogKey:
        """Return the suite-catalog key for this capability row.

        Returns:
            Catalog tuple key for suite metadata lookups.
        """
        return (self.standard, self.spec_version, "fapi1-advanced", self.api, self.suite)


@dataclass(frozen=True)
class EnvironmentDeclaration:
    """Non-secret declared capabilities for one participant environment.

    Attributes:
        supported_standards: Standards known to be available.
        supported_spec_versions: Specification versions known to be available.
        supported_api_families: API families known to be available.
        supported_suites: Suite identifiers known to be launchable.
        supported_psu_modes: PSU modes known to be supported.
        supported_token_endpoint_auth_methods: Token endpoint auth methods known
            to be registered.
        mtls_supported: Whether participant mTLS cert/key auth is supported.
        fapi_signing_supported: Whether request-object/client-assertion signing
            support is available.
        redirect_uri_supported: Whether redirect URI registration constraints are
            satisfied.
        resource_base_url_supported: Whether protected-resource base URLs are
            configured for relevant suites.
        known_unsupported_combinations: Explicit blocked combinations for the
            declared environment.
    """

    supported_standards: frozenset[SuiteStandard] | None = None
    supported_spec_versions: frozenset[SuiteSpecVersion] | None = None
    supported_api_families: frozenset[SuiteApiFamily] | None = None
    supported_suites: frozenset[SuiteName] | None = None
    supported_psu_modes: frozenset[PsuMode] | None = None
    supported_token_endpoint_auth_methods: frozenset[TokenEndpointClientAuthMode] | None = None
    mtls_supported: bool | None = None
    fapi_signing_supported: bool | None = None
    redirect_uri_supported: bool | None = None
    resource_base_url_supported: bool | None = None
    known_unsupported_combinations: tuple[UnsupportedCombination, ...] = ()


@dataclass(frozen=True)
class EnvironmentPreset:
    """Known model-bank environment preset for guided flows.

    Attributes:
        key: Stable preset key used by UI selectors.
        label: Human-readable preset label.
        environment: Default config ``environment`` value.
        discovery_url: Default config ``discoveryUrl`` value.
        declaration: Non-secret capability declaration for this preset.
    """

    key: str
    label: str
    environment: str
    discovery_url: str
    declaration: EnvironmentDeclaration


@dataclass(frozen=True)
class EnvironmentReference:
    """Selected environment information used in compatibility checks.

    Attributes:
        source: Whether the environment is a known preset or a custom value.
        key: Optional stable key for known presets.
        label: Human-readable display label.
        declaration: Optional capability declaration. Custom environments can
            omit this and rely on conservative unknown/warn handling.
    """

    source: EnvironmentSource
    key: str | None
    label: str
    declaration: EnvironmentDeclaration | None


@dataclass(frozen=True)
class CapabilityEvaluation:
    """Compatibility evaluation for a suite/environment/auth combination.

    Attributes:
        support: Evaluation status: ``supported``, ``blocked``, or ``unknown``.
        blockers: Hard-launch blockers for known unsupported combinations.
        warnings: Conservative warnings for unknown custom capabilities.
        suite_capability: Matched suite capability declaration when available.
    """

    support: CapabilitySupport
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    suite_capability: SuiteEnvironmentCapability | None


_TOKEN_AUTH_METHODS: frozenset[TokenEndpointClientAuthMode] = frozenset({"private_key_jwt", "tls_client_auth"})
"""Token endpoint auth methods currently supported by token-exchange AIS suites."""

_TLS_CLIENT_AUTH_METHOD = "tls_client_auth"
"""Token auth enum label used for non-secret method comparisons."""


def _suite_capability_from_metadata(metadata: SuiteMetadata) -> SuiteEnvironmentCapability:
    """Build suite capability defaults from a catalog metadata row.

    Args:
        metadata: Bundled suite catalog metadata row.

    Returns:
        Capability declaration for the selected suite row.
    """
    if metadata.suite == "discovery-jwks":
        return SuiteEnvironmentCapability(
            standard=metadata.standard,
            spec_version=metadata.spec_version,
            api=metadata.api,
            suite=metadata.suite,
            supported_psu_modes=frozenset(),
            supported_token_endpoint_auth_methods=frozenset(),
            mtls_requirement="not_required",
            fapi_signing_requirement="not_required",
            redirect_uri_requirement="not_required",
            resource_base_url_requirement="not_required",
        )
    if metadata.suite == "psu-auth-starter":
        return SuiteEnvironmentCapability(
            standard=metadata.standard,
            spec_version=metadata.spec_version,
            api=metadata.api,
            suite=metadata.suite,
            supported_psu_modes=frozenset({"manual"}),
            supported_token_endpoint_auth_methods=frozenset(),
            mtls_requirement="not_required",
            fapi_signing_requirement="required",
            redirect_uri_requirement="required",
            resource_base_url_requirement="not_required",
            known_unsupported_combinations=(
                UnsupportedCombination(
                    reason="Headless PSU is not yet supported by bundled starter suites.",
                    psu_mode="headless",
                ),
                UnsupportedCombination(
                    reason="Mobile QR PSU is not yet supported by bundled starter suites.",
                    psu_mode="mobile_qr",
                ),
            ),
        )
    if metadata.suite in {
        "ais-certification-slice",
        "ais-certification-baseline",
        "ais-fcs-legacy-benchmark",
        "pis-domestic-payment-starter",
        "pis-fcs-legacy-benchmark",
    }:
        return SuiteEnvironmentCapability(
            standard=metadata.standard,
            spec_version=metadata.spec_version,
            api=metadata.api,
            suite=metadata.suite,
            supported_psu_modes=frozenset({"manual"}),
            supported_token_endpoint_auth_methods=_TOKEN_AUTH_METHODS,
            mtls_requirement="optional",
            fapi_signing_requirement="required",
            redirect_uri_requirement="required",
            resource_base_url_requirement="required",
            known_unsupported_combinations=(
                UnsupportedCombination(
                    reason="Headless PSU is not yet supported by current AIS certification bundles.",
                    psu_mode="headless",
                ),
                UnsupportedCombination(
                    reason="Mobile QR PSU is not yet supported by current AIS certification bundles.",
                    psu_mode="mobile_qr",
                ),
            ),
        )
    return SuiteEnvironmentCapability(
        standard=metadata.standard,
        spec_version=metadata.spec_version,
        api=metadata.api,
        suite=metadata.suite,
        supported_psu_modes=frozenset(),
        supported_token_endpoint_auth_methods=frozenset(),
        mtls_requirement="optional",
        fapi_signing_requirement="optional",
        redirect_uri_requirement="optional",
        resource_base_url_requirement="optional",
    )


_BUNDLED_SUITE_CAPABILITIES: dict[SuiteCatalogKey, SuiteEnvironmentCapability] = {
    capability.key(): capability
    for capability in (_suite_capability_from_metadata(metadata) for metadata in list_supported_suites())
}
"""Capability metadata keyed by each bundled suite catalog row."""


_OZONE_PRESET = EnvironmentPreset(
    key="ozone-obie-preprod",
    label="Ozone OBIE pre-production",
    environment="ozone-model-bank",
    discovery_url="https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
    declaration=EnvironmentDeclaration(
        supported_standards=frozenset({"ob-read-write"}),
        supported_spec_versions=frozenset({"v3.1.11", "v4.0", "v4.0.1"}),
        supported_api_families=frozenset({"ais", "pis", "cbpii", "vrp"}),
        supported_suites=frozenset(
            {
                "discovery-jwks",
                "psu-auth-starter",
                "pis-domestic-payment-starter",
                "pis-fcs-legacy-benchmark",
                "ais-certification-slice",
                "ais-certification-baseline",
                "ais-fcs-legacy-benchmark",
            }
        ),
        supported_psu_modes=frozenset({"manual"}),
        supported_token_endpoint_auth_methods=_TOKEN_AUTH_METHODS,
        mtls_supported=True,
        fapi_signing_supported=True,
        redirect_uri_supported=True,
        resource_base_url_supported=True,
    ),
)
"""Known guided-flow model-bank preset with non-secret capability declarations."""


_MODEL_BANK_PRESETS: tuple[EnvironmentPreset, ...] = (_OZONE_PRESET,)
"""Deterministic list of known model-bank environment presets."""


def list_suite_environment_capabilities() -> tuple[SuiteEnvironmentCapability, ...]:
    """Return capability metadata for every bundled suite catalog row.

    Returns:
        Suite capability declarations in deterministic key order.
    """
    return tuple(_BUNDLED_SUITE_CAPABILITIES[key] for key in sorted(_BUNDLED_SUITE_CAPABILITIES))


def resolve_suite_environment_capability(selection: SuiteSelection) -> SuiteEnvironmentCapability | None:
    """Resolve capability metadata for a selected bundled suite.

    Args:
        selection: Validated suite selection from participant configuration.

    Returns:
        Matching suite capability metadata, or ``None`` for unsupported keys.
    """
    key: SuiteCatalogKey = (
        selection.standard,
        selection.spec_version,
        selection.profile,
        selection.api,
        selection.suite,
    )
    return _BUNDLED_SUITE_CAPABILITIES.get(key)


def list_environment_presets() -> tuple[EnvironmentPreset, ...]:
    """Return known model-bank environment presets.

    Returns:
        Preset declarations available for guided environment selection.
    """
    return _MODEL_BANK_PRESETS


def resolve_environment_preset(key: str) -> EnvironmentPreset | None:
    """Resolve a known model-bank preset by key.

    Args:
        key: Preset key used by UI selectors.

    Returns:
        Matching preset, or ``None`` when not known.
    """
    return next((preset for preset in _MODEL_BANK_PRESETS if preset.key == key), None)


def make_custom_environment_reference(
    *,
    label: str,
    declaration: EnvironmentDeclaration | None = None,
) -> EnvironmentReference:
    """Build a custom-environment reference for compatibility checks.

    Args:
        label: Human-readable environment label.
        declaration: Optional custom capability declaration.

    Returns:
        Custom environment reference with conservative unknown handling when no
        declaration is supplied.
    """
    return EnvironmentReference(source="custom", key=None, label=label, declaration=declaration)


def make_preset_environment_reference(preset_key: str) -> EnvironmentReference | None:
    """Build an environment reference from a known preset key.

    Args:
        preset_key: Known preset key.

    Returns:
        Preset environment reference, or ``None`` when key is unknown.
    """
    preset = resolve_environment_preset(preset_key)
    if preset is None:
        return None
    return EnvironmentReference(source="preset", key=preset.key, label=preset.label, declaration=preset.declaration)


def evaluate_suite_environment_support(
    *,
    selection: SuiteSelection,
    environment: EnvironmentReference,
    psu_mode: PsuMode | None = None,
    token_endpoint_auth_method: TokenEndpointClientAuthMode | None = None,
) -> CapabilityEvaluation:
    """Evaluate support for a suite/auth/environment combination.

    Args:
        selection: Suite selection under evaluation.
        environment: Selected environment reference and declarations.
        psu_mode: Optional PSU mode selected for later auth-bundle flow.
        token_endpoint_auth_method: Optional token-endpoint auth method selected
            for later auth-bundle flow.

    Returns:
        Compatibility evaluation with blockers, warnings, and support status.
    """
    suite_capability = resolve_suite_environment_capability(selection)
    if suite_capability is None:
        return CapabilityEvaluation(
            support="blocked",
            blockers=("Suite selection is not part of the bundled suite catalog.",),
            warnings=(),
            suite_capability=None,
        )

    blockers: list[str] = []
    warnings: list[str] = []
    declaration = environment.declaration

    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None if declaration is None else declaration.supported_standards,
        selected_value=selection.standard,
        dimension="standard",
    )
    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None if declaration is None else declaration.supported_spec_versions,
        selected_value=selection.spec_version,
        dimension="spec version",
    )
    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None if declaration is None else declaration.supported_api_families,
        selected_value=selection.api,
        dimension="API family",
    )
    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None if declaration is None else declaration.supported_suites,
        selected_value=selection.suite,
        dimension="suite",
    )

    if psu_mode is not None:
        _evaluate_psu_mode(
            blockers=blockers,
            warnings=warnings,
            environment=environment,
            suite_capability=suite_capability,
            psu_mode=psu_mode,
        )

    if token_endpoint_auth_method is not None:
        _evaluate_token_auth_method(
            blockers=blockers,
            warnings=warnings,
            environment=environment,
            suite_capability=suite_capability,
            token_endpoint_auth_method=token_endpoint_auth_method,
        )

    if _requires_capability(suite_capability.redirect_uri_requirement):
        _evaluate_boolean_capability(
            blockers=blockers,
            warnings=warnings,
            source=environment.source,
            declared_value=None if declaration is None else declaration.redirect_uri_supported,
            dimension="redirect URI registration",
        )

    if _requires_capability(suite_capability.resource_base_url_requirement):
        _evaluate_boolean_capability(
            blockers=blockers,
            warnings=warnings,
            source=environment.source,
            declared_value=None if declaration is None else declaration.resource_base_url_supported,
            dimension="resource base URL",
        )

    if _requires_capability(suite_capability.fapi_signing_requirement):
        _evaluate_boolean_capability(
            blockers=blockers,
            warnings=warnings,
            source=environment.source,
            declared_value=None if declaration is None else declaration.fapi_signing_supported,
            dimension="FAPI signing",
        )

    for unsupported in suite_capability.known_unsupported_combinations:
        if _matches_unsupported(
            unsupported,
            selection=selection,
            psu_mode=psu_mode,
            token_endpoint_auth_method=token_endpoint_auth_method,
        ):
            blockers.append(unsupported.reason)

    if declaration is not None:
        for unsupported in declaration.known_unsupported_combinations:
            if _matches_unsupported(
                unsupported,
                selection=selection,
                psu_mode=psu_mode,
                token_endpoint_auth_method=token_endpoint_auth_method,
            ):
                blockers.append(unsupported.reason)

    if blockers:
        return CapabilityEvaluation(
            support="blocked",
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(dict.fromkeys(warnings)),
            suite_capability=suite_capability,
        )

    if warnings:
        return CapabilityEvaluation(
            support="unknown",
            blockers=(),
            warnings=tuple(dict.fromkeys(warnings)),
            suite_capability=suite_capability,
        )

    return CapabilityEvaluation(
        support="supported",
        blockers=(),
        warnings=(),
        suite_capability=suite_capability,
    )


def _check_declared_set(
    *,
    blockers: list[str],
    warnings: list[str],
    source: EnvironmentSource,
    declared_values: frozenset[str] | None,
    selected_value: str,
    dimension: str,
) -> None:
    """Validate one selected value against a declared support set.

    Args:
        blockers: Mutable blocker accumulator.
        warnings: Mutable warning accumulator.
        source: Environment source label.
        declared_values: Optional declared support set.
        selected_value: Selected value under validation.
        dimension: Human-readable dimension label.
    """
    if declared_values is None:
        if source == "custom":
            warnings.append(f"Custom environment capability for {dimension} is undeclared; compatibility is unknown.")
        return
    if selected_value not in declared_values:
        blockers.append(f"Selected {dimension} '{selected_value}' is not declared as supported by the environment.")


def _evaluate_psu_mode(
    *,
    blockers: list[str],
    warnings: list[str],
    environment: EnvironmentReference,
    suite_capability: SuiteEnvironmentCapability,
    psu_mode: PsuMode,
) -> None:
    """Validate a selected PSU mode against suite and environment capability data.

    Args:
        blockers: Mutable blocker accumulator.
        warnings: Mutable warning accumulator.
        environment: Selected environment reference.
        suite_capability: Matched suite capability declaration.
        psu_mode: Selected PSU mode.
    """
    if not suite_capability.supported_psu_modes:
        blockers.append(f"Suite '{suite_capability.suite}' does not consume PSU mode selections.")
        return
    if psu_mode not in suite_capability.supported_psu_modes:
        blockers.append(f"PSU mode '{psu_mode}' is not supported by suite '{suite_capability.suite}'.")
        return
    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None if environment.declaration is None else environment.declaration.supported_psu_modes,
        selected_value=psu_mode,
        dimension="PSU mode",
    )


def _evaluate_token_auth_method(
    *,
    blockers: list[str],
    warnings: list[str],
    environment: EnvironmentReference,
    suite_capability: SuiteEnvironmentCapability,
    token_endpoint_auth_method: TokenEndpointClientAuthMode,
) -> None:
    """Validate a selected token auth method against suite and environment data.

    Args:
        blockers: Mutable blocker accumulator.
        warnings: Mutable warning accumulator.
        environment: Selected environment reference.
        suite_capability: Matched suite capability declaration.
        token_endpoint_auth_method: Selected token endpoint auth method.
    """
    if not suite_capability.supported_token_endpoint_auth_methods:
        blockers.append(f"Suite '{suite_capability.suite}' does not consume token endpoint auth-method selections.")
        return
    if token_endpoint_auth_method not in suite_capability.supported_token_endpoint_auth_methods:
        blockers.append(
            "Token endpoint auth method "
            f"'{token_endpoint_auth_method}' is not supported by suite "
            f"'{suite_capability.suite}'."
        )
        return
    _check_declared_set(
        blockers=blockers,
        warnings=warnings,
        source=environment.source,
        declared_values=None
        if environment.declaration is None
        else environment.declaration.supported_token_endpoint_auth_methods,
        selected_value=token_endpoint_auth_method,
        dimension="token endpoint auth method",
    )
    if token_endpoint_auth_method == _TLS_CLIENT_AUTH_METHOD:  # noqa: S105 - enum label, not a secret
        _evaluate_boolean_capability(
            blockers=blockers,
            warnings=warnings,
            source=environment.source,
            declared_value=None if environment.declaration is None else environment.declaration.mtls_supported,
            dimension="mTLS client credentials",
        )


def _evaluate_boolean_capability(
    *,
    blockers: list[str],
    warnings: list[str],
    source: EnvironmentSource,
    declared_value: bool | None,
    dimension: str,
) -> None:
    """Validate one required boolean capability.

    Args:
        blockers: Mutable blocker accumulator.
        warnings: Mutable warning accumulator.
        source: Environment source label.
        declared_value: Optional declared capability value.
        dimension: Human-readable capability label.
    """
    if declared_value is True:
        return
    if declared_value is False:
        blockers.append(f"Environment declaration marks {dimension} as unsupported.")
        return
    if source == "custom":
        warnings.append(f"Custom environment capability for {dimension} is undeclared; compatibility is unknown.")


def _requires_capability(requirement: CapabilityRequirement) -> bool:
    """Return whether a requirement level is mandatory for compatibility checks.

    Args:
        requirement: Requirement label on suite capability metadata.

    Returns:
        ``True`` when the requirement label is ``required``.
    """
    return requirement == "required"


def _matches_unsupported(
    unsupported: UnsupportedCombination,
    *,
    selection: SuiteSelection,
    psu_mode: PsuMode | None,
    token_endpoint_auth_method: TokenEndpointClientAuthMode | None,
) -> bool:
    """Return whether a known unsupported declaration matches the selection.

    Args:
        unsupported: Unsupported-combination declaration to evaluate.
        selection: Selected suite metadata values.
        psu_mode: Optional selected PSU mode.
        token_endpoint_auth_method: Optional selected token endpoint auth
            method.

    Returns:
        ``True`` when all declared filters match the selected values.
    """
    if unsupported.standard is not None and unsupported.standard != selection.standard:
        return False
    if unsupported.spec_version is not None and unsupported.spec_version != selection.spec_version:
        return False
    if unsupported.api is not None and unsupported.api != selection.api:
        return False
    if unsupported.suite is not None and unsupported.suite != selection.suite:
        return False
    if unsupported.psu_mode is not None and unsupported.psu_mode != psu_mode:
        return False
    return not (
        unsupported.token_endpoint_auth_method is not None
        and unsupported.token_endpoint_auth_method != token_endpoint_auth_method
    )
