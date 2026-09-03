"""Final product-surface integration coverage for Open Banking DCR 3.4."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from django.test import Client

import tests.dcr_test_service as dcr_test_service_module
from conformance import cli
from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_store import run_store
from conformance.json_types import JsonObject, JsonValue
from conformance.result_gate import StructuredResultGateError, validate_structured_conformance_result
from tests.dcr_test_service import DcrTestService


@pytest.fixture(autouse=True)
def _reset_run_state() -> Iterator[None]:
    """Reset process-local run and authorization stores around each test.

    Yields:
        Control to the test.
    """
    run_store.reset()
    auth_session_store.reset()
    yield
    _wait_for_terminal_run()
    run_store.reset()
    auth_session_store.reset()


def _canonical_dcr_plan(service: DcrTestService, root: Path, *, full_scope: bool = False) -> JsonObject:
    """Build a canonical DCR plan referencing deterministic local credentials.

    Args:
        service: Running deterministic DCR protocol service.
        root: Directory receiving the SSA reference.
        full_scope: Whether to select all optional management endpoints.

    Returns:
        Canonical schemaVersion 1.0 plan.
    """
    ssa_path = root / "participant-ssa.jwt"
    ssa_path.write_text(service.protocol.software_statement_assertion, encoding="utf-8")
    endpoints: list[JsonValue] = [
        {
            "method": "POST",
            "path": "/register",
            "operationId": "RegisterClient",
            "required": True,
            "locked": True,
        }
    ]
    if full_scope:
        endpoints.extend(
            {
                "method": method,
                "path": "/register/{ClientId}",
                "operationId": operation_id,
                "required": False,
                "locked": False,
            }
            for method, operation_id in (
                ("GET", "GetClient"),
                ("PUT", "UpdateClient"),
                ("DELETE", "DeleteClient"),
            )
        )
    return {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_DCR",
            "scheme": "open-banking-uk",
            "name": "dynamic-client-registration",
            "version": "3.4",
        },
        "executionMode": "certification",
        "securityEnvironment": {
            "discoveryUrl": service.discovery_url,
            "clientAuthMethod": "tls_client_auth",
            "signingPrivateKeyPath": str(service.protocol.signing_private_key_path),
            "signingKeyId": "fixture-signing-key",
            "mtls": {
                "enabled": True,
                "certificatePath": str(service.tls.client_certificate_path),
                "privateKeyPath": str(service.tls.client_private_key_path),
                "caBundlePath": str(service.tls.ca_certificate_path),
            },
        },
        "endpoints": endpoints,
        "dynamicClientRegistration": {
            "softwareStatementAssertionPath": str(ssa_path),
            "registrationAudience": "aspsp123",
        },
        "metadata": {"aspspName": "Deterministic DCR service"},
    }


def _wait_for_terminal_run(*, timeout_seconds: float = 20.0) -> JsonObject | None:
    """Wait for the active singleton run to complete.

    Args:
        timeout_seconds: Maximum wait.

    Returns:
        Terminal result, or ``None`` when no active run exists.

    Raises:
        AssertionError: If a run remains active beyond the timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        active_ids = [
            run_id
            for run_id, record in run_store._runs.items()  # noqa: SLF001 - test-only singleton inspection.
            if record.status in {"pending", "running"}
        ]
        if not active_ids:
            completed = [
                record.result
                for record in run_store._runs.values()  # noqa: SLF001 - test-only singleton inspection.
                if record.status == "completed"
            ]
            return completed[-1] if completed else None
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for DCR run to complete")


@pytest.mark.integration
def test_cli_plan_load_prepare_run_and_safe_result(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI executes canonical POST coverage and persists only safe evidence."""
    plan_path = tmp_path / "dcr-plan.json"
    plan_path.write_text(json.dumps(_canonical_dcr_plan(dcr_test_service, tmp_path)), encoding="utf-8")
    monkeypatch.setattr(dcr_test_service_module, "_FIXED_NOW", int(time.time()))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run(["--test-plan", str(plan_path)])

    assert exit_code == 0
    result = cast(JsonObject, json.loads((tmp_path / "out" / "test-results.json").read_text(encoding="utf-8")))
    assert result["status"] == "passed"
    assert cast(JsonObject, result["summary"]) == {
        "total": 25,
        "passed": 25,
        "failed": 0,
        "warn": 0,
        "skipped": 0,
    }
    trace_groups = cast(list[JsonObject], cast(JsonObject, result["catalogue"])["traceGroups"])
    assert {group["status"] for group in trace_groups} == {"passed", "skipped"}
    persisted = json.dumps(result) + (tmp_path / "out" / "execution-log.ndjson").read_text(encoding="utf-8")
    assert dcr_test_service.protocol.software_statement_assertion not in persisted
    assert "fixture-client-material-" not in persisted
    assert "fixture-registration-token-" not in persisted
    assert "fixture-grant-token-" not in persisted


@pytest.mark.integration
def test_rest_launch_status_and_result_accept_dcr_local_references(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REST creation validates and executes DCR before exposing structured results."""
    monkeypatch.setattr(dcr_test_service_module, "_FIXED_NOW", int(time.time()))
    monkeypatch.chdir(tmp_path)
    client = Client()

    creation = client.post(
        "/api/runs/",
        data=json.dumps(_canonical_dcr_plan(dcr_test_service, tmp_path)),
        content_type="application/json",
    )

    assert creation.status_code == 201
    run_id = creation.json()["id"]
    result = _wait_for_terminal_run()
    assert result is not None
    status_response = client.get(f"/api/runs/{run_id}/")
    result_response = client.get(f"/api/runs/{run_id}/result/")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert result_response.status_code == 200
    assert result_response.json()["status"] == "passed"
    assert result_response.json()["summary"]["failed"] == 0
    assert len(result_response.json()["catalogue"]["traceGroups"]) == 10


@pytest.mark.integration
def test_browser_import_review_launch_and_run_detail_preserve_dcr_hierarchy(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """Browser import safely reviews DCR and snapshots all selected execution steps."""
    client = Client()
    plan = _canonical_dcr_plan(dcr_test_service, tmp_path, full_scope=True)
    import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})

    assert import_response.status_code == 302
    review_response = client.get(import_response["Location"])
    review_content = review_response.content.decode("utf-8")
    assert review_response.status_code == 200
    assert "34" in review_content
    assert dcr_test_service.protocol.software_statement_assertion not in review_content

    launch_url = import_response["Location"].replace("/review/", "/launch/")
    with patch("conformance.api.run_lifecycle._execute_run") as mock_execute:
        launch_response = client.post(launch_url)
        assert launch_response.status_code == 302
        run_id = launch_response["Location"].rstrip("/").split("/")[-1]
        record = run_store.get_run(run_id)
        assert record is not None
        assert len(record.planned_steps) == 79
        assert record.planned_steps[0].group == "DCR-001 / DCR-001-C01"
        detail = client.get(launch_response["Location"])
        content = detail.content.decode("utf-8")
        assert detail.status_code == 200
        assert "DCR-001-C01-S01" in content
        assert "DCR-001 / DCR-001-C01" in content
        assert mock_execute.call_args is not None
        run_store.mark_failed(run_id, error="test cleanup")


@pytest.mark.unit
def test_structured_result_gate_rejects_transcript_style_false_positive() -> None:
    """The operational gate rejects a failed case despite a passing top-level label."""
    raw_result: JsonObject = {
        "status": "passed",
        "summary": {"failed": 0},
        "steps": [{"name": "DCR-001-C01-S01", "status": "passed", "message": "ok"}],
        "catalogue": {
            "api": "dcr",
            "traceGroups": [
                {
                    "traceGroupId": "DCR-001",
                    "status": "failed",
                    "testCases": [
                        {
                            "testCaseId": "DCR-001-C01",
                            "status": "failed",
                            "steps": [{"stepId": "DCR-001-C01-S01", "status": "failed"}],
                        }
                    ],
                }
            ],
        },
    }

    with pytest.raises(StructuredResultGateError, match="failed conformance scenario"):
        validate_structured_conformance_result(raw_result)


@pytest.mark.unit
def test_structured_result_gate_accepts_explicit_optional_skips() -> None:
    """Endpoint-not-selected cases remain valid when every executed case passed."""
    raw_result: JsonObject = {
        "status": "passed",
        "summary": {"failed": 0},
        "steps": [{"name": "DCR-001-C01-S01", "status": "passed", "message": "ok"}],
        "catalogue": {
            "api": "dcr",
            "traceGroups": [
                {
                    "traceGroupId": "DCR-001",
                    "status": "passed",
                    "testCases": [
                        {
                            "testCaseId": "DCR-001-C01",
                            "status": "passed",
                            "steps": [{"stepId": "DCR-001-C01-S01", "status": "passed"}],
                        }
                    ],
                },
                {
                    "traceGroupId": "DCR-003",
                    "status": "skipped",
                    "skipReason": "endpoint-not-selected",
                    "testCases": [
                        {
                            "testCaseId": "DCR-003-C01",
                            "status": "skipped",
                            "skipReason": "endpoint-not-selected",
                            "steps": [{"stepId": "DCR-003-C01-S01", "status": "skipped"}],
                        }
                    ],
                },
            ],
        },
    }

    validated = validate_structured_conformance_result(raw_result)

    assert validated == {"status": "passed", "failed": 0, "stepCount": 1, "caseCount": 2}


@pytest.mark.unit
def test_result_gate_cli_reports_conformance_failure(tmp_path: Path) -> None:
    """Result-gate CLI returns one for a structured failed result."""
    from conformance import result_gate

    result_path = tmp_path / "failed-result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "summary": {"failed": 1},
                "steps": [{"name": "DCR-001-C01-S01", "status": "failed", "message": "failed"}],
            }
        ),
        encoding="utf-8",
    )

    assert result_gate.run([str(result_path)]) == 1
