"""Bundled DCR catalogue JSON files.

This package contains the versioned catalogue JSON documents for the Open
Banking UK Dynamic Client Registration specification:

- ``dcr-3.2.json`` — DCR version 3.2 catalogue
- ``dcr-3.3.json`` — DCR version 3.3 catalogue
- ``dcr-3.4.json`` — DCR version 3.4 catalogue

These files are loaded at runtime by
:class:`~conformance.plugins.dcr.plugin.DcrPlugin` via
:mod:`importlib.resources`.
"""
