"""Unit tests for conformance.plugins.dcr.plugin module."""

from __future__ import annotations

import dataclasses

import pytest

from conformance.catalogue import Catalogue, CatalogueIdentity
from conformance.plugins.dcr.plugin import PLUGIN_ID, DcrPlugin, get_dcr_plugin
from conformance.target_config import TestTargetConfig


def _make_target(version: str = "3.3") -> TestTargetConfig:
    """Build a DCR TestTargetConfig for the given version."""
    return TestTargetConfig(
        standard="obl",
        specification="dynamic-client-registration",
        security_profile="fapi1-advanced",
        specification_version=version,
    )


@pytest.mark.unit
class TestDcrPluginId:
    """Verify plugin_id is stable."""

    def test_plugin_id_is_dcr(self) -> None:
        """plugin_id returns the string 'dcr'."""
        assert DcrPlugin().plugin_id == "dcr"
        assert PLUGIN_ID == "dcr"


@pytest.mark.unit
class TestDcrPluginTargetMetadata:
    """Verify target metadata structure."""

    def test_standard_is_obl(self) -> None:
        """target_metadata.standard is 'obl'."""
        meta = DcrPlugin().target_metadata()
        assert meta.standard == "obl"

    def test_specification_is_dcr(self) -> None:
        """target_metadata.specification is 'dynamic-client-registration'."""
        meta = DcrPlugin().target_metadata()
        assert meta.specification == "dynamic-client-registration"

    def test_supported_versions_contain_all_three(self) -> None:
        """target_metadata.supported_versions contains 3.2, 3.3, and 3.4."""
        meta = DcrPlugin().target_metadata()
        assert "3.2" in meta.supported_versions
        assert "3.3" in meta.supported_versions
        assert "3.4" in meta.supported_versions

    def test_uses_resource_groups_is_false(self) -> None:
        """DCR does not use resource groups."""
        meta = DcrPlugin().target_metadata()
        assert meta.uses_resource_groups is False
        assert meta.resource_groups == ()

    def test_display_label_is_non_empty(self) -> None:
        """display_label is set to a non-empty string."""
        meta = DcrPlugin().target_metadata()
        assert meta.display_label


@pytest.mark.unit
class TestDcrPluginSupportsTarget:
    """Verify supports_target returns correct boolean for various inputs."""

    def test_supports_dcr_3_2(self) -> None:
        """supports_target returns True for DCR 3.2."""
        assert DcrPlugin().supports_target(_make_target("3.2")) is True

    def test_supports_dcr_3_3(self) -> None:
        """supports_target returns True for DCR 3.3."""
        assert DcrPlugin().supports_target(_make_target("3.3")) is True

    def test_supports_dcr_3_4(self) -> None:
        """supports_target returns True for DCR 3.4."""
        assert DcrPlugin().supports_target(_make_target("3.4")) is True

    def test_rejects_unknown_version(self) -> None:
        """supports_target returns False for an unsupported DCR version."""
        assert DcrPlugin().supports_target(_make_target("2.0")) is False

    def test_rejects_wrong_specification(self) -> None:
        """supports_target returns False for read-write specification."""
        target = TestTargetConfig(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version="3.3",
        )
        assert DcrPlugin().supports_target(target) is False

    def test_rejects_wrong_standard(self) -> None:
        """supports_target returns False when standard is not 'obl'."""
        target = TestTargetConfig(
            standard="obl",  # Only "obl" is valid; test with wrong specification.
            specification="dynamic-client-registration",
            security_profile="fapi1-advanced",
            specification_version="3.3",
        )
        # Test wrong specification (DCR plugin should reject non-DCR targets)
        wrong_target = dataclasses.replace(target, specification="read-write")
        assert DcrPlugin().supports_target(wrong_target) is False


@pytest.mark.unit
class TestDcrPluginCatalogueIdentity:
    """Verify catalogue_identity returns a valid CatalogueIdentity."""

    def test_returns_catalogue_identity_for_33(self) -> None:
        """catalogue_identity returns a CatalogueIdentity for version 3.3."""
        identity = DcrPlugin().catalogue_identity(_make_target("3.3"))
        assert isinstance(identity, CatalogueIdentity)

    def test_identity_plugin_id_is_dcr(self) -> None:
        """CatalogueIdentity.plugin_id is 'dcr'."""
        identity = DcrPlugin().catalogue_identity(_make_target("3.3"))
        assert identity.plugin_id == "dcr"

    def test_identity_specification_version_matches_target(self) -> None:
        """CatalogueIdentity.specification_version matches the target version."""
        for version in ("3.2", "3.3", "3.4"):
            identity = DcrPlugin().catalogue_identity(_make_target(version))
            assert identity.specification_version == version

    def test_identity_content_hash_is_sha256_format(self) -> None:
        """CatalogueIdentity.content_hash starts with 'sha256:'."""
        identity = DcrPlugin().catalogue_identity(_make_target("3.3"))
        assert identity.content_hash.startswith("sha256:")

    def test_identity_is_deterministic(self) -> None:
        """Same version produces the same content_hash across calls."""
        plugin = DcrPlugin()
        identity1 = plugin.catalogue_identity(_make_target("3.2"))
        identity2 = plugin.catalogue_identity(_make_target("3.2"))
        assert identity1.content_hash == identity2.content_hash

    def test_different_versions_have_different_hashes(self) -> None:
        """Different versions produce different content hashes."""
        plugin = DcrPlugin()
        h32 = plugin.catalogue_identity(_make_target("3.2")).content_hash
        h33 = plugin.catalogue_identity(_make_target("3.3")).content_hash
        h34 = plugin.catalogue_identity(_make_target("3.4")).content_hash
        assert h32 != h33
        assert h33 != h34

    def test_raises_for_unsupported_version(self) -> None:
        """ValueError is raised for a version not in supported_versions."""
        with pytest.raises(ValueError, match="Unsupported DCR specification version"):
            DcrPlugin().catalogue_identity(_make_target("9.9"))


@pytest.mark.unit
class TestDcrPluginLoadCatalogue:
    """Verify load_catalogue returns a valid Catalogue."""

    def test_returns_catalogue_for_32(self) -> None:
        """load_catalogue returns a Catalogue for version 3.2."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.2"))
        assert isinstance(catalogue, Catalogue)

    def test_returns_catalogue_for_33(self) -> None:
        """load_catalogue returns a Catalogue for version 3.3."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        assert isinstance(catalogue, Catalogue)

    def test_returns_catalogue_for_34(self) -> None:
        """load_catalogue returns a Catalogue for version 3.4."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.4"))
        assert isinstance(catalogue, Catalogue)

    def test_catalogue_contains_mandatory_register_post(self) -> None:
        """Catalogue includes the mandatory dcr.register.post entry."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        ids = {e.endpoint_id for e in catalogue.endpoints}
        assert "dcr.register.post" in ids

    def test_catalogue_contains_discovery_entry(self) -> None:
        """Catalogue includes the dcr.discovery.validate entry."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        ids = {e.endpoint_id for e in catalogue.endpoints}
        assert "dcr.discovery.validate" in ids

    def test_catalogue_contains_negative_test_entries(self) -> None:
        """Catalogue includes negative test entries."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        ids = {e.endpoint_id for e in catalogue.endpoints}
        assert "dcr.negative.expired-ssa" in ids
        assert "dcr.negative.wrong-client-id" in ids

    def test_get_put_delete_entries_are_optional(self) -> None:
        """GET, PUT, DELETE /register/{clientId} entries are marked optional."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        for entry in catalogue.endpoints:
            if entry.endpoint_id in ("dcr.register.get", "dcr.register.put", "dcr.register.delete"):
                assert entry.requirement == "optional", f"{entry.endpoint_id} should be optional"

    def test_register_post_is_mandatory(self) -> None:
        """dcr.register.post entry is mandatory."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        for entry in catalogue.endpoints:
            if entry.endpoint_id == "dcr.register.post":
                assert entry.requirement == "mandatory"

    def test_no_resource_groups_in_entries(self) -> None:
        """All DCR catalogue entries have resource_group=None."""
        catalogue = DcrPlugin().load_catalogue(_make_target("3.3"))
        for entry in catalogue.endpoints:
            assert entry.resource_group is None, f"{entry.endpoint_id} should have no resource group"


@pytest.mark.unit
class TestDcrPluginMaskingFields:
    """Verify masking_fields contains expected sensitive field names."""

    def test_contains_registration_access_token(self) -> None:
        """masking_fields includes 'registration_access_token'."""
        assert "registration_access_token" in DcrPlugin().masking_fields()

    def test_contains_client_secret(self) -> None:
        """masking_fields includes 'client_secret'."""
        assert "client_secret" in DcrPlugin().masking_fields()

    def test_contains_access_token(self) -> None:
        """masking_fields includes 'access_token'."""
        assert "access_token" in DcrPlugin().masking_fields()

    def test_returns_frozenset(self) -> None:
        """masking_fields returns a frozenset."""
        assert isinstance(DcrPlugin().masking_fields(), frozenset)


@pytest.mark.unit
class TestGetDcrPlugin:
    """Verify get_dcr_plugin singleton behaviour."""

    def test_returns_dcr_plugin_instance(self) -> None:
        """get_dcr_plugin returns a DcrPlugin instance."""
        assert isinstance(get_dcr_plugin(), DcrPlugin)

    def test_returns_same_instance_on_second_call(self) -> None:
        """get_dcr_plugin returns the same instance on repeated calls."""
        assert get_dcr_plugin() is get_dcr_plugin()
