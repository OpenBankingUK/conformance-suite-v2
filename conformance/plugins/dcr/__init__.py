"""DCR conformance plugin package for Open Banking UK Dynamic Client Registration.

This package implements the :class:`~conformance.plugins.domain.ConformancePlugin`
Protocol for the Open Banking UK DCR specification versions 3.2, 3.3, and 3.4.

Sub-modules:

- :mod:`conformance.plugins.dcr.plugin` — :class:`DcrPlugin` concrete plugin
  implementation, catalogue loading, and masking-field registry.
- :mod:`conformance.plugins.dcr.runner` — :class:`DcrRunner` that orchestrates
  DCR scenarios against a live ASPSP and returns structured results.
- :mod:`conformance.plugins.dcr.scenarios` — :class:`DcrScenario` definitions
  and applicability helpers for all ten DCR scenario IDs.
- :mod:`conformance.plugins.dcr.discovery` — OIDC discovery fetch, validation,
  and auth-method selection per FAPI 1 Advanced requirements.
- :mod:`conformance.plugins.dcr.registration` — DCR registration JWT builder
  (PS256, ``application/jose`` content type).
- :mod:`conformance.plugins.dcr.client_state` — Parsed registration response,
  runtime client state, step evidence, and scenario result types.
- :mod:`conformance.plugins.dcr.token` — Token-endpoint grant helpers for
  ``tls_client_auth`` and ``private_key_jwt`` authentication methods.
- :mod:`conformance.plugins.dcr.schema_validation` — Registration response
  schema validation for each DCR specification version.
"""
