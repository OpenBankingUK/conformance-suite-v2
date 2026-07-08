"""Unit tests for the PluginRegistry."""

from __future__ import annotations

import pytest

from conformance.catalogue import Catalogue, CatalogueIdentity
from conformance.plugins.domain import PluginTargetMetadata
from conformance.plugins.registry import PluginRegistry, PluginRegistryError
from conformance.target_config import TestTargetConfig


# ---------------------------------------------------------------------------
# Stub plugins
# ---------------------------------------------------------------------------


class _ReadWritePlugin:
    """Stub read-write plugin."""

    @property
    def plugin_id(self) -> str:
        """Return the read-write plugin id."""
        return "read-write"

    def target_metadata(self) -> PluginTargetMetadata:
        """Return read-write target metadata."""
        return PluginTargetMetadata(
            plugin_id="read-write",
            standard="obl",
            specification="read-write",
            supported_versions=("v4.0.1",),
            uses_resource_groups=True,
        )

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return True for read-write targets."""
        return target.specification == "read-write"

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return a stub catalogue identity for read-write."""
        return CatalogueIdentity(
            plugin_id="read-write",
            specification="read-write",
            specification_version=target.specification_version,
            content_hash="sha256:rw",
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Return an empty catalogue."""
        return Catalogue(identity=self.catalogue_identity(target), endpoints=())

    def masking_fields(self) -> frozenset[str]:
        """Return empty masking fields."""
        return frozenset()


class _DcrPlugin:
    """Stub DCR plugin."""

    @property
    def plugin_id(self) -> str:
        """Return the DCR plugin id."""
        return "dynamic-client-registration"

    def target_metadata(self) -> PluginTargetMetadata:
        """Return DCR target metadata."""
        return PluginTargetMetadata(
            plugin_id="dynamic-client-registration",
            standard="obl",
            specification="dynamic-client-registration",
            supported_versions=("3.2", "3.3", "3.4"),
            uses_resource_groups=False,
        )

    def supports_target(self, target: TestTargetConfig) -> bool:
        """Return True for DCR targets."""
        return target.specification == "dynamic-client-registration"

    def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
        """Return a stub catalogue identity for DCR."""
        return CatalogueIdentity(
            plugin_id="dynamic-client-registration",
            specification="dynamic-client-registration",
            specification_version=target.specification_version,
            content_hash="sha256:dcr",
        )

    def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
        """Return an empty catalogue."""
        return Catalogue(identity=self.catalogue_identity(target), endpoints=())

    def masking_fields(self) -> frozenset[str]:
        """Return empty masking fields."""
        return frozenset()


# ---------------------------------------------------------------------------
# PluginRegistry tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_starts_empty() -> None:
    registry = PluginRegistry()
    assert registry.plugin_ids == ()


@pytest.mark.unit
def test_register_single_plugin() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    assert registry.plugin_ids == ("read-write",)


@pytest.mark.unit
def test_register_two_plugins_preserves_order() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    registry.register(_DcrPlugin())
    assert registry.plugin_ids == ("read-write", "dynamic-client-registration")


@pytest.mark.unit
def test_register_duplicate_raises() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    with pytest.raises(PluginRegistryError, match="already registered"):
        registry.register(_ReadWritePlugin())


@pytest.mark.unit
def test_resolve_finds_read_write_plugin() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    registry.register(_DcrPlugin())

    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    plugin = registry.resolve(target)
    assert plugin.plugin_id == "read-write"


@pytest.mark.unit
def test_resolve_finds_dcr_plugin() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    registry.register(_DcrPlugin())

    target = TestTargetConfig(
        standard="obl",
        specification="dynamic-client-registration",
        security_profile="fapi1-advanced",
        specification_version="3.3",
    )
    plugin = registry.resolve(target)
    assert plugin.plugin_id == "dynamic-client-registration"


@pytest.mark.unit
def test_resolve_raises_when_no_plugin_matches() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())

    target = TestTargetConfig(
        standard="obl",
        specification="dynamic-client-registration",
        security_profile="fapi1-advanced",
        specification_version="3.3",
    )
    with pytest.raises(PluginRegistryError, match="No plugin found"):
        registry.resolve(target)


@pytest.mark.unit
def test_resolve_error_message_includes_coordinates() -> None:
    registry = PluginRegistry()
    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    with pytest.raises(PluginRegistryError) as exc_info:
        registry.resolve(target)
    msg = str(exc_info.value)
    assert "read-write" in msg
    assert "v4.0.1" in msg


@pytest.mark.unit
def test_get_returns_plugin_by_id() -> None:
    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    registry.register(_DcrPlugin())
    plugin = registry.get("dynamic-client-registration")
    assert plugin.plugin_id == "dynamic-client-registration"


@pytest.mark.unit
def test_get_raises_for_unknown_id() -> None:
    registry = PluginRegistry()
    with pytest.raises(PluginRegistryError, match="No plugin registered"):
        registry.get("unknown")


@pytest.mark.unit
def test_resolve_uses_first_matching_plugin_in_order() -> None:
    """When two plugins claim the same target, the first registered wins."""

    class _AlsoReadWrite:
        """Second plugin that also claims read-write."""

        @property
        def plugin_id(self) -> str:
            """Return an alternative plugin id."""
            return "read-write-alt"

        def target_metadata(self) -> PluginTargetMetadata:
            """Return alternative target metadata."""
            return PluginTargetMetadata(
                plugin_id="read-write-alt",
                standard="obl",
                specification="read-write",
                supported_versions=("v4.0.1",),
                uses_resource_groups=True,
            )

        def supports_target(self, target: TestTargetConfig) -> bool:
            """Return True for any read-write target."""
            return target.specification == "read-write"

        def catalogue_identity(self, target: TestTargetConfig) -> CatalogueIdentity:
            """Return a stub catalogue identity."""
            return CatalogueIdentity(
                plugin_id="read-write-alt",
                specification="read-write",
                specification_version=target.specification_version,
                content_hash="sha256:alt",
            )

        def load_catalogue(self, target: TestTargetConfig) -> Catalogue:
            """Return an empty catalogue."""
            return Catalogue(identity=self.catalogue_identity(target), endpoints=())

        def masking_fields(self) -> frozenset[str]:
            """Return empty masking fields."""
            return frozenset()

    registry = PluginRegistry()
    registry.register(_ReadWritePlugin())
    registry.register(_AlsoReadWrite())

    target = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    plugin = registry.resolve(target)
    assert plugin.plugin_id == "read-write"
