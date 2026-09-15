"""Component tests for REST run creation from catalogue-backed test plans."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client

from tests.support.catalogue_plans import build_config_json as _config_json
from tests.support.catalogue_plans import build_plan_spec_json as _plan_spec_json

pytestmark = pytest.mark.component


def test_api_create_run_rejects_removed_plan_spec(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "planSpec": _plan_spec_json()}

    with patch("conformance.api.views.start_run", return_value={"id": "run-1", "status": "pending"}) as start_run_mock:
        response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "Legacy run request field(s) are no longer supported" in response.json()["error"]
    start_run_mock.assert_not_called()


def test_api_create_run_rejects_removed_manifest_field(tmp_path: Path) -> None:
    body = {"config": _config_json(tmp_path), "manifest": {"schemaVersion": "v1"}}

    response = Client().post("/api/runs/", data=json.dumps(body), content_type="application/json")

    assert response.status_code == 400
    assert "Legacy run request field(s) are no longer supported" in response.json()["error"]
