"""Unit tests for the plugin domain Protocol and PluginTargetMetadata."""

from __future__ import annotations

import pytest

from conformance.catalogue import Catalogue, CatalogueIdentity
from conformance.plugins.domain import ConformancePlugin, PluginTargetMetadata
from conformance.target_config import TestTargetConfig


# ---------------------------------------------------------------------------
# Minimal stub plugin for structural subtyping tests
# ---------------------------------------------------------------------------


class _StubPlugin:
    """Minimal implementation of ConformancePlugin for structural subtyping."""

    @property
    def plugin_id(self) -> str:
        """Return the stub plugin id."""
        return "stub"

    def target_metadata(self) -> PluginTargetMetadata:
        """Return stub target metadata."""
        return PluginTargetMetadata(
            plugin_id="stub",
            standard="obl",
            specification="read-write",
            supported_versions=("v4.0.1",),
            uses_resource_groups=True,
            resource_groups=("ais",),
            display_label="Stub Plugin",
        )

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return True for matching read-write v4.0.1 targets."""
        return (
            target.standard == "obl"
            and target.specification == "read-write"
            and target.specification_version in ("v4.0.1",)
        )

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return a stub catalogue identity."""
        return CatalogueIdentity(
            plugin_id="stub",
            specification="read-write",
            specification_version=target.specification_version,
            content_hash="sha256:abc123",
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Return an empty stub catalogue."""
        return Catalogue(
            identity=self.catalogue_identity(target),
            endpoints=(),
        )

    def masking_fields(self) -> frozenset[str]:
        """Return an empty masking field set."""
        return frozenset()


# ---------------------------------------------------------------------------
# PluginTargetMetadata construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plugin_target_metadata_basic() -> None:
    meta = PluginTargetMetadata(
        plugin_id="read-write",
        standard="obl",
        specification="read-write",
        supported_versions=("v3.1.11", "v4.0", "v4.0.1"),
        uses_resource_groups=True,
        resource_groups=("ais", "pis", "cbpii", "vrp"),
        display_label="Read/Write API",
    )
    assert meta.plugin_id == "read-write"
    assert meta.standard == "obl"
    assert meta.specification == "read-write"
    assert meta.supported_versions == ("v3.1.11", "v4.0", "v4.0.1")
    assert meta.uses_resource_groups is True
    assert meta.resource_groups == ("ais", "pis", "cbpii", "vrp")
    assert meta.display_label == "Read/Write API"


@pytest.mark.unit
def test_plugin_target_metadata_dcr_no_resource_groups() -> None:
    meta = PluginTargetMetadata(
        plugin_id="dynamic-client-registration",
        standard="obl",
        specification="dynamic-client-registration",
        supported_versions=("3.2", "3.3", "3.4"),
        uses_resource_groups=False,
    )
    assert meta.uses_resource_groups is False
    assert meta.resource_groups == ()
    assert meta.display_label == ""


@pytest.mark.unit
def test_plugin_target_metadata_is_frozen() -> None:
    meta = PluginTargetMetadata(
        plugin_id="read-write",
        standard="obl",
        specification="read-write",
        supported_versions=("v4.0.1",),
        uses_resource_groups=True,
    )
    with pytest.raises(Exception):
        meta.plugin_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Structural subtyping: _StubPlugin satisfies ConformancePlugin
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stub_plugin_satisfies_protocol() -> None:
    plugin: ConformancePlugin = _StubPlugin()  # type: ignore[assignment]
    assert plugin.plugin_id == "stub"


@pytest.mark.unit
def test_stub_plugin_target_metadata() -> None:
    plugin = _StubPlugin()
    meta = plugin.target_metadata()
    assert meta.plugin_id == "stub"
    assert meta.supported_versions == ("v4.0.1",)


@pytest.mark.unit
def test_stub_plugin_supports_matching_target() -> None:
    plugin = _StubPlugin()
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    assert plugin.supports_target(target) is True


@pytest.mark.unit
def test_stub_plugin_rejects_wrong_specification() -> None:
    plugin = _StubPlugin()
    target = TestTargetConfig(
        standard="obl",
        specification="dynamic-client-registration",
        security_profile="fapi1-advanced",
        specification_version="3.3",
    )
    assert plugin.supports_target(target) is False


@pytest.mark.unit
def test_stub_plugin_rejects_wrong_version() -> None:
    plugin = _StubPlugin()
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v3.1.11",
    )
    assert plugin.supports_target(target) is False


@pytest.mark.unit
def test_stub_plugin_catalogue_identity() -> None:
    plugin = _StubPlugin()
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    identity = plugin.catalogue_identity(target)
    assert identity.plugin_id == "stub"
    assert identity.specification_version == "v4.0.1"
    assert identity.content_hash == "sha256:abc123"


@pytest.mark.unit
def test_stub_plugin_load_catalogue_returns_catalogue() -> None:
    plugin = _StubPlugin()
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    catalogue = plugin.load_catalogue(target)
    assert catalogue.endpoints == ()


@pytest.mark.unit
def test_stub_plugin_masking_fields_empty() -> None:
    plugin = _StubPlugin()
    assert plugin.masking_fields() == frozenset()
