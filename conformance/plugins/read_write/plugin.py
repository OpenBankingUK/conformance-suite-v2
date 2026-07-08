"""Read/Write API conformance plugin for Open Banking Limited (OBL).

This module provides :class:`ReadWritePlugin`, the concrete implementation of
the :class:`~conformance.plugins.domain.ConformancePlugin` protocol for the
Open Banking Read/Write API specification.

The plugin:

- Covers the ``"obl"`` standard and ``"read-write"`` specification.
- Supports specification version ``"v4.0.1"``.
- Organises endpoints by resource group: AIS, PIS, CBPII, and VRP.
- Loads endpoint catalogues from JSON files bundled under
  ``catalogues/<version_dir>/`` relative to this package.
- Computes a canonical content hash from the combined bytes of all
  resource-group catalogue files for drift detection.

Security note: no participant-controlled values are used in catalogue file
resolution; the version directory mapping is derived solely from the
specification-version string via :func:`_version_to_dir`.
"""

from __future__ import annotations

import json
from pathlib import Path

from conformance.catalogue import (
    Catalogue,
    CatalogueIdentity,
    EndpointCatalogueEntry,
    compute_catalogue_hash,
    parse_catalogue,
)
from conformance.plugins.domain import PluginId, PluginTargetMetadata
from conformance.target_config import TestTargetConfig

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PLUGIN_ID: PluginId = "read-write"
"""Stable unique identifier for the Read/Write plugin."""

_STANDARD: str = "obl"
"""Open Banking Limited standard identifier."""

_SPECIFICATION: str = "read-write"
"""Specification identifier for the Read/Write API."""

_SUPPORTED_VERSIONS: frozenset[str] = frozenset({"v4.0.1"})
"""Specification versions served by this plugin."""

_RESOURCE_GROUPS: tuple[str, ...] = ("ais", "pis", "cbpii", "vrp")
"""Ordered tuple of resource-group identifiers in display order."""

_RESOURCE_GROUP_LABELS: dict[str, str] = {
    "ais": "Accounts and Transactions (AIS)",
    "pis": "Payments (PIS)",
    "cbpii": "Confirmation of Funds (CBPII)",
    "vrp": "Variable Recurring Payments (VRP)",
}
"""Human-readable display labels for each resource group."""

_CATALOGUES_DIR: Path = Path(__file__).parent / "catalogues"
"""Absolute path to the bundled catalogues directory."""

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _version_to_dir(version: str) -> str:
    """Convert a specification version string to a catalogue directory name.

    Replaces dots with underscores so that ``"v4.0.1"`` maps to the
    directory name ``"v4_0_1"`` used for bundled catalogue files.

    Args:
        version: Specification version string (e.g. ``"v4.0.1"``).

    Returns:
        Directory name string with dots replaced by underscores
        (e.g. ``"v4_0_1"``).
    """
    return version.replace(".", "_")


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ReadWritePlugin:
    """Conformance plugin for the Open Banking Read/Write API specification.

    Implements the :class:`~conformance.plugins.domain.ConformancePlugin`
    protocol for the ``"obl"`` standard and ``"read-write"`` specification.
    Supports specification version ``"v4.0.1"``.

    Catalogues are loaded from JSON files bundled alongside this module
    under ``catalogues/<version_dir>/<resource_group>.json``.  The
    content hash returned by :meth:`catalogue_identity` and embedded in
    the :class:`~conformance.catalogue.Catalogue` returned by
    :meth:`load_catalogue` is computed from the concatenated raw bytes of
    all resource-group catalogue files in :data:`_RESOURCE_GROUPS` order.
    This ensures the hash changes whenever any catalogue file is modified.

    The ``"contentHash"`` placeholder values inside the JSON files are
    overridden at load time; callers must not rely on those embedded values.
    """

    @property
    def plugin_id(self) -> PluginId:
        """Return the stable unique identifier for this plugin.

        Returns:
            The string ``"read-write"``.
        """
        return _PLUGIN_ID

    def target_metadata(self) -> PluginTargetMetadata:
        """Return guided-UI hierarchy metadata for the Read/Write plugin.

        Returns:
            :class:`~conformance.plugins.domain.PluginTargetMetadata`
            describing the OBL Read/Write API with supported versions and
            the four resource groups.
        """
        return PluginTargetMetadata(
            plugin_id=_PLUGIN_ID,
            standard=_STANDARD,
            specification=_SPECIFICATION,
            supported_versions=tuple(sorted(_SUPPORTED_VERSIONS)),
            uses_resource_groups=True,
            resource_groups=_RESOURCE_GROUPS,
            display_label="Read/Write API",
        )

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return ``True`` if this plugin handles the given target coordinates.

        Checks that the target's ``standard``, ``specification``, and
        ``specification_version`` all match the values this plugin serves.
        Returns ``False`` without raising for any unrecognised target.

        Args:
            target: The target coordinates to test.

        Returns:
            ``True`` when the target is for the OBL Read/Write API at a
            supported version; ``False`` otherwise.
        """
        return (
            target.standard == _STANDARD
            and target.specification == _SPECIFICATION
            and target.specification_version in _SUPPORTED_VERSIONS
        )

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return the catalogue identity for a given target.

        Computes the content hash from the combined raw bytes of all
        resource-group catalogue files for the requested specification
        version.  The hash changes whenever any catalogue file is modified,
        enabling drift detection without loading the full catalogue.

        Args:
            target: The target coordinates used to select the catalogue
                version directory.

        Returns:
            :class:`~conformance.catalogue.CatalogueIdentity` with the
            combined content hash for the target's specification version.
        """
        combined_bytes = self._combined_catalogue_bytes(target.specification_version)
        return CatalogueIdentity(
            plugin_id=_PLUGIN_ID,
            specification=_SPECIFICATION,
            specification_version=target.specification_version,
            content_hash=compute_catalogue_hash(combined_bytes),
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Load and return the merged endpoint catalogue for a target.

        Loads all four resource-group catalogue files for the requested
        specification version, merges their endpoints into a single
        ordered tuple, and returns a :class:`~conformance.catalogue.Catalogue`
        whose identity carries the combined content hash.

        The ``contentHash`` placeholder values stored inside the individual
        JSON files are NOT used; the hash is always recomputed from the raw
        file bytes.

        Args:
            target: The target coordinates specifying the specification
                version to load.

        Returns:
            A fully validated :class:`~conformance.catalogue.Catalogue`
            containing all endpoint entries for all resource groups at the
            requested version, with a computed content hash.
        """
        version = target.specification_version
        combined_bytes = self._combined_catalogue_bytes(version)
        content_hash = compute_catalogue_hash(combined_bytes)

        all_entries: list[EndpointCatalogueEntry] = []
        for rg in _RESOURCE_GROUPS:
            rg_bytes = self._resource_group_file_bytes(version, rg)
            rg_catalogue = parse_catalogue(json.loads(rg_bytes))
            all_entries.extend(rg_catalogue.endpoints)

        identity = CatalogueIdentity(
            plugin_id=_PLUGIN_ID,
            specification=_SPECIFICATION,
            specification_version=version,
            content_hash=content_hash,
        )
        return Catalogue(identity=identity, endpoints=tuple(all_entries))

    def masking_fields(self) -> frozenset[str]:
        """Return the set of runtime field names that must be masked.

        The Read/Write plugin does not define any additional masking fields
        beyond those handled by the core masking layer.  OAuth tokens and
        credentials are masked by the executor regardless of plugin
        registration.

        Returns:
            An empty frozenset.
        """
        return frozenset()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _catalogue_dir(self, version: str) -> Path:
        """Resolve the catalogues directory for a specification version.

        Args:
            version: Specification version string (e.g. ``"v4.0.1"``).

        Returns:
            Absolute :class:`~pathlib.Path` to the catalogue directory for
            the given version.
        """
        return _CATALOGUES_DIR / _version_to_dir(version)

    def _resource_group_file_bytes(self, version: str, resource_group: str) -> bytes:
        """Read the raw bytes of one resource-group catalogue file.

        Args:
            version: Specification version string (e.g. ``"v4.0.1"``).
            resource_group: Resource-group identifier (e.g. ``"ais"``).

        Returns:
            Raw UTF-8 bytes of the catalogue JSON file.
        """
        file_path = self._catalogue_dir(version) / f"{resource_group}.json"
        return file_path.read_bytes()

    def _combined_catalogue_bytes(self, version: str) -> bytes:
        """Concatenate raw catalogue bytes for all resource groups.

        Reads every resource-group catalogue file in :data:`_RESOURCE_GROUPS`
        order and concatenates their raw bytes.  This combined value is used
        as the input to :func:`~conformance.catalogue.compute_catalogue_hash`
        so the hash reflects the full set of catalogue data.

        Args:
            version: Specification version string (e.g. ``"v4.0.1"``).

        Returns:
            Concatenated raw bytes of all resource-group catalogue files.
        """
        combined = b""
        for rg in _RESOURCE_GROUPS:
            combined += self._resource_group_file_bytes(version, rg)
        return combined
