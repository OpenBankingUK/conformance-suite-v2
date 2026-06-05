"""Tests for conformance.headers module."""

import pytest

from conformance.headers import freeze_headers


@pytest.mark.unit
class TestFrozenHeadersLookup:
    """Verify immutable header lookup follows mapping semantics."""

    def test_non_string_key_raises_key_error(self) -> None:
        """Direct lookup with non-string keys raises KeyError."""
        headers = freeze_headers({"Content-Type": "application/json"})

        with pytest.raises(KeyError):
            headers[object()]  # type: ignore[index]  # Intentional non-string lookup regression probe.

    def test_get_returns_default_for_non_string_key(self) -> None:
        """Inherited get returns the supplied default for non-string keys."""
        headers = freeze_headers({"Content-Type": "application/json"})

        assert headers.get(object(), "missing") == "missing"  # type: ignore[call-overload]  # Intentional Mapping.get probe.
