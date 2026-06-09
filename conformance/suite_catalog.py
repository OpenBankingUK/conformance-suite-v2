"""Resolve config-selected conformance suites to bundled manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import cast

from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import Manifest, ManifestError, parse_manifest
from conformance.model_bank_config import (
    SuiteApiFamily,
    SuiteName,
    SuiteProfile,
    SuiteSelection,
    SuiteSpecVersion,
    SuiteStandard,
)


class SuiteCatalogError(ValueError):
    """Raised when a config-selected suite cannot be resolved from the catalog."""


type SuiteCatalogKey = tuple[SuiteStandard, SuiteSpecVersion, SuiteProfile, SuiteApiFamily, SuiteName]
"""Tuple key used to map a validated config suite selection to a bundled manifest."""


@dataclass(frozen=True)
class SuiteMetadata:
    """Display and trace metadata for a bundled suite catalog entry.

    Attributes:
        catalog_id: Stable identifier for the catalog entry and bundled
            manifest resource.
        label: Human-readable display label for CLI/API/UI surfaces.
        standard: Open Banking standard family covered by the suite.
        spec_version: Standards specification version covered by the suite.
        profile: Security profile scoped by the suite.
        api: API family covered by the suite.
        suite: Versioned smoke/conformance suite identifier.
        manifest_resource: Package resource name for the bundled manifest.
        description: Short scope note for participants and logs.
    """

    catalog_id: str
    label: str
    standard: SuiteStandard
    spec_version: SuiteSpecVersion
    profile: SuiteProfile
    api: SuiteApiFamily
    suite: SuiteName
    manifest_resource: str
    description: str

    def to_json_object(self) -> JsonObject:
        """Convert suite metadata into the public result/log JSON shape.

        Returns:
            JSON object containing the catalog identifiers and suite selection
            fields safe to expose in participant-visible reports and logs.
        """
        return {
            "catalogId": self.catalog_id,
            "manifestResource": self.manifest_resource,
            "standard": self.standard,
            "specVersion": self.spec_version,
            "profile": self.profile,
            "api": self.api,
            "suite": self.suite,
        }


@dataclass(frozen=True)
class ResolvedSuite:
    """Parsed manifest and metadata for a config-selected suite.

    Attributes:
        metadata: Display and trace metadata describing the catalog entry.
        manifest: Parsed bundled manifest ready for plan construction or
            execution.
    """

    metadata: SuiteMetadata
    manifest: Manifest


@dataclass(frozen=True)
class _CatalogEntry:
    """Internal catalog row pointing a suite key at a bundled resource.

    Attributes:
        key: Version/profile/API/suite tuple accepted by participant config.
        resource_name: JSON manifest resource stored in
            :mod:`conformance.suites`.
        label: Human-readable display label.
        description: Short note describing the bundled suite scope.
    """

    key: SuiteCatalogKey
    resource_name: str
    label: str
    description: str


_RESOURCE_PACKAGE = "conformance.suites"
"""Importable package containing bundled suite manifest JSON resources."""

_SMOKE_SUITE_DESCRIPTION = (
    "Smoke-level OpenID discovery and JWKS checks for the selected Open Banking Read/Write version; "
    "this is not full Read/Write API certification coverage."
)
"""Shared description for the discovery-jwks catalog entries, which are intentionally smoke-scoped."""

_PSU_AUTH_STARTER_DESCRIPTION = (
    "OpenID discovery, JWKS fetch, and manual PSU authorisation starter suite for the selected "
    "Open Banking Read/Write version; this is not full Read/Write API certification coverage. "
    "The bundled redirectUri is resolved from participant config and must be registered with the ASPSP."
)
"""Description for the psu-auth-starter catalog entries.

Explicitly states that the suite is non-certifying and that the bundled
``redirectUri`` config value must be registered with the ASPSP before use.
"""

_AIS_CERTIFICATION_SLICE_DESCRIPTION = (
    "Certification-grade Open Banking Read/Write v4.0 AIS slice covering discovery, JWKS, manual PSU "
    "authorisation, token exchange, account-access consent creation, and a protected accounts resource "
    "check. The bundled suite remains partial coverage and must not be treated as full Read/Write API "
    "certification."
)
"""Description for the partial v4 AIS conformance proof-point suite."""

_AIS_CERTIFICATION_SLICE_V401_DESCRIPTION = (
    "Certification-grade Open Banking Read/Write v4.0.1 AIS slice covering discovery, JWKS, manual PSU "
    "authorisation, token exchange, account-access consent creation, and a protected accounts resource "
    "check. The bundled suite remains partial coverage and must not be treated as full Read/Write API "
    "certification."
)
"""Description for the partial v4.0.1 AIS conformance proof-point suite."""

_AIS_CERTIFICATION_BASELINE_DESCRIPTION = (
    "Certification-track Open Banking Read/Write v4.0 AIS baseline suite covering the current certifiable "
    "foundation for discovery, JWKS, manual PSU authorisation, token exchange, consent creation, and core "
    "account-resource checks. The bundled suite remains partial coverage until the Standards-approved v4 AIS "
    "mandatory matrix is fully applied."
)
"""Description for the partial v4 AIS certification baseline suite."""

_AIS_CERTIFICATION_BASELINE_V401_DESCRIPTION = (
    "Certification-track Open Banking Read/Write v4.0.1 AIS baseline suite covering the current certifiable "
    "foundation for discovery, JWKS, manual PSU authorisation, token exchange, consent creation, and core "
    "account-resource checks. The bundled suite remains partial coverage until the Standards-approved v4 AIS "
    "mandatory matrix is fully applied."
)
"""Description for the partial v4.0.1 AIS certification baseline suite."""

_CATALOG_ENTRIES: tuple[_CatalogEntry, ...] = (
    _CatalogEntry(
        key=("ob-read-write", "v3.1.11", "fapi1-advanced", "ais", "discovery-jwks"),
        resource_name="ob-read-write-v3.1.11-fapi1-advanced-discovery-jwks.json",
        label="Open Banking Read/Write v3.1.11 FAPI 1 Advanced discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v3.1.11", "fapi1-advanced", "ais", "psu-auth-starter"),
        resource_name="ob-read-write-v3.1.11-fapi1-advanced-psu-auth-starter.json",
        label="Open Banking Read/Write v3.1.11 FAPI 1 Advanced PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "ais", "ais-certification-baseline"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-ais-certification-baseline.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced AIS certification baseline",
        description=_AIS_CERTIFICATION_BASELINE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "ais", "ais-certification-slice"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-ais-certification-slice.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced AIS certification slice",
        description=_AIS_CERTIFICATION_SLICE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "ais", "discovery-jwks"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-discovery-jwks.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "ais", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "cbpii", "discovery-jwks"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-cbpii-discovery-jwks.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced CBPII discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "cbpii", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-cbpii-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced CBPII PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "pis", "discovery-jwks"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-pis-discovery-jwks.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced PIS discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "pis", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-pis-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced PIS PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "vrp", "discovery-jwks"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-vrp-discovery-jwks.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced VRP discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0", "fapi1-advanced", "vrp", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0-fapi1-advanced-vrp-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0 FAPI 1 Advanced VRP PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "ais", "ais-certification-baseline"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-ais-certification-baseline.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced AIS certification baseline",
        description=_AIS_CERTIFICATION_BASELINE_V401_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "ais", "ais-certification-slice"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-ais-certification-slice.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced AIS certification slice",
        description=_AIS_CERTIFICATION_SLICE_V401_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "ais", "discovery-jwks"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-discovery-jwks.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "ais", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "cbpii", "discovery-jwks"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-cbpii-discovery-jwks.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced CBPII discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "cbpii", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-cbpii-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced CBPII PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "pis", "discovery-jwks"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-pis-discovery-jwks.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced PIS discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "pis", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-pis-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced PIS PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "vrp", "discovery-jwks"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-vrp-discovery-jwks.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced VRP discovery/JWKS smoke suite",
        description=_SMOKE_SUITE_DESCRIPTION,
    ),
    _CatalogEntry(
        key=("ob-read-write", "v4.0.1", "fapi1-advanced", "vrp", "psu-auth-starter"),
        resource_name="ob-read-write-v4.0.1-fapi1-advanced-vrp-psu-auth-starter.json",
        label="Open Banking Read/Write v4.0.1 FAPI 1 Advanced VRP PSU auth starter suite",
        description=_PSU_AUTH_STARTER_DESCRIPTION,
    ),
)
"""Bundled suite catalog rows, kept in deterministic key order."""

_CATALOG_BY_KEY: dict[SuiteCatalogKey, _CatalogEntry] = {entry.key: entry for entry in _CATALOG_ENTRIES}
"""Lookup table from validated suite selection key to catalog entry."""


def list_supported_suites() -> tuple[SuiteMetadata, ...]:
    """Return supported suite metadata in deterministic order.

    Returns:
        Tuple of catalog metadata rows sorted by suite key.
    """
    return tuple(_metadata_from_entry(entry) for entry in sorted(_CATALOG_ENTRIES, key=lambda entry: entry.key))


def resolve_suite(selection: SuiteSelection) -> ResolvedSuite:
    """Resolve a participant config suite selection to a parsed bundled manifest.

    Args:
        selection: Validated suite selection from ``ModelBankConfig.test_suite``.

    Returns:
        Parsed bundled manifest and display metadata for the selected suite.

    Raises:
        SuiteCatalogError: If the selection is unsupported, the bundled
            manifest resource is missing, or the bundled manifest is invalid.
    """
    key = _selection_key(selection)
    entry = _CATALOG_BY_KEY.get(key)
    if entry is None:
        raise SuiteCatalogError(
            "Unsupported suite selection: "
            f"standard={selection.standard}, specVersion={selection.spec_version}, "
            f"profile={selection.profile}, api={selection.api}, suite={selection.suite}"
        )
    return ResolvedSuite(metadata=_metadata_from_entry(entry), manifest=_load_manifest(entry))


def _selection_key(selection: SuiteSelection) -> SuiteCatalogKey:
    """Convert a typed suite selection into the catalog tuple key.

    Args:
        selection: Validated suite selection from participant config.

    Returns:
        Tuple key used by the catalog lookup table.
    """
    return (selection.standard, selection.spec_version, selection.profile, selection.api, selection.suite)


def _metadata_from_entry(entry: _CatalogEntry) -> SuiteMetadata:
    """Build display metadata from an internal catalog entry.

    Args:
        entry: Catalog row to render as public metadata.

    Returns:
        Public metadata for API/UI/CLI display and later result metadata.
    """
    standard, spec_version, profile, api, suite = entry.key
    catalog_id = "/".join((standard, spec_version, profile, suite))
    if api != "ais":
        catalog_id = "/".join((standard, spec_version, profile, api, suite))
    return SuiteMetadata(
        catalog_id=catalog_id,
        label=entry.label,
        standard=standard,
        spec_version=spec_version,
        profile=profile,
        api=api,
        suite=suite,
        manifest_resource=entry.resource_name,
        description=entry.description,
    )


def _load_manifest(entry: _CatalogEntry) -> Manifest:
    """Read and parse the bundled manifest for a catalog entry.

    Args:
        entry: Catalog row whose resource should be loaded.

    Returns:
        Parsed and validated manifest from the bundled resource.

    Raises:
        SuiteCatalogError: If the resource cannot be read, decoded, or parsed
            as a valid conformance manifest.
    """
    raw_manifest = _decode_json_manifest(
        _read_resource_text(entry.resource_name),
        resource_name=entry.resource_name,
    )
    try:
        return parse_manifest(raw_manifest)
    except ManifestError as error:
        raise SuiteCatalogError(f"Invalid bundled suite manifest {entry.resource_name}: {error}") from error


def _read_resource_text(resource_name: str) -> str:
    """Read a text resource from the bundled suite package.

    Args:
        resource_name: JSON file name within :mod:`conformance.suites`.

    Returns:
        UTF-8 text contents of the bundled resource.

    Raises:
        SuiteCatalogError: If the package or resource cannot be read.
    """
    try:
        return resources.files(_RESOURCE_PACKAGE).joinpath(resource_name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:
        raise SuiteCatalogError(f"Bundled suite manifest resource not found: {resource_name}") from error
    except OSError as error:
        raise SuiteCatalogError(f"Unable to read bundled suite manifest {resource_name}: {error}") from error


def _decode_json_manifest(text: str, *, resource_name: str) -> dict[str, JsonValue]:
    """Decode a bundled manifest JSON resource into an object.

    Args:
        text: Raw UTF-8 text read from the bundled resource.
        resource_name: Resource name used in error messages.

    Returns:
        JSON object ready for manifest validation.

    Raises:
        SuiteCatalogError: If the resource is malformed JSON or does not
            decode to a JSON object.
    """
    try:
        raw_manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise SuiteCatalogError(f"Invalid JSON in bundled suite manifest {resource_name}: {error.msg}") from error
    if not isinstance(raw_manifest, dict):
        raise SuiteCatalogError(f"Bundled suite manifest {resource_name} must be a JSON object")
    return cast(dict[str, JsonValue], raw_manifest)
