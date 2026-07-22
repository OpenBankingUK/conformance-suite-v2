"""DCR ConformancePlugin implementation for the Open Banking UK DCR specification.

:class:`DcrPlugin` satisfies the
:class:`~conformance.plugins.domain.ConformancePlugin` Protocol and provides:

- Catalogue loading for DCR specification versions 3.2, 3.3, and 3.4.
- Target applicability checking (``standard="obl"``,
  ``specification="dynamic-client-registration"``).
- Drift-detection catalogue identity from live SHA-256 file hashes.
- The set of runtime field names that must be masked in evidence.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

from conformance.catalogue import Catalogue, CatalogueIdentity, compute_catalogue_hash, parse_catalogue
from conformance.plugins.domain import ConformancePlugin, PluginId, PluginTargetMetadata
from conformance.target_config import TestTargetConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLUGIN_ID: PluginId = "dcr"
"""Stable plugin identifier used in catalogue documents and diagnostic messages."""

_SPECIFICATION = "dynamic-client-registration"
"""Open Banking specification identifier for DCR."""

_STANDARD = "obl"
"""Open Banking Limited standard identifier."""

_SUPPORTED_VERSIONS: tuple[str, ...] = ("3.2", "3.3", "3.4")
"""Tuple of DCR specification version strings supported by this plugin."""

_CATALOGUES_PACKAGE = "conformance.plugins.dcr.catalogues"
"""Python package path for the bundled DCR catalogue JSON files."""

# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class DcrPlugin:
    """Open Banking UK DCR conformance plugin.

    Implements the :class:`~conformance.plugins.domain.ConformancePlugin`
    structural Protocol for the DCR specification.  Supports versions 3.2,
    3.3, and 3.4 of the OBIE Dynamic Client Registration specification.

    DCR does not use resource groups — operations (POST /register,
    GET /register/{clientId}, etc.) are directly selectable in the plan-builder
    UI without a resource-group grouping level.

    Usage::

        from conformance.plugins.dcr.plugin import DcrPlugin
        from conformance.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(DcrPlugin())
    """

    @property
    def plugin_id(self) -> PluginId:
        """Return the stable DCR plugin identifier.

        Returns:
            The string ``"dcr"``.
        """
        return PLUGIN_ID

    def target_metadata(self) -> PluginTargetMetadata:
        """Return guided-UI hierarchy metadata for the DCR plugin.

        Returns:
            :class:`~conformance.plugins.domain.PluginTargetMetadata` describing
            the ``obl`` standard, ``dynamic-client-registration`` specification,
            supported versions 3.2/3.3/3.4, and no resource groups.
        """
        return PluginTargetMetadata(
            plugin_id=PLUGIN_ID,
            standard=_STANDARD,
            specification=_SPECIFICATION,
            supported_versions=_SUPPORTED_VERSIONS,
            uses_resource_groups=False,
            resource_groups=(),
            display_label="Dynamic Client Registration",
        )

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return ``True`` when the target coordinates match this plugin.

        Args:
            target: The target coordinates to check.

        Returns:
            ``True`` when ``target.standard == "obl"``,
            ``target.specification == "dynamic-client-registration"``, and
            ``target.specification_version`` is one of the supported versions.
        """
        return (
            target.standard == _STANDARD
            and target.specification == _SPECIFICATION
            and target.specification_version in _SUPPORTED_VERSIONS
        )

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return the live drift-detection identity for the DCR catalogue.

        Reads the bundled catalogue JSON bytes and computes a fresh SHA-256
        hash so the caller can detect catalogue drift between plan-build time
        and execution time.

        Args:
            target: Target coordinates used to select the catalogue file
                (version is used to pick ``dcr-{version}.json``).

        Returns:
            :class:`~conformance.catalogue.CatalogueIdentity` with a freshly
            computed ``content_hash``.

        Raises:
            ValueError: If ``target.specification_version`` is not supported.
        """
        raw_bytes = self._load_catalogue_bytes(target.specification_version)
        content_hash = compute_catalogue_hash(raw_bytes)
        return CatalogueIdentity(
            plugin_id=PLUGIN_ID,
            specification=_SPECIFICATION,
            specification_version=target.specification_version,
            content_hash=content_hash,
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Load and parse the DCR catalogue for a given specification version.

        Args:
            target: Target coordinates; ``specification_version`` selects the
                catalogue file.

        Returns:
            A validated :class:`~conformance.catalogue.Catalogue` for the
            requested version.

        Raises:
            ValueError: If ``target.specification_version`` is not supported.
            conformance.catalogue.CatalogueParseError: If the bundled catalogue
                file is structurally invalid.
        """
        raw_bytes = self._load_catalogue_bytes(target.specification_version)
        content_hash = compute_catalogue_hash(raw_bytes)
        raw_json = json.loads(raw_bytes)
        catalogue = parse_catalogue(raw_json)
        identity = CatalogueIdentity(
            plugin_id=catalogue.identity.plugin_id,
            specification=catalogue.identity.specification,
            specification_version=catalogue.identity.specification_version,
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
        """Return the runtime field names that must be masked in DCR evidence.

        Extends the standard masking set with DCR-specific fields:
        ``registration_access_token`` and ``client_secret`` as returned by the
        ASPSP in DCR registration responses.

        Returns:
            Frozenset of field-name strings requiring masking in persisted
            results and execution logs.
        """
        return frozenset(
            {
                "registration_access_token",
                "client_secret",
                "access_token",
                "client_assertion",
            }
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _load_catalogue_bytes(self, version: str) -> bytes:
        """Load raw catalogue JSON bytes from the bundled package resources.

        Args:
            version: DCR specification version string (e.g. ``"3.3"``).

        Returns:
            Raw JSON bytes of the catalogue file.

        Raises:
            ValueError: If ``version`` is not one of the supported versions.
        """
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported DCR specification version {version!r}; expected one of {sorted(_SUPPORTED_VERSIONS)}"
            )
        catalogue_file = f"dcr-{version}.json"
        try:
            package_ref = importlib.resources.files(_CATALOGUES_PACKAGE)
            resource = package_ref.joinpath(catalogue_file)
            return resource.read_bytes()
        except (FileNotFoundError, ModuleNotFoundError, TypeError) as exc:
            # Fallback to path-relative loading for environments where
            # importlib.resources is not set up (e.g. editable installs).
            fallback_path = Path(__file__).parent / "catalogues" / catalogue_file
            if fallback_path.exists():
                return fallback_path.read_bytes()
            raise ValueError(f"DCR catalogue file {catalogue_file!r} not found in package resources") from exc


# ---------------------------------------------------------------------------
# Module-level singleton (optional convenience)
# ---------------------------------------------------------------------------

_default_plugin: DcrPlugin | None = None


def get_dcr_plugin() -> DcrPlugin:
    """Return the module-level singleton :class:`DcrPlugin` instance.

    Creates the instance on first call and caches it for subsequent calls.
    Use this in application startup code to avoid creating multiple plugin
    instances.

    Returns:
        The module-level :class:`DcrPlugin` singleton.
    """
    global _default_plugin  # noqa: PLW0603
    if _default_plugin is None:
        _default_plugin = DcrPlugin()
    return _default_plugin


# Satisfy the ConformancePlugin Protocol at type-check time.
_: ConformancePlugin = DcrPlugin()
