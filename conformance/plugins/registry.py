"""Internal plugin registry: registration and target-based resolution.

The :class:`PluginRegistry` holds the ordered list of registered
:class:`~conformance.plugins.domain.ConformancePlugin` instances and provides
:meth:`~PluginRegistry.resolve` to find the right plugin for a given
:class:`~conformance.target_config.TestTargetConfig`.

At Stage 1 the registry is a simple ordered collection.  Concrete plugin
instances (Read/Write, DCR) will be registered in later stages; the registry
itself does not depend on any concrete implementation.

Usage pattern::

    registry = PluginRegistry()
    registry.register(my_read_write_plugin)
    registry.register(my_dcr_plugin)

    plugin = registry.resolve(target)  # finds the first plugin that supports target
"""

from __future__ import annotations

from conformance.plugins.domain import ConformancePlugin, PluginId
from conformance.target_config import TestTargetConfig


class PluginRegistryError(LookupError):
    """Raised when a plugin cannot be found or a registration conflict occurs.

    Wraps :class:`LookupError` so callers can catch either the specific error
    or the generic base class.
    """


class PluginRegistry:
    """Ordered registry of internal conformance plugins.

    Plugins are stored in registration order.  :meth:`resolve` iterates in
    that order and returns the first plugin whose
    :meth:`~conformance.plugins.domain.ConformancePlugin.supports_target`
    returns ``True``.

    Thread safety: the registry is intended to be populated at startup and
    read-only thereafter.  Concurrent :meth:`register` calls from multiple
    threads are not safe without external locking.
    """

    def __init__(self) -> None:
        """Initialise an empty plugin registry."""
        self._plugins: list[ConformancePlugin] = []

    def register(self, plugin: ConformancePlugin) -> None:
        """Register a plugin with the registry.

        Raises :class:`PluginRegistryError` if a plugin with the same
        :attr:`~conformance.plugins.domain.ConformancePlugin.plugin_id` has
        already been registered.

        Args:
            plugin: The plugin instance to register.

        Raises:
            PluginRegistryError: If a plugin with the same ``plugin_id`` is
                already registered.
        """
        existing_ids = {p.plugin_id for p in self._plugins}
        if plugin.plugin_id in existing_ids:
            raise PluginRegistryError(f"A plugin with id {plugin.plugin_id!r} is already registered")
        self._plugins.append(plugin)

    def resolve(self, target: TestTargetConfig) -> ConformancePlugin:
        """Find the first registered plugin that supports a given target.

        Iterates over registered plugins in registration order and returns the
        first one whose :meth:`~conformance.plugins.domain.ConformancePlugin.supports_target`
        method returns ``True`` for ``target``.

        Args:
            target: The target coordinates to find a plugin for.

        Returns:
            The first matching :class:`~conformance.plugins.domain.ConformancePlugin`.

        Raises:
            PluginRegistryError: If no registered plugin supports the given
                target.
        """
        for plugin in self._plugins:
            if plugin.supports_target(target):
                return plugin
        raise PluginRegistryError(
            f"No plugin found for target: standard={target.standard!r}, "
            f"specification={target.specification!r}, "
            f"version={target.specification_version!r}"
        )

    def get(self, plugin_id: PluginId) -> ConformancePlugin:
        """Look up a registered plugin by its stable identifier.

        Args:
            plugin_id: The plugin identifier to look up.

        Returns:
            The registered :class:`~conformance.plugins.domain.ConformancePlugin`
            with the given ``plugin_id``.

        Raises:
            PluginRegistryError: If no plugin with the given ``plugin_id`` is
                registered.
        """
        for plugin in self._plugins:
            if plugin.plugin_id == plugin_id:
                return plugin
        raise PluginRegistryError(f"No plugin registered with id {plugin_id!r}")

    @property
    def plugin_ids(self) -> tuple[PluginId, ...]:
        """Return the ordered tuple of registered plugin IDs.

        Returns:
            Tuple of plugin ID strings in registration order.
        """
        return tuple(p.plugin_id for p in self._plugins)
