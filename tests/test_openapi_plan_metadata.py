"""Unit tests for conformance.openapi_plan_metadata tree derivation helpers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from conformance.api.plan_builder import PlanPreview, build_plan_preview
from conformance.json_types import JsonValue
from conformance.manifest import Manifest, load_manifest_from_object
from conformance.model_bank_config import ModelBankConfig, parse_model_bank_config
from conformance.openapi_plan_metadata import (
    OpenApiOperation,
    StepTreeNode,
    build_plan_tree,
    load_openapi_operations,
    normalize_manifest_url,
)
from conformance.suite_catalog import SuiteMetadata, list_supported_suites, resolve_suite


def _ais_baseline_suite_metadata() -> SuiteMetadata:
    """Return the bundled v4.0.1 AIS certification baseline suite metadata."""
    metadata = next(
        (
            item
            for item in list_supported_suites()
            if item.standard == "ob-read-write"
            and item.spec_version == "v4.0.1"
            and item.api == "ais"
            and item.suite == "ais-certification-baseline"
        ),
        None,
    )
    assert metadata is not None
    return metadata


def _minimal_config(*, include_suite: bool) -> ModelBankConfig:
    """Build a parsed minimal model-bank configuration for plan preview tests."""
    raw_config: dict[str, JsonValue] = {
        "environment": "test-env",
        "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        "oauth": {
            "clientId": "test-client-id",
            "redirectUri": "https://conformance.example.com/callback",
            "resourceBaseUrl": "https://resource.example.com",
        },
    }
    if include_suite:
        raw_config["testSuite"] = {
            "standard": "ob-read-write",
            "specVersion": "v4.0.1",
            "profile": "fapi1-advanced",
            "suite": "ais-certification-baseline",
        }
    return parse_model_bank_config(raw_config, base_dir=Path.cwd())


def _ais_baseline_preview() -> tuple[SuiteMetadata, Manifest, PlanPreview]:
    """Resolve the AIS baseline suite and build a default preview for tree assertions."""
    suite_metadata = _ais_baseline_suite_metadata()
    resolved = resolve_suite(suite_metadata.to_suite_selection())
    preview = build_plan_preview(
        config=_minimal_config(include_suite=True),
        manifest=resolved.manifest,
        suite_metadata=resolved.metadata,
    )
    return resolved.metadata, resolved.manifest, preview


def _flatten_nodes(nodes: tuple[StepTreeNode, ...]) -> tuple[StepTreeNode, ...]:
    """Flatten a tree of step nodes into depth-first order."""
    flattened: list[StepTreeNode] = []
    for node in nodes:
        flattened.append(node)
        flattened.extend(_flatten_nodes(node.children))
    return tuple(flattened)


@pytest.mark.unit
def test_load_openapi_operations_returns_operations_for_ais_suite() -> None:
    """load_openapi_operations returns indexed operations for a v4.0.1 AIS suite."""
    operations = load_openapi_operations(_ais_baseline_suite_metadata())

    assert ("GET", "/accounts") in operations
    assert ("POST", "/account-access-consents") in operations
    assert operations
    assert all(isinstance(operation, OpenApiOperation) and operation.summary for operation in operations.values())


@pytest.mark.unit
def test_load_openapi_operations_returns_empty_for_none() -> None:
    """load_openapi_operations returns an empty dict when suite_metadata is None."""
    assert load_openapi_operations(None) == {}


@pytest.mark.unit
def test_load_openapi_operations_returns_empty_for_unknown_standard() -> None:
    """load_openapi_operations returns an empty dict for an unrecognised standard."""
    unknown_metadata = replace(_ais_baseline_suite_metadata(), standard="cvrp")

    assert load_openapi_operations(unknown_metadata) == {}


@pytest.mark.unit
def test_normalize_manifest_url_strips_resource_base_url_placeholder() -> None:
    """normalize_manifest_url removes the {{resourceBaseUrl}} prefix."""
    assert normalize_manifest_url("{{resourceBaseUrl}}/accounts") == "/accounts"


@pytest.mark.unit
def test_normalize_manifest_url_converts_double_brace_params() -> None:
    """normalize_manifest_url converts {{ConsentId}} to {ConsentId}."""
    url = "{{resourceBaseUrl}}/account-access-consents/{{ConsentId}}"
    assert normalize_manifest_url(url) == "/account-access-consents/{ConsentId}"


@pytest.mark.unit
def test_normalize_manifest_url_strips_query_string() -> None:
    """normalize_manifest_url removes query strings."""
    assert normalize_manifest_url("/accounts?limit=10") == "/accounts"


@pytest.mark.unit
def test_normalize_manifest_url_handles_plain_path() -> None:
    """normalize_manifest_url returns a clean OpenAPI path unchanged."""
    assert normalize_manifest_url("/accounts/{AccountId}") == "/accounts/{AccountId}"


@pytest.mark.unit
def test_build_plan_tree_returns_nodes_for_ais_baseline_suite() -> None:
    """build_plan_tree returns a non-empty tree for the v4.0.1 AIS baseline suite."""
    suite_metadata, manifest, preview = _ais_baseline_preview()

    nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )

    assert nodes
    assert all(isinstance(node, StepTreeNode) for node in nodes)


@pytest.mark.unit
def test_build_plan_tree_includes_all_manifest_steps() -> None:
    """build_plan_tree does not drop any manifest step from the tree."""
    suite_metadata, manifest, preview = _ais_baseline_preview()

    nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )

    manifest_step_ids = {step.id for step in manifest.steps}
    tree_step_ids = {step_id for node in nodes for step_id in node.descendant_step_ids}

    assert tree_step_ids == manifest_step_ids


@pytest.mark.unit
def test_build_plan_tree_returns_fallback_for_no_suite_metadata() -> None:
    """build_plan_tree returns a single fallback group when suite_metadata is None."""
    manifest = load_manifest_from_object(
        {
            "schemaVersion": "v1",
            "name": "Fallback grouping manifest",
            "steps": [
                {
                    "id": "accounts-list",
                    "name": "List accounts",
                    "request": {"method": "GET", "url": "https://resource.example.com/accounts"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
                {
                    "id": "accounts-balances",
                    "name": "List balances",
                    "request": {"method": "GET", "url": "https://resource.example.com/balances"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                },
            ],
        }
    )
    preview = build_plan_preview(config=_minimal_config(include_suite=False), manifest=manifest, suite_metadata=None)

    nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=None,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )

    assert len(nodes) == 1
    assert nodes[0].label == "Other steps"
    assert set(nodes[0].descendant_step_ids) == {step.id for step in manifest.steps}


@pytest.mark.unit
def test_build_plan_tree_step_counts_match_manifest() -> None:
    """build_plan_tree top-level total_count equals total manifest step count."""
    suite_metadata, manifest, preview = _ais_baseline_preview()

    nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )

    assert sum(node.total_count for node in nodes) == len(manifest.steps)


@pytest.mark.unit
def test_build_plan_tree_mandatory_count_correct() -> None:
    """build_plan_tree mandatory_count on nodes equals count of mandatory steps in that subtree."""
    suite_metadata, manifest, preview = _ais_baseline_preview()

    nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )
    mandatory_ids = {step.id for step in manifest.steps if step.mandatory}

    for node in _flatten_nodes(nodes):
        expected_count = sum(1 for step_id in node.descendant_step_ids if step_id in mandatory_ids)
        assert node.mandatory_count == expected_count


@pytest.mark.unit
def test_build_plan_tree_node_ids_are_stable() -> None:
    """build_plan_tree produces the same node IDs on repeated calls with same inputs."""
    suite_metadata, manifest, preview = _ais_baseline_preview()

    first_nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )
    second_nodes = build_plan_tree(
        manifest=manifest,
        suite_metadata=suite_metadata,
        selected_plan=preview.selected_plan,
        rows=preview.rows,
        auth_bundles=preview.auth_inventory,
    )

    assert tuple(node.id for node in first_nodes) == tuple(node.id for node in second_nodes)
