"""Narrow metadata registry for supported Open Banking specification families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type ScopePresentation = Literal["resource-groups", "direct-endpoints"]
"""Participant scope presentation owned by a specification family."""

type ExecutionSchedulingPolicy = Literal["dependency-ordered", "sequential"]
"""Execution scheduling policy declared by a specification family."""

type SpecificationSecurityProfile = Literal["all", "fapi1-advanced", "fapi2"]
"""Security profiles that a specification version can declare."""


@dataclass(frozen=True)
class SpecificationVersionDefinition:
    """Catalogue binding for one participant-facing specification version.

    Attributes:
        version: Canonical participant-facing specification version.
        catalogue_standard: Internal catalogue standard key.
        catalogue_version: Internal catalogue version key.
        catalogue_apis: Internal catalogue API families backing the version.
        security_profiles: Security profiles valid for this exact version.
    """

    version: str
    catalogue_standard: str
    catalogue_version: str
    catalogue_apis: tuple[str, ...]
    security_profiles: tuple[SpecificationSecurityProfile, ...]


@dataclass(frozen=True)
class SpecificationDefinition:
    """Metadata for one supported Open Banking UK specification family.

    Attributes:
        scheme: Canonical participant-facing scheme identifier.
        scheme_display_name: Participant-facing scheme label.
        family: Canonical plan family discriminator.
        specification: Canonical specification identifier.
        display_name: Participant-facing specification label.
        versions: Supported versions and their internal catalogue bindings.
        uses_resource_groups: Whether plans select endpoints inside resource
            groups rather than directly.
        scope_presentation: Builder presentation policy for endpoint scope.
        execution_scheduling: Compiler/runtime scheduling policy.
    """

    scheme: str
    scheme_display_name: str
    family: str
    specification: str
    display_name: str
    versions: tuple[SpecificationVersionDefinition, ...]
    uses_resource_groups: bool
    scope_presentation: ScopePresentation
    execution_scheduling: ExecutionSchedulingPolicy


_OPEN_BANKING_READ_WRITE = SpecificationDefinition(
    scheme="open-banking-uk",
    scheme_display_name="Open Banking UK",
    family="OBL_READ_WRITE",
    specification="read-write",
    display_name="Read/Write",
    versions=tuple(
        SpecificationVersionDefinition(
            version=version,
            catalogue_standard="open-banking",
            catalogue_version="v4.0",
            catalogue_apis=("ais", "pis", "cbpii", "vrp"),
            security_profiles=("fapi1-advanced",),
        )
        for version in ("4.0.1", "4.0.0", "4.0")
    ),
    uses_resource_groups=True,
    scope_presentation="resource-groups",
    execution_scheduling="dependency-ordered",
)
"""Open Banking UK Read/Write family metadata."""

_OPEN_BANKING_DCR = SpecificationDefinition(
    scheme="open-banking-uk",
    scheme_display_name="Open Banking UK",
    family="OBL_DCR",
    specification="dynamic-client-registration",
    display_name="Dynamic Client Registration",
    versions=(
        SpecificationVersionDefinition(
            version="3.4",
            catalogue_standard="open-banking",
            catalogue_version="v3.4",
            catalogue_apis=("dcr",),
            security_profiles=("all",),
        ),
    ),
    uses_resource_groups=False,
    scope_presentation="direct-endpoints",
    execution_scheduling="sequential",
)
"""Open Banking UK Dynamic Client Registration 3.4 metadata."""

_SPECIFICATIONS = (_OPEN_BANKING_READ_WRITE, _OPEN_BANKING_DCR)
"""Supported specification definitions in stable participant-facing order."""


def supported_specifications() -> tuple[SpecificationDefinition, ...]:
    """Return supported Open Banking specification definitions.

    Returns:
        Immutable definitions in stable display order.
    """
    return _SPECIFICATIONS


def specification_for_family(family: str) -> SpecificationDefinition:
    """Resolve a canonical plan family discriminator.

    Args:
        family: Canonical plan family value.

    Returns:
        Matching specification definition.

    Raises:
        ValueError: If the family is unsupported.
    """
    for definition in _SPECIFICATIONS:
        if definition.family == family:
            return definition
    supported = ", ".join(definition.family for definition in _SPECIFICATIONS)
    raise ValueError(f"specification.family must be one of: {supported}")


def specification_for_boundary(
    scheme: str,
    specification: str,
    version: str,
) -> tuple[SpecificationDefinition, SpecificationVersionDefinition]:
    """Resolve a scheme/specification/version boundary.

    Args:
        scheme: Canonical participant-facing scheme identifier.
        specification: Canonical participant-facing specification identifier.
        version: Participant-facing specification version.

    Returns:
        Matching family definition and version binding.

    Raises:
        ValueError: If the boundary or version is unsupported.
    """
    for definition in _SPECIFICATIONS:
        if definition.scheme != scheme or definition.specification != specification:
            continue
        for version_definition in definition.versions:
            if version_definition.version == version:
                return definition, version_definition
        supported_versions = ", ".join(item.version for item in definition.versions)
        raise ValueError(f"specification.version must be one of: {supported_versions}")
    supported = ", ".join(f"{item.scheme}/{item.specification}" for item in _SPECIFICATIONS)
    raise ValueError(f"specification boundary must be one of: {supported}")


def security_profiles_for_boundary(
    scheme: str,
    specification: str,
    version: str,
) -> tuple[SpecificationSecurityProfile, ...]:
    """Return security profiles declared for an exact specification boundary.

    Args:
        scheme: Canonical participant-facing scheme identifier.
        specification: Canonical participant-facing specification identifier.
        version: Participant-facing specification version.

    Returns:
        Security profiles valid for the selected specification version.

    Raises:
        ValueError: If the boundary or version is unsupported.
    """
    _definition, version_definition = specification_for_boundary(scheme, specification, version)
    return version_definition.security_profiles


def derived_security_profile_for_boundary(
    scheme: str,
    specification: str,
    version: str,
) -> SpecificationSecurityProfile:
    """Derive the sole security profile for a specification boundary.

    Args:
        scheme: Canonical participant-facing scheme identifier.
        specification: Canonical participant-facing specification identifier.
        version: Participant-facing specification version.

    Returns:
        The only security profile declared for the selected version.

    Raises:
        ValueError: If the boundary is unsupported or does not declare exactly
            one security profile.
    """
    profiles = security_profiles_for_boundary(scheme, specification, version)
    if len(profiles) != 1:
        raise ValueError(
            "specification boundary must declare exactly one security profile "
            f"for automatic derivation; found {len(profiles)}"
        )
    return profiles[0]
