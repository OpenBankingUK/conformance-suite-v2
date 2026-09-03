"""Shared local protocol-service fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.dcr_test_service import DcrTestService, running_dcr_test_service


@pytest.fixture
def dcr_test_service(tmp_path: Path) -> Iterator[DcrTestService]:
    """Yield an isolated deterministic mTLS DCR protocol service.

    Args:
        tmp_path: Per-test directory for ephemeral keys and certificates.

    Yields:
        Running service reset to its initial state.
    """
    yield from running_dcr_test_service(tmp_path / "dcr-service")
