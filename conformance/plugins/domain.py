"""ConformancePlugin Protocol and supporting plugin domain types.

Every internal plugin must satisfy the :class:`ConformancePlugin` Protocol.
At Stage 1 the Protocol defines the full interface contract; concrete
implementations (Read/Write, DCR) will be added in later stages.

A plugin provides:

- :meth:`ConformancePlugin.plugin_id` — stable identifier string;
- :meth:`ConformancePlugin.target_metadata` — hierarchy metadata for guided UI;
- :meth:`ConformancePlugin.supports_target` — target applicability predicate;
- :meth:`ConformancePlugin.catalogue_identity` — identity/hash of the
  plugin's bundled catalogue;
- :meth:`ConformancePlugin.load_catalogue` — validated :class:`Catalogue`
  for a given target;
- :meth:`ConformancePlugin.masking_fields` — frozenset of field names whose
  runtime values must be masked in persisted evidence.

:class:`PluginTargetMetadata` carries the guided-UI hierarchy metadata (which
standards/specifications/versions the plugin covers, whether it uses resource
groups, and what those groups are).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from conformance.catalogue import Catalogue, CatalogueIdentity
from conformance.target_config import TestTargetConfig

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

type PluginId = str
"""Stable unique identifier string for a registered plugin (e.g. ``"read-write"``).

Plugin IDs are kebab-case ASCII strings.  They appear in catalogue
:class:`~conformance.catalogue.CatalogueIdentity` records and in diagnostic
messages.
"""

# ---------------------------------------------------------------------------
# Plugin metadata dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginTargetMetadata:
    """Guided-UI hierarchy metadata contributed by a plugin.

    Describes the set of (standard, specification, version) coordinates the
    plugin covers, whether it exposes resource groups, and what those groups
    are.  This data drives the Standard → Specification → Version step
    rendering in the plan-builder UI without the UI needing to know concrete
    plugin details.

    Attributes:
        plugin_id: Owning plugin identifier (e.g. ``"read-write"``).
        standard: The standard this plugin covers (e.g. ``"obl"``).
        specification: The specification this plugin covers
            (e.g. ``"read-write"``).
        supported_versions: Ordered tuple of specification version strings
            the plugin supports.
        uses_resource_groups: ``True`` when the specification organises its
            endpoints by resource group (Read/Write).  ``False`` for
            specifications that expose operations directly (DCR).
        resource_groups: Ordered tuple of resource-group identifiers exposed
            by this plugin.  Empty when :attr:`uses_resource_groups` is
            ``False``.
        display_label: Human-readable label for the specification shown in the
            guided UI (e.g. ``"Read/Write API"``).
    """

    plugin_id: PluginId
    standard: str
    specification: str
    supported_versions: tuple[str, ...]
    uses_resource_groups: bool
    resource_groups: tuple[str, ...] = field(default_factory=tuple)
    display_label: str = ""


# ---------------------------------------------------------------------------
# Plugin Protocol
# ---------------------------------------------------------------------------


class ConformancePlugin(Protocol):
    """Structural interface that every internal conformance plugin must satisfy.

    This is a Python :class:`typing.Protocol` (structural subtyping).  Concrete
    plugins do not need to inherit from this class; they only need to provide
    the attributes and methods listed here with matching signatures.

    ``plugin_id`` is the primary key used by :class:`PluginRegistry` for
    registration and lookup.  It must be unique across all registered plugins
    and must match the ``pluginId`` field in the plugin's catalogue documents.
    """

    @property
    def plugin_id(self) -> PluginId:
        """Return the stable unique identifier for this plugin.

        Returns:
            Kebab-case plugin ID string (e.g. ``"read-write"``).
        """
        ...

    def target_metadata(self) -> PluginTargetMetadata:
        """Return guided-UI hierarchy metadata for this plugin.

        Returns:
            :class:`PluginTargetMetadata` describing the standard, specification,
            supported versions, and resource-group structure.
        """
        ...

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return ``True`` if this plugin can serve the given target coordinates.

        The plugin should check ``target.standard``, ``target.specification``,
        and ``target.specification_version`` against its supported values.  It
        must not raise on an unrecognised target — it should simply return
        ``False``.

        Args:
            target: The target coordinates from a Run Plan v2 or plan-builder
                submission.

        Returns:
            ``True`` when this plugin owns the target; ``False`` otherwise.
        """
        ...

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return the identity of the catalogue this plugin provides for a target.

        The caller can use the returned :class:`~conformance.catalogue.CatalogueIdentity`
        to check for catalogue drift (comparing :attr:`~conformance.catalogue.CatalogueIdentity.content_hash`
        against a saved Run Plan v2) before loading the full catalogue.

        Args:
            target: The target coordinates to resolve the catalogue identity
                for.

        Returns:
            :class:`~conformance.catalogue.CatalogueIdentity` for the catalogue
            that covers the given target.
        """
        ...

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Load and return the validated catalogue for a target.

        The returned :class:`~conformance.catalogue.Catalogue` contains all
        endpoint entries, requirement levels, and display labels for the
        selected specification version and standard.

        Args:
            target: The target coordinates to load the catalogue for.

        Returns:
            A fully validated :class:`~conformance.catalogue.Catalogue`
            instance.
        """
        ...

    def masking_fields(self) -> frozenset[str]:
        """Return the set of runtime field names whose values must be masked.

        These are field names (as used in :class:`~conformance.run_plan_v2.EndpointSelection`
        ``field_values`` maps or in executor evidence) whose runtime values
        must never appear unmasked in persisted results, execution logs, or
        API responses.

        Returns:
            Frozenset of field-name strings requiring masking.
        """
        ...
