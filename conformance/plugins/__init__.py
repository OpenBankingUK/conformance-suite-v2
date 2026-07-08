"""Internal conformance plugin framework.

Plugins are the unit of extension in the endpoint-first conformance architecture.
Each plugin packages a versioned JSON catalogue, target metadata for guided-UI
hierarchy, a plan compiler that maps selected endpoint coverage to executable
tests, and masking metadata for any sensitive runtime values.

This package exposes the :class:`~conformance.plugins.domain.ConformancePlugin`
protocol, the :class:`~conformance.plugins.domain.PluginId` type alias, and the
:class:`~conformance.plugins.registry.PluginRegistry` collection.  Concrete
plugin implementations live under sub-packages of this package or alongside
their respective catalogues.

See :mod:`conformance.plugins.domain` for the full plugin interface contract and
:mod:`conformance.plugins.registry` for registration and resolution helpers.
"""
