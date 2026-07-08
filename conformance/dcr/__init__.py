"""DCR (Dynamic Client Registration) native Python plugin package.

This package implements Open Banking UK Dynamic Client Registration natively
in Python, using the legacy Go DCR tool as behavioural reference.

At Stage 1 this package exposes:

- :mod:`conformance.dcr.credentials` — file-backed credential paths and
  runtime credential loader;
- :mod:`conformance.dcr.transport` — mTLS transport configuration.

Concrete DCR execution logic, registration JWT signing, client lifecycle, and
scenario coverage will be added in Phase 5 of the implementation plan.
"""
