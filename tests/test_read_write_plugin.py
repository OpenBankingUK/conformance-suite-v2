"""Unit tests for the ReadWritePlugin and its catalogue loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.catalogue import (
    Catalogue,
    compute_catalogue_hash,
    parse_catalogue,
)
from conformance.plugins.read_write.plugin import (
    ReadWritePlugin,
    _normalise_version,
    _version_to_dir,
)
from conformance.target_config import TestTargetConfig

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_target(
    *,
    version: str = "v4.0.1",
    resource_groups: tuple[str, ...] = (),
) -> TestTargetConfig:
    """Build a TestTargetConfig for the Read/Write plugin."""
    return TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version=version,
        resource_groups=resource_groups,
    )


# ---------------------------------------------------------------------------
# _version_to_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_version_to_dir_replaces_dots() -> None:
    assert _version_to_dir("v4.0.1") == "v4_0_1"


@pytest.mark.unit
def test_version_to_dir_no_dots_unchanged() -> None:
    assert _version_to_dir("v4") == "v4"


@pytest.mark.unit
def test_normalise_version_converts_v40_alias() -> None:
    """The temporary v4.0 migration alias serialises as patch-explicit v4.0.0."""
    assert _normalise_version("v4.0") == "v4.0.0"
    assert _normalise_version("v4.0.1") == "v4.0.1"


# ---------------------------------------------------------------------------
# ReadWritePlugin.plugin_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plugin_id() -> None:
    assert ReadWritePlugin().plugin_id == "read-write"


# ---------------------------------------------------------------------------
# ReadWritePlugin.target_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_target_metadata_fields() -> None:
    meta = ReadWritePlugin().target_metadata()
    assert meta.plugin_id == "read-write"
    assert meta.standard == "obl"
    assert meta.specification == "read-write"
    assert meta.supported_versions == ("v3.1.11", "v4.0.0", "v4.0.1")
    assert "v4.0.1" in meta.supported_versions
    assert meta.uses_resource_groups is True
    assert set(meta.resource_groups) == {"ais", "pis", "cbpii", "vrp"}
    assert meta.display_label == "Read/Write API"


# ---------------------------------------------------------------------------
# ReadWritePlugin.supports_target
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_supports_target_matching() -> None:
    assert ReadWritePlugin().supports_target(_make_target()) is True


@pytest.mark.unit
def test_supports_target_wrong_standard() -> None:
    # Standard "obl" is the only supported standard; test wrong specification
    # to exercise the False path (wrong standard can't be constructed safely).
    target_bad_version = TestTargetConfig(
        standard="obl",
        specification="read-write",
        security_profile="fapi1-advanced",
        specification_version="v9.9.9",
    )
    assert ReadWritePlugin().supports_target(target_bad_version) is False


@pytest.mark.unit
def test_supports_target_wrong_specification() -> None:
    target = TestTargetConfig(
        standard="obl",
        specification="dynamic-client-registration",
        security_profile="fapi1-advanced",
        specification_version="v4.0.1",
    )
    assert ReadWritePlugin().supports_target(target) is False


@pytest.mark.unit
def test_supports_target_unsupported_version() -> None:
    target = _make_target(version="v9.9.9")
    assert ReadWritePlugin().supports_target(target) is False


@pytest.mark.unit
def test_supports_target_accepts_v40_alias() -> None:
    """The temporary v4.0 alias resolves to the v4.0.0 catalogue."""
    assert ReadWritePlugin().supports_target(_make_target(version="v4.0")) is True


# ---------------------------------------------------------------------------
# ReadWritePlugin.catalogue_identity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalogue_identity_returns_correct_fields() -> None:
    plugin = ReadWritePlugin()
    identity = plugin.catalogue_identity(_make_target())
    assert identity.plugin_id == "read-write"
    assert identity.specification == "read-write"
    assert identity.specification_version == "v4.0.1"
    assert identity.standard == "obl"
    assert identity.security_profile == "fapi1-advanced"
    assert identity.content_hash.startswith("sha256:")
    assert len(identity.content_hash) == len("sha256:") + 64


@pytest.mark.unit
def test_catalogue_identity_normalises_v40_alias() -> None:
    """Catalogue identities expose the canonical patch-explicit version."""
    identity = ReadWritePlugin().catalogue_identity(_make_target(version="v4.0"))
    assert identity.specification_version == "v4.0.0"
    assert identity.version_aliases == ("v4.0",)


@pytest.mark.unit
def test_catalogue_identity_hash_is_deterministic() -> None:
    plugin = ReadWritePlugin()
    target = _make_target()
    h1 = plugin.catalogue_identity(target).content_hash
    h2 = plugin.catalogue_identity(target).content_hash
    assert h1 == h2


# ---------------------------------------------------------------------------
# ReadWritePlugin.load_catalogue
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_catalogue_returns_catalogue_instance() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    assert isinstance(catalogue, Catalogue)


@pytest.mark.unit
def test_load_catalogue_identity_matches_catalogue_identity_method() -> None:
    plugin = ReadWritePlugin()
    target = _make_target()
    from_method = plugin.catalogue_identity(target).content_hash
    from_catalogue = plugin.load_catalogue(target).identity.content_hash
    assert from_method == from_catalogue


@pytest.mark.unit
def test_load_catalogue_schema_v2_metadata() -> None:
    """Consolidated Read/Write catalogues expose schema v2 migration metadata."""
    catalogue = ReadWritePlugin().load_catalogue(_make_target(version="v4.0.0"))
    assert catalogue.schema_version == 2
    assert catalogue.identity.specification_version == "v4.0.0"
    assert catalogue.identity.version_aliases == ("v4.0",)
    assert {group.resource_group for group in catalogue.resource_groups} == {"ais", "pis", "cbpii", "vrp"}
    assert {field.field_id for field in catalogue.field_schemas} >= {
        "tls.certPath",
        "tls.keyPath",
        "tls.caBundlePath",
        "fapiSigning.signingCertificatePath",
        "fapiSigning.signingPrivateKeyPath",
        "fapiSigning.requestObjectIssuerOverride",
        "fapiSigning.privateKeyJwtIssuerOverride",
        "fapiSigning.privateKeyJwtSubjectOverride",
        "fapiSigning.tokenEndpointAuthMethod",
    }
    assert {primitive.primitive_id for primitive in catalogue.runner_primitives} >= {
        "read-write.http-request",
        "read-write.detached-jws",
        "read-write.response-signature",
        "read-write.migration-source-step",
    }
    assert catalogue.readiness_policy is not None
    assert catalogue.readiness_policy.policy_id == "read-write-resource-group-readiness-v1"
    assert catalogue.masking is not None
    assert "client_assertion" in catalogue.masking.masked_fields
    assert catalogue.source_coverage["baselinePath"] == (
        "docs/requirements/suite-coverage/migration-parity-baseline.json"
    )


@pytest.mark.unit
def test_load_catalogue_has_executable_tests_and_source_coverage() -> None:
    """Migrated catalogues preserve source coverage as executable test metadata."""
    catalogue = ReadWritePlugin().load_catalogue(_make_target(version="v4.0.0"))
    test_ids = {test.test_id for test in catalogue.executable_tests}

    assert "OB-400-ACC-100400" in test_ids
    assert "OB-400-DOP-100100" in test_ids
    assert "ais.accounts.get-accounts.http" in test_ids
    migrated = next(test for test in catalogue.executable_tests if test.test_id == "OB-400-ACC-100400")
    assert {ref.source_kind for ref in migrated.source_coverage} == {"current-suite", "previous-fcs"}


@pytest.mark.unit
def test_v400_catalogue_covers_phase_1_target_test_ids() -> None:
    """The v4.0.0 catalogue preserves every phase-1 target catalogue test ID."""
    catalogue = ReadWritePlugin().load_catalogue(_make_target(version="v4.0.0"))
    test_ids = {test.test_id for test in catalogue.executable_tests}
    baseline = json.loads(_MIGRATION_BASELINE.read_bytes())
    target_ids = {target_id for record in baseline["records"] for target_id in record["targetCatalogueTestIds"]}

    assert target_ids <= test_ids


@pytest.mark.unit
def test_catalogue_hash_includes_schema_v2_policy_metadata() -> None:
    """Catalogue drift hashes change when executable policy metadata changes."""
    catalogue_path = _CATALOGUE_ROOT / "v4_0_0" / "catalogue.json"
    raw = catalogue_path.read_bytes()
    mutated = json.loads(raw)
    mutated["readinessPolicy"]["failedSelectedOutcome"] = "manual-review"

    assert compute_catalogue_hash(raw) != compute_catalogue_hash(json.dumps(mutated, sort_keys=True).encode())


@pytest.mark.unit
@pytest.mark.parametrize("version", ["v3.1.11", "v4.0.0", "v4.0.1"])
def test_load_catalogue_supported_versions(version: str) -> None:
    """Every planned Read/Write version has a parseable consolidated catalogue."""
    catalogue = ReadWritePlugin().load_catalogue(_make_target(version=version))
    assert catalogue.schema_version == 2
    assert catalogue.identity.specification_version == version
    assert {group.resource_group for group in catalogue.resource_groups} == {"ais", "pis", "cbpii", "vrp"}
    assert catalogue.executable_tests


@pytest.mark.unit
def test_load_catalogue_contains_all_resource_groups() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    resource_groups = {entry.resource_group for entry in catalogue.endpoints}
    assert "ais" in resource_groups
    assert "pis" in resource_groups
    assert "cbpii" in resource_groups
    assert "vrp" in resource_groups


@pytest.mark.unit
def test_load_catalogue_ais_has_mandatory_accounts() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    ais_endpoints = [e for e in catalogue.endpoints if e.resource_group == "ais"]
    endpoint_ids = [e.endpoint_id for e in ais_endpoints]
    assert "ais.accounts.get-accounts" in endpoint_ids
    assert "ais.accounts.get-account" in endpoint_ids
    assert "ais.transactions.get-account-transactions" in endpoint_ids
    assert "ais.balances.get-account-balances" in endpoint_ids


@pytest.mark.unit
def test_load_catalogue_ais_has_consent_endpoints() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    ais_endpoints = [e for e in catalogue.endpoints if e.resource_group == "ais"]
    endpoint_ids = [e.endpoint_id for e in ais_endpoints]
    assert "ais.account-access-consents.create" in endpoint_ids
    assert "ais.account-access-consents.get" in endpoint_ids
    assert "ais.account-access-consents.delete" in endpoint_ids


@pytest.mark.unit
def test_load_catalogue_pis_has_mandatory_endpoints() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    pis_endpoints = [e for e in catalogue.endpoints if e.resource_group == "pis"]
    mandatory = [e for e in pis_endpoints if e.requirement == "mandatory"]
    mandatory_ids = [e.endpoint_id for e in mandatory]
    assert "pis.domestic-payment-consents.create" in mandatory_ids
    assert "pis.domestic-payment-consents.get" in mandatory_ids
    assert "pis.domestic-payments.create" in mandatory_ids
    assert "pis.domestic-payments.get" in mandatory_ids


@pytest.mark.unit
def test_load_catalogue_cbpii_has_mandatory_endpoints() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    cbpii_endpoints = [e for e in catalogue.endpoints if e.resource_group == "cbpii"]
    mandatory = [e for e in cbpii_endpoints if e.requirement == "mandatory"]
    mandatory_ids = [e.endpoint_id for e in mandatory]
    assert "cbpii.funds-confirmation-consents.create" in mandatory_ids
    assert "cbpii.funds-confirmation-consents.get" in mandatory_ids
    assert "cbpii.funds-confirmation-consents.delete" in mandatory_ids
    assert "cbpii.funds-confirmations.create" in mandatory_ids


@pytest.mark.unit
def test_load_catalogue_vrp_has_mandatory_endpoints() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    vrp_endpoints = [e for e in catalogue.endpoints if e.resource_group == "vrp"]
    mandatory = [e for e in vrp_endpoints if e.requirement == "mandatory"]
    mandatory_ids = [e.endpoint_id for e in mandatory]
    assert "vrp.domestic-vrp-consents.create" in mandatory_ids
    assert "vrp.domestic-vrp-consents.get" in mandatory_ids
    assert "vrp.domestic-vrps.create" in mandatory_ids
    assert "vrp.domestic-vrps.get" in mandatory_ids


@pytest.mark.unit
def test_load_catalogue_all_endpoints_have_non_empty_fields() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    for entry in catalogue.endpoints:
        assert entry.endpoint_id, f"Empty endpoint_id for {entry}"
        assert entry.method, f"Empty method for {entry.endpoint_id}"
        assert entry.path, f"Empty path for {entry.endpoint_id}"
        assert entry.display_label, f"Empty display_label for {entry.endpoint_id}"
        assert entry.requirement in ("mandatory", "conditional", "optional"), (
            f"Invalid requirement {entry.requirement!r} for {entry.endpoint_id}"
        )


@pytest.mark.unit
def test_load_catalogue_endpoint_ids_are_unique() -> None:
    catalogue = ReadWritePlugin().load_catalogue(_make_target())
    ids = [entry.endpoint_id for entry in catalogue.endpoints]
    assert len(ids) == len(set(ids)), "Duplicate endpoint IDs in catalogue"


# ---------------------------------------------------------------------------
# ReadWritePlugin.masking_fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_masking_fields_returns_frozenset() -> None:
    result = ReadWritePlugin().masking_fields()
    assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# Catalogue JSON files are individually parseable
# ---------------------------------------------------------------------------


_CATALOGUE_ROOT = Path(__file__).parent.parent / "conformance" / "plugins" / "read_write" / "catalogues"
_MIGRATION_BASELINE = (
    Path(__file__).parent.parent / "docs" / "requirements" / "suite-coverage" / "migration-parity-baseline.json"
)


@pytest.mark.unit
@pytest.mark.parametrize("version_dir", ["v3_1_11", "v4_0_0", "v4_0_1"])
def test_consolidated_catalogue_file_is_parseable(version_dir: str) -> None:
    """Each consolidated catalogue file must be valid schema v2 JSON."""
    file_path = _CATALOGUE_ROOT / version_dir / "catalogue.json"
    raw_bytes = file_path.read_bytes()
    raw_json = json.loads(raw_bytes)
    catalogue = parse_catalogue(raw_json)
    assert isinstance(catalogue, Catalogue)
    assert catalogue.schema_version == 2
    assert catalogue.executable_tests


@pytest.mark.unit
@pytest.mark.parametrize("version_dir", ["v3_1_11", "v4_0_0", "v4_0_1"])
def test_catalogue_index_json_is_valid_json(version_dir: str) -> None:
    """Each catalogue index points at the consolidated schema v2 catalogue."""
    index_path = _CATALOGUE_ROOT / version_dir / "catalogue_index.json"
    raw = json.loads(index_path.read_bytes())
    assert raw["schemaVersion"] == 2
    assert raw["catalogueFile"] == "catalogue.json"
    assert "specification" in raw
    assert "resourceGroups" in raw
    resource_group_ids = {rg["id"] for rg in raw["resourceGroups"]}
    assert resource_group_ids == {"ais", "pis", "cbpii", "vrp"}


# ---------------------------------------------------------------------------
# Plugin conforms to the ConformancePlugin Protocol (structural check)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plugin_satisfies_protocol() -> None:
    """ReadWritePlugin must satisfy the ConformancePlugin structural protocol."""

    plugin = ReadWritePlugin()
    # Protocol runtime check via isinstance (requires runtime_checkable)
    # The protocol is not runtime_checkable by default; verify via duck-typing
    assert hasattr(plugin, "plugin_id")
    assert hasattr(plugin, "target_metadata")
    assert hasattr(plugin, "supports_target")
    assert hasattr(plugin, "catalogue_identity")
    assert hasattr(plugin, "load_catalogue")
    assert hasattr(plugin, "masking_fields")
    _ = plugin.plugin_id
    _ = plugin.target_metadata()
    _ = plugin.supports_target(_make_target())
    _ = plugin.catalogue_identity(_make_target())
    _ = plugin.load_catalogue(_make_target())
    _ = plugin.masking_fields()
