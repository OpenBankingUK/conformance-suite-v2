"""Component tests for REST run creation from catalogue-backed test plans."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client

from conformance.catalogue import CatalogueKey
from tests.support.catalogue_plans import build_canonical_plan_json as _canonical_plan_json
from tests.support.catalogue_plans import build_config_json as _config_json
from tests.support.catalogue_plans import build_plan_spec_json as _plan_spec_json
from tests.support.catalogue_plans import build_test_catalogue as _test_catalogue

pytestmark = pytest.mark.component


def test_api_create_run_rejects_removed_plan_spec(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "planSpec": _plan_spec_json()}

    with patch("conformance.api.views.start_run", return_value={"id": "run-1", "status": "pending"}) as start_run_mock:
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "Legacy run request field(s) are no longer supported" in response.json()["error"]
    start_run_mock.assert_not_called()


def test_api_create_run_accepts_capability_selected_canonical_test_plan(tmp_path: Path) -> None:
    body = _canonical_plan_json(capabilities=("accounts.balances",))

    with (
        patch("conformance.test_plan_validation.supported_catalogues", return_value=(_test_catalogue(),)),
        patch("conformance.api.views.start_run", return_value={"id": "run-1", "status": "pending"}) as start_run_mock,
    ):
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    compiled_plan = start_run_mock.call_args.kwargs["compiled_plan"]
    assert response.status_code == 201
    assert [case.test_case_id for case in compiled_plan.test_cases] == ["accounts-read", "accounts-balances"]
    assert [capability.capability_id for capability in compiled_plan.traceability.selected_capabilities] == [
        "accounts.read",
        "accounts.balances",
    ]


def test_api_create_run_accepts_nested_canonical_test_plan(tmp_path: Path) -> None:
    body = {"testPlan": _canonical_plan_json(capabilities=("accounts.balances",))}

    with (
        patch("conformance.test_plan_validation.supported_catalogues", return_value=(_test_catalogue(),)),
        patch("conformance.api.views.start_run", return_value={"id": "run-1", "status": "pending"}) as start_run_mock,
    ):
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    compiled_plan = start_run_mock.call_args.kwargs["compiled_plan"]
    assert response.status_code == 201
    assert response.json()["id"] == "run-1"
    assert compiled_plan.catalogue_key == CatalogueKey(
        standard="open-banking-uk",
        version="4.0.1",
        api="read-write",
    )
    assert [case.test_case_id for case in compiled_plan.test_cases] == ["accounts-read", "accounts-balances"]
    assert start_run_mock.call_args.kwargs["runtime_inputs"]["accessToken"] == "secret-access-token"


def test_api_create_run_rejects_unknown_capability_selected_plan(tmp_path: Path) -> None:
    """REST test-plan validation rejects capability ids outside the catalogue contract."""
    body = _canonical_plan_json(capabilities=("accounts.unknown",))

    with patch("conformance.test_plan_validation.supported_catalogues", return_value=(_test_catalogue(),)):
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "unknown capability 'accounts.unknown'" in response.json()["error"]


def test_api_create_run_rejects_removed_manifest_field(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "manifest": {"schemaVersion": "v1"}}

    response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "Legacy run request field(s) are no longer supported" in response.json()["error"]
