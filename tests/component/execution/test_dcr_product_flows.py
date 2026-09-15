"""Component tests for the CLI, REST, and browser product surfaces of DCR 3.4."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

import pytest
from django.test import Client

import tests.support.dcr_test_service as dcr_test_service_module
from conformance import cli
from conformance.api.run_store import run_store
from conformance.json_types import JsonObject, JsonValue
from tests.support.dcr_test_service import DcrProtocolService, DcrTestService
from tests.support.run_execution import StubbedRunExecution

pytestmark = pytest.mark.component


def _participant_dcr_plan(service: DcrProtocolService, root: Path, *, full_scope: bool = False) -> JsonObject:
    """Build a participant DCR plan referencing deterministic local credentials.

    Args:
        service: Running deterministic DCR protocol service.
        root: Directory receiving the SSA reference.
        full_scope: Whether to select all optional management endpoints.

    Returns:
        Participant-plan 1.0 document.
    """
    ssa_path = root / "participant-ssa.jwt"
    ssa_path.write_text(service.protocol.software_statement_assertion, encoding="utf-8")
    selected_capabilities: list[JsonValue] = ["dcr.v34.capability.registration"]
    if full_scope:
        selected_capabilities.extend(
            (
                "dcr.v34.capability.retrieval",
                "dcr.v34.capability.update",
                "dcr.v34.capability.deletion",
            )
        )
    return {
        "documentType": "participant-plan",
        "executionConfiguration": {
            "compatibilityRuntimeInputs": {},
            "dynamicClientRegistration": {
                "softwareStatementAssertionPath": str(ssa_path),
                "registrationAudience": "aspsp123",
            },
            "metadata": {"aspspName": "Deterministic DCR service"},
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
        },
        "id": "participant.dcr-product-flow",
        "predefinedInputs": [],
        "schemaVersion": "1.0",
        "scheme": "open-banking-uk",
        "securityProfile": "all",
        "selectedCapabilityIds": selected_capabilities,
        "specification": {
            "id": "dynamic-client-registration",
            "requirementsScope": "dcr",
            "version": "3.4",
        },
        "suiteReleaseId": "obl.open-banking-mvp.catalogue-release",
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


@pytest.mark.usefixtures("api_singleton_stores")
def test_cli_participant_plan_runs_dcr_with_stable_traceability(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI executes participant-plan POST coverage with safe stable evidence.

    Kept on the loopback listener because the CLI builds its own mTLS client
    from the participant's configured certificate, key, and CA bundle.
    """
    plan_path = tmp_path / "dcr-plan.json"
    plan_path.write_text(
        json.dumps(_participant_dcr_plan(dcr_test_service, tmp_path)),
        encoding="utf-8",
    )
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
    traceability = cast(JsonObject, result["traceability"])
    manifest = cast(JsonObject, traceability["executionManifest"])
    assert len(cast(list[JsonObject], manifest["steps"])) == 6
    assert all(step["resultStatus"] != "missing" for step in cast(list[JsonObject], manifest["steps"]))
    trace_groups = cast(list[JsonObject], cast(JsonObject, result["catalogue"])["traceGroups"])
    assert {group["status"] for group in trace_groups} == {"passed", "skipped"}
    persisted = json.dumps(result) + (tmp_path / "out" / "execution-log.ndjson").read_text(encoding="utf-8")
    assert dcr_test_service.protocol.software_statement_assertion not in persisted
    assert "fixture-client-material-" not in persisted
    assert "fixture-registration-token-" not in persisted
    assert "fixture-grant-token-" not in persisted


@pytest.mark.usefixtures("api_singleton_stores")
def test_rest_launch_status_and_result_accept_dcr_local_references(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REST creation validates and executes DCR before exposing structured results.

    Kept on the loopback listener because the REST lifecycle launches the
    product's own mTLS client in a background worker, which this test owns and
    waits out before asserting on the structured result.
    """
    monkeypatch.setattr(dcr_test_service_module, "_FIXED_NOW", int(time.time()))
    monkeypatch.chdir(tmp_path)
    client = Client()

    creation = client.post(
        "/api/runs/",
        data=json.dumps(_participant_dcr_plan(dcr_test_service, tmp_path)),
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


def test_browser_import_reviews_but_blocks_unmapped_dcr_work(
    dcr_protocol_service: DcrProtocolService,
    stubbed_run_execution: StubbedRunExecution,
    tmp_path: Path,
) -> None:
    """Browser import exposes replacement DCR work the compatibility runtime cannot observe."""
    client = Client()
    plan = _participant_dcr_plan(dcr_protocol_service, tmp_path, full_scope=True)
    import_response = client.post("/builder/import/", data={"plan_json": json.dumps(plan)})

    assert import_response.status_code == 302
    review_response = client.get(import_response["Location"])
    review_content = review_response.content.decode("utf-8")
    assert review_response.status_code == 200
    assert "dcr.v34.test.retrieval.revoked-token" in review_content
    assert "cannot provide a stable observation" in review_content
    assert dcr_protocol_service.protocol.software_statement_assertion not in review_content

    launch_url = import_response["Location"].replace("/review/", "/launch/")
    launch_response = client.post(launch_url)
    assert launch_response.status_code == 400
    stubbed_run_execution.assert_not_launched()
