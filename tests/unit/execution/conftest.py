"""DCR protocol fixtures for the in-process execution unit tests.

The definitions live in :mod:`tests.support.dcr_fixtures` because the component
suite needs the same services; this conftest only makes them visible to the
unit package that consumes them.
"""

from __future__ import annotations

from tests.support import dcr_fixtures

dcr_fixture_materials = dcr_fixtures.dcr_fixture_materials
dcr_protocol_service = dcr_fixtures.dcr_protocol_service
