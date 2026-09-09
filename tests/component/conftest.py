"""Fixtures owned by the component suite.

Only component tests reach the API singletons or the deterministic DCR
services, so these fixtures live here rather than in the root conftest. The DCR
fixtures themselves are defined in :mod:`tests.support.dcr_fixtures` because
the unit suite drives the same in-process protocol service.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import run_store
from tests.support import dcr_fixtures
from tests.support.run_execution import StubbedRunExecution

dcr_fixture_materials = dcr_fixtures.dcr_fixture_materials
dcr_protocol_service = dcr_fixtures.dcr_protocol_service
dcr_test_service = dcr_fixtures.dcr_test_service


@pytest.fixture
def api_singleton_stores() -> Iterator[None]:
    """Reset the process-local run and auth-session singletons around one test.

    Only tests that reach the API singletons need this; unit tests that build
    their own :class:`~conformance.api.run_store.RunStore` must not pay for it.

    Yields:
        Control back to pytest while the test executes.
    """
    run_store.reset()
    auth_session_store.reset()
    yield
    run_store.reset()
    auth_session_store.reset()


@pytest.fixture
def stubbed_run_execution(api_singleton_stores: None) -> Iterator[StubbedRunExecution]:
    """Replace background run execution with a stub the test owns.

    Requesting the singleton reset first means the worker is always joined
    before the stores it would touch are cleared.

    Args:
        api_singleton_stores: Singleton reset this fixture must outlive.

    Yields:
        Stub recording every launch made by the test.
    """
    stub = StubbedRunExecution()
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr("conformance.api.run_lifecycle._execute_run", stub)
        yield stub
    stub.join()
