"""Unit tests for the Catalogue domain types and parse/hash helpers."""

from __future__ import annotations

import dataclasses
import json
from typing import cast

import pytest

from conformance.catalogue import (
    CatalogueIdentity,
    CatalogueParseError,
    EndpointCatalogueEntry,
    compute_catalogue_hash,
    parse_catalogue,
)
from conformance.json_types import JsonObject

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _minimal_catalogue_doc() -> JsonObject:
    """Return a minimal valid catalogue JSON document."""
    return {
        "identity": {
            "pluginId": "read-write",
            "specification": "read-write",
            "specificationVersion": "v4.0.1",
            "contentHash": "sha256:abc",
        },
        "endpoints": [],
    }


def _endpoint_entry_doc(
    *,
    endpoint_id: str = "get-accounts",
    operation: str = "GET",
    path: str = "/accounts",
    method: str = "GET",
    resource_group: str | None = "ais",
    requirement: str = "mandatory",
    display_label: str = "Get Accounts",
) -> JsonObject:
    """Return a minimal valid endpoint entry dict."""
    entry: JsonObject = {
        "endpointId": endpoint_id,
        "operation": operation,
        "path": path,
        "method": method,
        "requirement": requirement,
        "displayLabel": display_label,
    }
    if resource_group is not None:
        entry["resourceGroup"] = resource_group
    return entry


# ---------------------------------------------------------------------------
# compute_catalogue_hash
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_catalogue_hash_format() -> None:
    result = compute_catalogue_hash(b"{}")
    assert result.startswith("sha256:")
    assert len(result) == len("sha256:") + 64


@pytest.mark.unit
def test_compute_catalogue_hash_deterministic() -> None:
    data = b'{"endpoints": []}'
    assert compute_catalogue_hash(data) == compute_catalogue_hash(data)


@pytest.mark.unit
def test_compute_catalogue_hash_differs_for_different_content() -> None:
    assert compute_catalogue_hash(b"a") != compute_catalogue_hash(b"b")


# ---------------------------------------------------------------------------
# CatalogueIdentity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalogue_identity_is_frozen() -> None:
    identity = CatalogueIdentity(
        plugin_id="read-write",
        specification="read-write",
        specification_version="v4.0.1",
        content_hash="sha256:abc",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.plugin_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_catalogue — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_minimal_catalogue() -> None:
    doc = _minimal_catalogue_doc()
    catalogue = parse_catalogue(doc)
    assert catalogue.identity.plugin_id == "read-write"
    assert catalogue.identity.specification == "read-write"
    assert catalogue.identity.specification_version == "v4.0.1"
    assert catalogue.endpoints == ()


@pytest.mark.unit
def test_parse_catalogue_with_one_mandatory_endpoint() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = [_endpoint_entry_doc()]
    catalogue = parse_catalogue(doc)
    assert len(catalogue.endpoints) == 1
    entry = catalogue.endpoints[0]
    assert entry.endpoint_id == "get-accounts"
    assert entry.operation == "GET"
    assert entry.path == "/accounts"
    assert entry.method == "GET"
    assert entry.resource_group == "ais"
    assert entry.requirement == "mandatory"
    assert entry.display_label == "Get Accounts"


@pytest.mark.unit
def test_parse_catalogue_with_null_resource_group() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = [_endpoint_entry_doc(resource_group=None)]
    catalogue = parse_catalogue(doc)
    assert catalogue.endpoints[0].resource_group is None


@pytest.mark.unit
def test_parse_catalogue_with_missing_resource_group_key() -> None:
    """Absent resourceGroup key (not null) should also resolve to None."""
    doc = _minimal_catalogue_doc()
    # _endpoint_entry_doc(resource_group=None) omits the key entirely
    entry = _endpoint_entry_doc(resource_group=None)
    assert "resourceGroup" not in entry
    doc["endpoints"] = [entry]
    catalogue = parse_catalogue(doc)
    assert catalogue.endpoints[0].resource_group is None


@pytest.mark.unit
def test_parse_catalogue_conditional_and_optional_requirements() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = [
        _endpoint_entry_doc(endpoint_id="e1", requirement="conditional"),
        _endpoint_entry_doc(endpoint_id="e2", requirement="optional"),
    ]
    catalogue = parse_catalogue(doc)
    assert catalogue.endpoints[0].requirement == "conditional"
    assert catalogue.endpoints[1].requirement == "optional"


@pytest.mark.unit
def test_parse_catalogue_preserves_endpoint_order() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = [
        _endpoint_entry_doc(endpoint_id="z"),
        _endpoint_entry_doc(endpoint_id="a"),
    ]
    catalogue = parse_catalogue(doc)
    assert catalogue.endpoints[0].endpoint_id == "z"
    assert catalogue.endpoints[1].endpoint_id == "a"


# ---------------------------------------------------------------------------
# parse_catalogue — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_non_object_raises() -> None:
    with pytest.raises(CatalogueParseError, match="must be a JSON object"):
        parse_catalogue([])


@pytest.mark.unit
def test_parse_missing_identity_raises() -> None:
    with pytest.raises(CatalogueParseError, match="identity"):
        parse_catalogue({"endpoints": []})


@pytest.mark.unit
def test_parse_identity_missing_plugin_id_raises() -> None:
    doc = _minimal_catalogue_doc()
    cast(JsonObject, doc["identity"]).pop("pluginId")
    with pytest.raises(CatalogueParseError, match="pluginId"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_identity_empty_specification_raises() -> None:
    doc = _minimal_catalogue_doc()
    cast(JsonObject, doc["identity"])["specification"] = ""
    with pytest.raises(CatalogueParseError):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_missing_endpoints_raises() -> None:
    with pytest.raises(CatalogueParseError, match="endpoints"):
        parse_catalogue(
            {"identity": {"pluginId": "p", "specification": "s", "specificationVersion": "v", "contentHash": "h"}}
        )


@pytest.mark.unit
def test_parse_endpoints_not_array_raises() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = {}
    with pytest.raises(CatalogueParseError, match="endpoints"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_endpoint_entry_not_object_raises() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = ["not-an-object"]
    with pytest.raises(CatalogueParseError, match="endpoints\\[0\\]"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_endpoint_missing_endpoint_id_raises() -> None:
    doc = _minimal_catalogue_doc()
    entry = _endpoint_entry_doc()
    entry.pop("endpointId")
    doc["endpoints"] = [entry]
    with pytest.raises(CatalogueParseError, match="endpointId"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_endpoint_invalid_requirement_raises() -> None:
    doc = _minimal_catalogue_doc()
    doc["endpoints"] = [_endpoint_entry_doc(requirement="required")]
    with pytest.raises(CatalogueParseError, match="requirement"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_endpoint_resource_group_not_string_raises() -> None:
    doc = _minimal_catalogue_doc()
    entry = _endpoint_entry_doc()
    entry["resourceGroup"] = 42  # valid int JsonValue for negative test
    doc["endpoints"] = [entry]
    with pytest.raises(CatalogueParseError, match="resourceGroup"):
        parse_catalogue(doc)


@pytest.mark.unit
def test_parse_endpoint_resource_group_empty_string_raises() -> None:
    doc = _minimal_catalogue_doc()
    entry = _endpoint_entry_doc()
    entry["resourceGroup"] = ""
    doc["endpoints"] = [entry]
    with pytest.raises(CatalogueParseError, match="resourceGroup"):
        parse_catalogue(doc)


# ---------------------------------------------------------------------------
# EndpointCatalogueEntry is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_endpoint_catalogue_entry_is_frozen() -> None:
    entry = EndpointCatalogueEntry(
        endpoint_id="e",
        operation="GET",
        path="/foo",
        method="GET",
        resource_group="ais",
        requirement="mandatory",
        display_label="Foo",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.endpoint_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Catalogue is frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalogue_is_frozen() -> None:
    cat = parse_catalogue(_minimal_catalogue_doc())
    with pytest.raises(dataclasses.FrozenInstanceError):
        cat.endpoints = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Hash consistency with JSON serialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_catalogue_hash_from_json_bytes() -> None:
    data = json.dumps({"endpoints": []}).encode()
    h1 = compute_catalogue_hash(data)
    h2 = compute_catalogue_hash(data)
    assert h1 == h2
    assert h1 != compute_catalogue_hash(b"different")
