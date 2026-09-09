"""Pytest fixtures that hand out deterministic DCR protocol services.

Both the in-process protocol tests and the loopback mTLS tests need the same
immutable cryptographic material, so the fixtures are defined once here and
re-exported by the narrowest conftest that owns each consuming package.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.dcr_test_service import (
    DcrFixtureMaterials,
    DcrProtocolService,
    DcrTestService,
    create_dcr_fixture_materials,
    running_dcr_test_service,
)


@pytest.fixture(scope="session")
def dcr_fixture_materials(tmp_path_factory: pytest.TempPathFactory) -> DcrFixtureMaterials:
    """Generate the DCR mTLS and protocol material once for the whole session.

    Key generation and certificate signing dominate DCR fixture cost and the
    resulting material is immutable, so it is shared. Every fixture below
    layers fresh mutable protocol state on top of it.

    Args:
        tmp_path_factory: Session-scoped temporary directory factory.

    Returns:
        Immutable mTLS and protocol material.
    """
    return create_dcr_fixture_materials(tmp_path_factory.mktemp("dcr-materials"))


@pytest.fixture
def dcr_protocol_service(dcr_fixture_materials: DcrFixtureMaterials) -> DcrProtocolService:
    """Yield an in-process deterministic DCR protocol service.

    Use this for protocol and orchestration behaviour: it opens no socket and
    performs no TLS handshake, and its mutable state is fresh per test.

    Args:
        dcr_fixture_materials: Shared immutable mTLS and protocol material.

    Returns:
        Protocol service reachable through :meth:`DcrProtocolService.client`.
    """
    return DcrProtocolService(dcr_fixture_materials)


@pytest.fixture
def dcr_test_service(dcr_fixture_materials: DcrFixtureMaterials) -> Iterator[DcrTestService]:
    """Yield an isolated deterministic mTLS DCR protocol service.

    Reserved for tests that exercise transport, TLS, mTLS, certificates, or
    the product's own HTTP client configuration.

    Args:
        dcr_fixture_materials: Shared immutable mTLS and protocol material.

    Yields:
        Running service with fresh mutable state.
    """
    yield from running_dcr_test_service(dcr_fixture_materials)
