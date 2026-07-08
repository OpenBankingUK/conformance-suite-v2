"""Endpoint catalogues for the Open Banking Read/Write API specification v4.0.1.

Contains one JSON catalogue file per resource group (``ais.json``,
``pis.json``, ``cbpii.json``, ``vrp.json``) and a ``catalogue_index.json``
that lists the available resource groups.  Each catalogue file conforms to the
:class:`~conformance.catalogue.Catalogue` schema and is loaded at run time by
:class:`~conformance.plugins.read_write.plugin.ReadWritePlugin`.
"""
