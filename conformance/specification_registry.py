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
class OpenApiDocumentUpdate:
    """One published OpenAPI ("swagger") document update for a specification version.

    Open Banking UK Read/Write core specification pages only change with a
    specification version bump, but the OpenAPI documents representing a version
    are periodically republished upstream as releases/updates. The original
    publication is called ``Baseline``; later publications keep the historical
    upstream terminology (``Release-N`` before ``Update-N``).

    Attributes:
        specification_version: Participant-facing specification version, for
            example ``"4.0.0"``.
        update: Plan value identifying the update, for example ``"Baseline"``,
            ``"Release-2"`` or ``"Update-3"``.
        upstream_tag: ``OpenBankingUK/read-write-api-specs`` git tag that
            published the update.
        upstream_commit: Immutable upstream commit the bundled snapshot was
            retrieved from.
    """

    specification_version: str
    update: str
    upstream_tag: str
    upstream_commit: str

    @property
    def catalogue_id(self) -> str:
        """Return the stable catalogue identifier, for example ``v4.0.0-Release-2``."""
        return f"v{self.specification_version}-{self.update}"

    @property
    def label(self) -> str:
        """Return the short label shown once a version is chosen, for example ``Release 2``."""
        return self.update.replace("-", " ")

    @property
    def display_name(self) -> str:
        """Return the full display name, for example ``v4.0.0 Release 2``."""
        return f"v{self.specification_version} {self.label}"


@dataclass(frozen=True)
class SpecificationVersionDefinition:
    """Catalogue binding for one participant-facing specification version.

    Attributes:
        version: Canonical participant-facing specification version.
        catalogue_standard: Internal catalogue standard key.
        catalogue_version: Endpoint (API path) version of the backing
            catalogues, for example ``v4.0`` for both 4.0.0 and 4.0.1.
        catalogue_apis: Internal catalogue API families backing the version.
        security_profiles: Security profiles valid for this exact version.
        openapi_document_updates: Selectable OpenAPI document updates in
            publication order. Empty when the specification does not publish
            selectable OpenAPI document updates.
    """

    version: str
    catalogue_standard: str
    catalogue_version: str
    catalogue_apis: tuple[str, ...]
    security_profiles: tuple[SpecificationSecurityProfile, ...]
    openapi_document_updates: tuple[OpenApiDocumentUpdate, ...] = ()

    @property
    def endpoint_version(self) -> str:
        """Return the endpoint (API path) version backing this specification version."""
        return self.catalogue_version


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
    versions=(
        SpecificationVersionDefinition(
            version="4.0.1",
            catalogue_standard="open-banking",
            catalogue_version="v4.0",
            catalogue_apis=("ais", "pis", "cbpii", "vrp"),
            security_profiles=("fapi1-advanced",),
            openapi_document_updates=(
                OpenApiDocumentUpdate("4.0.1", "Baseline", "v4.0.1", "2011fb5ec95e90df2e23143e8c6db643f03a1c2a"),
                OpenApiDocumentUpdate(
                    "4.0.1", "Update-1", "v4.0.1-Update-1", "484a1795680d9852600247878b9fd2bfe584c0aa"
                ),
            ),
        ),
        SpecificationVersionDefinition(
            version="4.0.0",
            catalogue_standard="open-banking",
            catalogue_version="v4.0",
            catalogue_apis=("ais", "pis", "cbpii", "vrp"),
            security_profiles=("fapi1-advanced",),
            openapi_document_updates=(
                OpenApiDocumentUpdate("4.0.0", "Baseline", "v4.0", "37b77536a7955065d57ca7f9dbd7466918a063e2"),
                OpenApiDocumentUpdate(
                    "4.0.0", "Release-2", "v4.0-Release-2", "bafb0aa00b9d7c1ea8876d7e958a3e1f67b3b36e"
                ),
                OpenApiDocumentUpdate("4.0.0", "Update-3", "v4.0-Update-3", "b6a61af57dfb58f7f8ad4570e7e5d9ea42343df2"),
                OpenApiDocumentUpdate("4.0.0", "Update-4", "v4.0-Update-4", "28811990e053ba2a308812131d67001803382414"),
                OpenApiDocumentUpdate("4.0.0", "Update-5", "v4.0-Update-5", "5af5621e1b68dda5a0d821f2ffee9d282cfe550a"),
            ),
        ),
        SpecificationVersionDefinition(
            version="3.1.11",
            catalogue_standard="open-banking",
            catalogue_version="v3.1",
            catalogue_apis=("ais", "pis", "cbpii", "vrp"),
            security_profiles=("fapi1-advanced",),
            openapi_document_updates=(
                # The upstream v3.1.11 tag was later moved to the Release 5 commit; Baseline is pinned to the
                # commit originally published as the v3.1.11 release on 2023-06-19.
                OpenApiDocumentUpdate("3.1.11", "Baseline", "v3.1.11", "a2e935d8bcf4728214ad516fd914b808573c91b6"),
                OpenApiDocumentUpdate("3.1.11", "Release-2", "v3.1.11r2", "914462422259b5125d57601fb18c28a58735045e"),
                OpenApiDocumentUpdate("3.1.11", "Release-3", "v3.1.11r3", "ebbc72cf1e80a59e2eea1eb713cb79c6039c07c1"),
                OpenApiDocumentUpdate("3.1.11", "Release-4", "v3.1.11r4", "3fde4d10ce3f0cc1473d84c326ec7edd024b3708"),
                OpenApiDocumentUpdate("3.1.11", "Release-5", "v3.1.11r5", "6b06eb9f5b319618828151dd637ddff5c00965c2"),
            ),
        ),
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


def openapi_document_update_for_boundary(
    scheme: str,
    specification: str,
    version: str,
    update: str | None,
) -> OpenApiDocumentUpdate | None:
    """Resolve and validate the OpenAPI document update selected for a boundary.

    Args:
        scheme: Canonical participant-facing scheme identifier.
        specification: Canonical participant-facing specification identifier.
        version: Participant-facing specification version.
        update: Selected update value, for example ``"Update-1"``, or ``None``
            when the plan did not declare one.

    Returns:
        The matching update, or ``None`` when the specification version does
        not publish selectable OpenAPI document updates and none was supplied.

    Raises:
        ValueError: If the boundary is unsupported, an update is required but
            missing, supplied but unsupported, or not published for the version.
    """
    _definition, version_definition = specification_for_boundary(scheme, specification, version)
    updates = version_definition.openapi_document_updates
    if not updates:
        if update is not None:
            raise ValueError(f"specification.openApiDocumentUpdate is not supported for {specification} {version}")
        return None
    supported = ", ".join(item.update for item in updates)
    if update is None:
        raise ValueError(f"specification.openApiDocumentUpdate is required for {version}; must be one of: {supported}")
    for item in updates:
        if item.update == update:
            return item
    raise ValueError(f"specification.openApiDocumentUpdate must be one of: {supported} for {version}")


def specification_version_for_catalogue(
    *,
    standard: str,
    endpoint_version: str,
    specification_version: str,
    api: str | None,
) -> SpecificationVersionDefinition | None:
    """Return the registry version binding backing a catalogue boundary.

    Args:
        standard: Internal catalogue standard key.
        endpoint_version: Catalogue endpoint (API path) version.
        specification_version: Participant-facing specification version.
        api: Catalogue API family, or ``None`` to match any API family bound
            to the version (used for catalogues such as cVRP that share a
            specification version's OpenAPI documents without being a
            selectable plan boundary).

    Returns:
        The matching version definition, or ``None`` for catalogues outside
        the registry (for example ad-hoc test catalogues).
    """
    for definition in _SPECIFICATIONS:
        for version_definition in definition.versions:
            if (
                version_definition.catalogue_standard == standard
                and version_definition.catalogue_version == endpoint_version
                and version_definition.version == specification_version
                and (api is None or api in version_definition.catalogue_apis)
            ):
                return version_definition
    return None


def latest_openapi_document_update(version_definition: SpecificationVersionDefinition) -> OpenApiDocumentUpdate | None:
    """Return the most recently published OpenAPI document update for a version.

    Args:
        version_definition: Specification version binding.

    Returns:
        The last update in publication order, or ``None`` when the version has
        no selectable updates.
    """
    updates = version_definition.openapi_document_updates
    return updates[-1] if updates else None


def all_openapi_document_updates() -> tuple[OpenApiDocumentUpdate, ...]:
    """Return every bundled OpenAPI document update across all specifications.

    Returns:
        Updates in registry display order.
    """
    return tuple(
        update
        for definition in _SPECIFICATIONS
        for version_definition in definition.versions
        for update in version_definition.openapi_document_updates
    )


def openapi_document_update_by_catalogue_id(catalogue_id: str) -> OpenApiDocumentUpdate:
    """Resolve an OpenAPI document update by its catalogue identifier.

    Args:
        catalogue_id: Identifier such as ``"v4.0.1-Update-1"``.

    Returns:
        Matching update.

    Raises:
        ValueError: If the identifier is unknown.
    """
    for update in all_openapi_document_updates():
        if update.catalogue_id == catalogue_id:
            return update
    raise ValueError(f"Unknown OpenAPI document update: {catalogue_id}")
