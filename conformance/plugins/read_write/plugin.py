"""Read/Write API conformance plugin for Open Banking Limited (OBL).

This module provides :class:`ReadWritePlugin`, the concrete implementation of
the :class:`~conformance.plugins.domain.ConformancePlugin` protocol for the
Open Banking Read/Write API specification.

The plugin:

- Covers the ``"obl"`` standard and ``"read-write"`` specification.
- Supports specification versions ``"v3.1.11"``, ``"v4.0.0"``, and
  ``"v4.0.1"``.  The legacy migration alias ``"v4.0"`` resolves to the
  canonical patch-explicit version ``"v4.0.0"``.
- Organises endpoints by resource group: AIS, PIS, CBPII, and VRP.
- Loads consolidated schema v2 catalogues from
  ``catalogues/<version_dir>/catalogue.json`` relative to this package.
- Computes a canonical content hash from the consolidated catalogue bytes for
  drift detection so executable tests and policy metadata affect drift.

Security note: no participant-controlled values are used in catalogue file
resolution; the version directory mapping is derived solely from the
specification-version string via :func:`_version_to_dir`.
"""

from __future__ import annotations

import json
from pathlib import Path

from conformance.catalogue import Catalogue, CatalogueIdentity, compute_catalogue_hash, parse_catalogue
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

_SUPPORTED_VERSIONS: tuple[str, ...] = ("v3.1.11", "v4.0.0", "v4.0.1")
"""Specification versions served by this plugin."""

_VERSION_ALIASES: dict[str, str] = {"v4.0": "v4.0.0"}
"""Temporary migration aliases normalised to patch-explicit versions."""

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
    Supports specification versions ``"v3.1.11"``, ``"v4.0.0"``, and
    ``"v4.0.1"``.

    Catalogues are loaded from JSON files bundled alongside this module
    under ``catalogues/<version_dir>/catalogue.json``.  The
    content hash returned by :meth:`catalogue_identity` and embedded in
    the :class:`~conformance.catalogue.Catalogue` returned by
    :meth:`load_catalogue` is computed from the consolidated catalogue bytes.
    This ensures the hash changes whenever endpoint, executable test, masking,
    field schema, or readiness policy metadata is modified.

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
            supported_versions=_SUPPORTED_VERSIONS,
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
            and _normalise_version(target.specification_version) in _SUPPORTED_VERSIONS
        )

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return the catalogue identity for a given target.

        Computes the content hash from the consolidated catalogue JSON bytes
        for the requested specification version.  The hash changes whenever
        endpoints, executable tests, masking metadata, field schemas, or
        readiness policy change.

        Args:
            target: The target coordinates used to select the catalogue
                version directory.

        Returns:
            :class:`~conformance.catalogue.CatalogueIdentity` with the live
            content hash for the target's canonical specification version.
        """
        version = _normalise_version(target.specification_version)
        catalogue_bytes = self._catalogue_bytes(version)
        return CatalogueIdentity(
            plugin_id=_PLUGIN_ID,
            specification=_SPECIFICATION,
            specification_version=version,
            content_hash=compute_catalogue_hash(catalogue_bytes),
            standard=_STANDARD,
            security_profile=target.security_profile,
            version_aliases=tuple(alias for alias, canonical in _VERSION_ALIASES.items() if canonical == version),
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Load and return the consolidated catalogue for a target.

        Loads the schema v2 catalogue file for the requested specification
        version and returns a :class:`~conformance.catalogue.Catalogue` whose
        identity carries the recomputed live content hash.

        The ``contentHash`` placeholder stored inside the JSON file is NOT
        used; the hash is always recomputed from the raw catalogue bytes.

        Args:
            target: The target coordinates specifying the specification
                version to load.

        Returns:
            A fully validated :class:`~conformance.catalogue.Catalogue`
            containing all endpoint entries for all resource groups at the
            requested version, with a computed content hash.
        """
        version = _normalise_version(target.specification_version)
        catalogue_bytes = self._catalogue_bytes(version)
        content_hash = compute_catalogue_hash(catalogue_bytes)
        raw_catalogue = json.loads(catalogue_bytes)
        catalogue = parse_catalogue(raw_catalogue)
        identity = CatalogueIdentity(
            plugin_id=catalogue.identity.plugin_id,
            specification=catalogue.identity.specification,
            specification_version=version,
            content_hash=content_hash,
            standard=catalogue.identity.standard,
            security_profile=catalogue.identity.security_profile,
            version_aliases=catalogue.identity.version_aliases,
        )
        return Catalogue(
            identity=identity,
            endpoints=catalogue.endpoints,
            schema_version=catalogue.schema_version,
            resource_groups=catalogue.resource_groups,
            field_schemas=catalogue.field_schemas,
            runner_primitives=catalogue.runner_primitives,
            executable_tests=catalogue.executable_tests,
            readiness_policy=catalogue.readiness_policy,
            masking=catalogue.masking,
            source_coverage=catalogue.source_coverage,
        )

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

    def _catalogue_bytes(self, version: str) -> bytes:
        """Read the raw bytes of one consolidated catalogue file.

        Args:
            version: Specification version string (e.g. ``"v4.0.1"``).

        Returns:
            Raw UTF-8 bytes of the catalogue JSON file.

        Raises:
            ValueError: If ``version`` is not a supported canonical version.
        """
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported Read/Write specification version {version!r}")
        file_path = self._catalogue_dir(version) / "catalogue.json"
        return file_path.read_bytes()


def _normalise_version(version: str) -> str:
    """Normalise a Read/Write specification version to its canonical form.

    Args:
        version: Specification version string from a target.

    Returns:
        Patch-explicit canonical version string.
    """
    return _VERSION_ALIASES.get(version, version)
