"""Unit tests for catalogue-native planning and primitive execution."""

from __future__ import annotations

import pytest

from conformance.catalogue_execution import (
    CataloguePrimitiveResult,
    CompiledCatalogueRun,
    CompiledCatalogueStep,
    compile_catalogue_run_plan,
    execute_compiled_catalogue_run,
)
from conformance.plan_executor import build_default_registry, compile_catalogue_graph_for_plan
from conformance.plugins.registry import PluginRegistry
from conformance.run_plan_v2 import EndpointSelection, RunPlanV2, RunPlanV2TargetCoordinates


def _rw_plan(
    *,
    version: str = "v4.0.1",
    resource_groups: tuple[str, ...] = ("ais",),
    endpoint_selections: tuple[EndpointSelection, ...] = (),
) -> RunPlanV2:
    """Build a Read/Write RunPlanV2 fixture.

    Args:
        version: Specification version.
        resource_groups: Selected resource groups.
        endpoint_selections: Optional endpoint selections.

    Returns:
        A RunPlanV2 targeting the Read/Write catalogue.
    """
    return RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version=version,
            catalogue_hash="sha256:unknown",
        ),
        resource_groups=resource_groups,
        endpoint_selections=endpoint_selections,
    )


def _dcr_plan(
    *,
    version: str = "3.3",
    endpoint_selections: tuple[EndpointSelection, ...] = (),
) -> RunPlanV2:
    """Build a DCR RunPlanV2 fixture.

    Args:
        version: DCR specification version.
        endpoint_selections: Optional endpoint selections.

    Returns:
        A RunPlanV2 targeting the DCR catalogue.
    """
    return RunPlanV2(
        schema_version="2",
        target=RunPlanV2TargetCoordinates(
            standard="obl",
            specification="dynamic-client-registration",
            security_profile="fapi1-advanced",
            specification_version=version,
            catalogue_hash="sha256:unknown",
        ),
        resource_groups=(),
        endpoint_selections=endpoint_selections,
    )


def _compile(plan: RunPlanV2) -> CompiledCatalogueRun:
    """Compile a plan with the default built-in plugin registry.

    Args:
        plan: RunPlanV2 to compile.

    Returns:
        The compiled catalogue-native run graph.
    """
    return compile_catalogue_run_plan(plan, registry=build_default_registry())


def _selection(endpoint_id: str, *, method: str = "GET", selected: bool = True) -> EndpointSelection:
    """Build an endpoint selection fixture.

    Args:
        endpoint_id: Catalogue endpoint identifier.
        method: HTTP method accepted by selection validation.
        selected: Whether the endpoint is selected.

    Returns:
        An EndpointSelection fixture.
    """
    return EndpointSelection(endpoint_id=endpoint_id, operation=method, selected=selected, field_values={})


@pytest.mark.unit
def test_compile_catalogue_run_plan_supports_multi_resource_group_graph() -> None:
    """Read/Write plans compile into one multi-resource-group catalogue graph."""
    compiled = _compile(_rw_plan(resource_groups=("ais", "pis")))
    step_ids = [step.step_id for step in compiled.steps]

    assert step_ids.count("read-write.discovery") == 1
    assert step_ids.count("read-write.jwks") == 1
    assert "read-write.ais.client-credentials-token" in step_ids
    assert "read-write.pis.client-credentials-token" in step_ids
    assert "read-write.cbpii.client-credentials-token" not in step_ids
    assert compiled.selected_resource_groups == ("ais", "pis")
    assert all(step.resource_group in {None, "ais", "pis"} for step in compiled.steps)
    assert compiled.readiness_policy is not None
    assert compiled.masking is not None


@pytest.mark.unit
def test_compile_catalogue_run_plan_omits_unselected_endpoint_tests() -> None:
    """Explicitly unselected endpoint coverage is absent from compiled tests."""
    compiled = _compile(
        _rw_plan(
            resource_groups=("ais",),
            endpoint_selections=(
                _selection("ais.accounts.get-accounts"),
                _selection("ais.accounts.get-account", selected=False),
            ),
        )
    )
    compiled_test_ids = {step.test_id for step in compiled.steps if step.test_id is not None}

    assert compiled.selected_endpoint_ids == ("ais.accounts.get-accounts",)
    assert "ais.accounts.get-accounts.http" in compiled_test_ids
    assert "ais.accounts.get-account.http" not in compiled_test_ids
    assert "ais.accounts.get-account" in compiled.omitted_mandatory_endpoint_ids


@pytest.mark.unit
def test_compile_catalogue_run_plan_keeps_migration_tests_version_scoped() -> None:
    """Catalogue target-version predicates select only matching migration tests."""
    compiled = _compile(_rw_plan(version="v4.0.0", resource_groups=("ais",)))
    test_ids = {step.test_id for step in compiled.steps if step.test_id is not None}

    assert "OB-400-ACC-100400" in test_ids
    assert compiled.target.specification_version == "v4.0.0"
    assert compiled.catalogue_identity.specification_version == "v4.0.0"


@pytest.mark.unit
def test_compile_catalogue_graph_for_plan_uses_default_registry() -> None:
    """The plan-executor bridge exposes the catalogue-native compiler."""
    compiled = compile_catalogue_graph_for_plan(_rw_plan(resource_groups=("ais",)))

    assert compiled.plugin_id == "read-write"
    assert compiled.steps


@pytest.mark.unit
def test_execute_compiled_catalogue_run_skips_only_dependency_blocked_selected_tests() -> None:
    """A failed resource-group prerequisite skips only selected tests in that group."""
    compiled = _compile(
        _rw_plan(
            resource_groups=("ais", "pis"),
            endpoint_selections=(
                _selection("ais.accounts.get-accounts"),
                _selection("pis.domestic-payments.get"),
            ),
        )
    )
    result = execute_compiled_catalogue_run(
        compiled,
        primitive_handlers={
            "oidc-discovery": _passing_handler,
            "jwks-fetch": _passing_handler,
            "oauth-token": _passing_handler,
            "read-write.psu-authorization": _auth_handler_with_ais_failure,
            "http-step": _passing_handler,
            "migration-source-step": _passing_handler,
        },
    )
    results = {step_result.step_id: step_result for step_result in result.step_results}

    assert results["read-write.ais.psu-authorization"].outcome == "failed"
    assert results["ais.accounts.get-accounts.http"].outcome == "skipped"
    assert results["pis.domestic-payments.get.http"].outcome == "passed"
    assert "ais.accounts.get-account.http" not in results


def _passing_handler(step: CompiledCatalogueStep) -> CataloguePrimitiveResult:
    """Return a passed primitive result.

    Args:
        step: Compiled step being executed.

    Returns:
        A passed primitive result.
    """
    return CataloguePrimitiveResult(outcome="passed", detail=f"{step.step_id} passed")


def _auth_handler_with_ais_failure(step: CompiledCatalogueStep) -> CataloguePrimitiveResult:
    """Fail AIS authorisation while passing other resource groups.

    Args:
        step: Compiled step being executed.

    Returns:
        A primitive result reflecting the resource-group-specific outcome.
    """
    if step.resource_group == "ais":
        return CataloguePrimitiveResult(outcome="failed", detail="AIS authorisation failed")
    return CataloguePrimitiveResult(outcome="passed", detail=f"{step.step_id} passed")


@pytest.mark.unit
def test_execute_compiled_catalogue_run_accepts_primitive_type_handler_fallback() -> None:
    """Primitive type handlers allow plugins to share handler implementations."""
    compiled = _compile(
        _rw_plan(
            resource_groups=("ais",),
            endpoint_selections=(_selection("ais.accounts.get-accounts"),),
        )
    )

    result = execute_compiled_catalogue_run(
        compiled,
        primitive_handlers={
            "oidc-discovery": _passing_handler,
            "jwks-fetch": _passing_handler,
            "oauth-token": _passing_handler,
            "psu-authorization": _passing_handler,
            "http-step": _passing_handler,
            "migration-source-step": _passing_handler,
        },
    )

    assert all(step_result.outcome == "passed" for step_result in result.step_results)


@pytest.mark.unit
def test_compile_catalogue_run_plan_rejects_unknown_resource_group() -> None:
    """Unknown selected resource groups fail during catalogue compilation."""
    registry = PluginRegistry()
    for plugin_id in build_default_registry().plugin_ids:
        registry.register(build_default_registry().get(plugin_id))

    with pytest.raises(ValueError, match="Unknown resource group"):
        compile_catalogue_run_plan(_rw_plan(resource_groups=("not-a-group",)), registry=registry)


@pytest.mark.unit
def test_compile_catalogue_run_plan_dcr_exposes_primary_scenario_ids() -> None:
    """DCR catalogues compile primary scenario IDs from schema v2 executable tests."""
    compiled = _compile(_dcr_plan(version="3.4"))
    test_ids = {step.test_id for step in compiled.steps if step.test_id is not None}

    assert test_ids == {
        "DCR-001",
        "DCR-002",
        "DCR-003",
        "DCR-004",
        "DCR-005",
        "DCR-007",
        "DCR-008",
        "DCR-009",
        "DCR-010",
        "DCR-011",
    }
    assert compiled.plugin_id == "dcr"
    assert compiled.selected_resource_groups == ()
    assert compiled.readiness_policy is not None
    assert compiled.readiness_policy.certification_status == "non-certifying"
    assert compiled.masking is not None
    assert "dcr.ssaPath" in compiled.masking.masked_fields


@pytest.mark.unit
def test_compile_catalogue_run_plan_dcr_get_put_delete_selection() -> None:
    """GET, PUT, and DELETE DCR endpoint selections use catalogue-native IDs."""
    compiled = _compile(
        _dcr_plan(
            endpoint_selections=(
                _selection("dcr.register.get", method="GET"),
                _selection("dcr.register.put", method="PUT"),
                _selection("dcr.register.delete", method="DELETE"),
            ),
        )
    )
    test_ids = {step.test_id for step in compiled.steps if step.test_id is not None}

    assert compiled.selected_endpoint_ids == ("dcr.register.get", "dcr.register.put", "dcr.register.delete")
    assert test_ids == {"DCR-002", "DCR-003", "DCR-004"}
    assert "dcr.register.post" in compiled.omitted_mandatory_endpoint_ids
    assert "get-registration" not in compiled.selected_endpoint_ids


@pytest.mark.unit
def test_compile_catalogue_run_plan_dcr_rejects_stale_operation_ids() -> None:
    """Old pre-catalogue DCR operation IDs are rejected during compilation."""
    with pytest.raises(ValueError, match="get-registration"):
        _compile(_dcr_plan(endpoint_selections=(_selection("get-registration", method="GET"),)))
