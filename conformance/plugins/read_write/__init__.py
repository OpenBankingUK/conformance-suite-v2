"""Open Banking Read/Write API v4 conformance plugin.

This sub-package contains the :class:`~conformance.plugins.read_write.plugin.ReadWritePlugin`
implementation of the :class:`~conformance.plugins.domain.ConformancePlugin` protocol, plus
the versioned JSON endpoint catalogues for the Open Banking Read/Write API specification.

Catalogue files live under ``catalogues/v4_0_1/`` and are loaded at run time
by :class:`~conformance.plugins.read_write.plugin.ReadWritePlugin`.  Each
resource-group catalogue (AIS, PIS, CBPII, VRP) is a standalone JSON document
conforming to the :class:`~conformance.catalogue.Catalogue` schema.
"""
