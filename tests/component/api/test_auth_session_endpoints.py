"""Component tests for the loopback REST PSU auth-session endpoints."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from conformance.api.auth_session_store import auth_session_store
from conformance.api.run_lifecycle import BrowserParticipantActionLogger
from conformance.api.run_store import run_store
from conformance.approved_releases import APPROVED_RELEASE_POLICY_SCHEMA_VERSION

pytestmark = pytest.mark.component


VALID_CONFIG = {
    "environment": "test-env",
    "discoveryUrl": "https://example.com/.well-known/openid-configuration",
}

VALID_TEST_PLAN = {
    "schemaVersion": "1.0",
    "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1", "profile": "FAPI1_ADVANCED"},
    "securityEnvironment": {
        "discoveryUrl": "https://example.com/.well-known/openid-configuration",
        "resourceBaseUrl": "https://resource.example.com",
    },
    "resourceGroups": [
        {
            "id": "AIS",
            "endpoints": [{"method": "GET", "path": "/open-banking/v4.0/aisp/accounts"}],
        }
    ],
    "businessTestData": {},
    "metadata": {},
}


@pytest.mark.usefixtures("api_singleton_stores")
class TestRegisterAuthSessionEndpoint:
    """``POST /api/runs/<id>/auth-sessions/`` registers a PSU auth session."""

    def test_returns_201_with_server_generated_state(self) -> None:
        """A bodyless request returns 201 with a server-generated state."""
        client = Client()
        record = run_store.create_run()
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "awaiting"
        assert "createdAt" in body
        assert len(body["state"]) >= 32

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        record = run_store.create_run()

        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/?timeZone=Europe/London")

        assert response.status_code == 201
        body = response.json()
        assert body["displayTimeZone"] == "Europe/London"
        created_at_local = datetime.fromisoformat(body["createdAt"]).astimezone(ZoneInfo("Europe/London"))
        assert body["createdAtLocal"] == created_at_local.isoformat()

    def test_accepts_caller_supplied_state_above_entropy_bar(self) -> None:
        """A caller-supplied state of sufficient length is accepted."""
        client = Client()
        record = run_store.create_run()
        state = "x" * 32
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.json()["state"] == state

    def test_rejects_caller_supplied_state_below_entropy_bar(self) -> None:
        """A short caller-supplied state is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": "short"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_non_string_state(self) -> None:
        """A non-string ``state`` field is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": 123}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_invalid_json_body(self) -> None:
        """Malformed JSON in the body yields 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_non_json_body_without_content_type(self) -> None:
        """A non-empty body is parsed regardless of Content-Type.

        Prevents the silent-drop bug where a caller posting
        ``{"state": ...}`` without ``Content-Type: application/json``
        would have their state ignored and receive 201 with a
        server-generated state instead.
        """
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    def test_rejects_non_object_body(self) -> None:
        """A non-object JSON body is rejected with 400."""
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps([1, 2]),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_rejects_multipart_with_fields(self) -> None:
        """``multipart/form-data`` carrying fields is rejected with 400.

        Guards against the silent-drop bug where a caller posting
        ``state`` as a multipart form field would otherwise be ignored
        and receive 201 with a server-generated state. The empty
        multipart envelope produced by Django's test client when
        ``client.post(url)`` is called without ``data`` is still
        accepted as bodyless (covered by
        :meth:`test_returns_201_with_server_generated_state`).
        """
        client = Client()
        record = run_store.create_run()
        response = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data={"state": "y" * 40},
        )
        assert response.status_code == 400

    def test_returns_404_for_unknown_run(self) -> None:
        """An unknown run id yields 404."""
        client = Client()
        response = client.post("/api/runs/missing/auth-sessions/")
        assert response.status_code == 404

    def test_returns_409_for_terminal_run(self) -> None:
        """Registration against a completed run is rejected with 409."""
        client = Client()
        record = run_store.create_run()
        run_store.mark_running(record.run_id)
        run_store.mark_completed(record.run_id, result={"status": "passed"})
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 409

    def test_returns_409_for_duplicate_state(self) -> None:
        """Re-registering the same caller-supplied state yields 409."""
        client = Client()
        record = run_store.create_run()
        state = "y" * 40
        first = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/runs/{record.run_id}/auth-sessions/",
            data=json.dumps({"state": state}),
            content_type="application/json",
        )
        assert second.status_code == 409

    def test_returns_400_when_per_run_cap_exceeded(self) -> None:
        """Registering past the per-run cap yields 400."""
        from conformance.api.auth_session_store import MAX_SESSIONS_PER_RUN

        client = Client()
        record = run_store.create_run()
        for _ in range(MAX_SESSIONS_PER_RUN):
            ok = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
            assert ok.status_code == 201
        over = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert over.status_code == 400

    def test_emits_auth_session_registered_event(self) -> None:
        """Successful registration appends an ``auth-session-registered`` event."""
        client = Client()
        record = run_store.create_run()
        response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")
        assert response.status_code == 201
        live = run_store._runs[record.run_id]  # noqa: SLF001 — read live logger
        assert live.execution_logger is not None
        events = live.execution_logger.events()
        types = [event.type for event in events]
        assert "auth-session-registered" in types
        registered = next(event for event in events if event.type == "auth-session-registered")
        assert registered.payload["state"] == response.json()["state"]
        assert registered.payload["status"] == "awaiting"

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        """The loopback guard applies to the register endpoint."""
        client = Client(REMOTE_ADDR="10.0.0.5")
        response = client.post("/api/runs/some-id/auth-sessions/")
        assert response.status_code == 403

    def test_rolls_back_session_when_run_terminates_during_register(self) -> None:
        """Race fix: a run completing mid-register must not leak a session.

        Simulates the run lifecycle transitioning the run to
        ``completed`` (and sweeping its sessions) between
        ``auth_session_store.register`` and the post-register run-record
        revalidation. The view must roll back the just-created session
        and return 409 instead of 201, preventing the session from
        outliving its parent run.
        """
        from unittest.mock import patch

        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        original_get_run = run_store.get_run
        call_count = {"n": 0}

        def get_run_with_terminal_race(run_id: str) -> object:
            """Return the live record on the pre-check, terminate on revalidation."""
            call_count["n"] += 1
            if call_count["n"] == 2:
                run_store.mark_running(run_id)
                run_store.mark_completed(run_id, result={"status": "passed"})
            return original_get_run(run_id)

        with patch.object(run_store, "get_run", side_effect=get_run_with_terminal_race):
            response = client.post(f"/api/runs/{record.run_id}/auth-sessions/")

        assert response.status_code == 409
        assert response.json()["status"] == "completed"
        # The just-created session must have been rolled back.
        assert auth_session_store.for_run(record.run_id) == []


@pytest.mark.usefixtures("api_singleton_stores")
class TestGetAuthSessionEndpoint:
    """``GET /api/runs/<id>/auth-sessions/<state>/`` returns session state."""

    def test_returns_awaiting_session(self) -> None:
        """A freshly registered session is returned with status ``awaiting``."""
        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == registered["state"]
        assert body["status"] == "awaiting"
        assert "createdAt" in body
        assert "code" not in body
        assert "capturedAt" not in body

    def test_appends_local_timestamp_display_fields_when_time_zone_is_requested(self) -> None:
        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_code(registered["state"], "auth-code-xyz")

        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/?timeZone=Europe/London",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["displayTimeZone"] == "Europe/London"
        created_at_local = datetime.fromisoformat(body["createdAt"]).astimezone(ZoneInfo("Europe/London"))
        captured_at_local = datetime.fromisoformat(body["capturedAt"]).astimezone(ZoneInfo("Europe/London"))
        assert body["createdAtLocal"] == created_at_local.isoformat()
        assert body["capturedAtLocal"] == captured_at_local.isoformat()

    def test_returns_captured_session_with_code(self) -> None:
        """After ``capture_code`` the response includes the code and capturedAt."""
        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_code(registered["state"], "auth-code-xyz")
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "captured"
        assert body["code"] == "auth-code-xyz"
        assert "capturedAt" in body

    def test_returns_error_session(self) -> None:
        """After ``capture_error`` the response includes error fields."""
        from conformance.api.auth_session_store import auth_session_store

        client = Client()
        record = run_store.create_run()
        registered = client.post(f"/api/runs/{record.run_id}/auth-sessions/").json()
        auth_session_store.capture_error(
            registered["state"],
            error="access_denied",
            description="User declined consent",
        )
        response = client.get(
            f"/api/runs/{record.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["error"] == "access_denied"
        assert body["errorDescription"] == "User declined consent"

    def test_returns_404_for_unknown_run(self) -> None:
        """Unknown run id yields 404."""
        client = Client()
        response = client.get("/api/runs/missing/auth-sessions/any-state/")
        assert response.status_code == 404

    def test_returns_404_for_unknown_state(self) -> None:
        """Unknown state under a known run yields 404."""
        client = Client()
        record = run_store.create_run()
        response = client.get(f"/api/runs/{record.run_id}/auth-sessions/bogus/")
        assert response.status_code == 404

    def test_returns_404_for_cross_run_lookup(self) -> None:
        """A state registered against another run is not visible.

        Exercises the ``(run_id, state)`` scoping guarantee directly:
        the lookup runs against an *existing* but unrelated run record,
        so a 404 here is attributable to run-scoped binding rather than
        a missing run.
        """
        client = Client()
        run_a = run_store.create_run()
        run_store.mark_running(run_a.run_id)
        run_store.mark_completed(run_a.run_id, result={"status": "passed"})
        run_b = run_store.create_run()
        registered = client.post(f"/api/runs/{run_b.run_id}/auth-sessions/").json()

        # Look up ``run_b``'s state under ``run_a``'s still-existing run id.
        response = client.get(
            f"/api/runs/{run_a.run_id}/auth-sessions/{registered['state']}/",
        )
        assert response.status_code == 404

    def test_non_loopback_request_is_rejected_with_403(self) -> None:
        """The loopback guard applies to the get endpoint."""
        client = Client(REMOTE_ADDR="10.0.0.5")
        response = client.get("/api/runs/some-id/auth-sessions/some-state/")
        assert response.status_code == 403


@pytest.mark.usefixtures("api_singleton_stores")
class TestExecuteRunDiscardsAuthSessions:
    """The run lifecycle must drop auth sessions on terminal exit.

    Awaiting auth sessions registered against a run MUST NOT outlive that
    run. The hook lives in ``_execute_run``'s ``finally`` block so it
    covers both the happy path (``mark_completed``) and the failure path
    (``mark_failed``). These tests exercise the hook directly rather than
    relying on the full HTTP request lifecycle.
    """

    def test_completed_run_discards_awaiting_auth_sessions(self) -> None:
        """Sessions are discarded after a successful run completes."""
        from datetime import datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 2

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            return_value=fake_result,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(record.run_id) == []
        # The run itself transitioned to completed (sanity check).
        assert run_store.get_run(record.run_id) is not None
        assert run_store.get_run(record.run_id).status == "completed"  # type: ignore[union-attr]

    def test_reset_run_before_terminal_transition_does_not_raise_and_discards_sessions(self) -> None:
        """Lifecycle cleanup tolerates run-store resets that remove the run."""
        from datetime import datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 1

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )

        def reset_store_then_return_result(*args: object, **kwargs: object) -> SmokeCheckResult:
            """Simulate fixture teardown/reset racing with lifecycle terminalization."""
            run_store.reset()
            return fake_result

        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=reset_store_then_return_result,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert run_store.get_run(record.run_id) is None
        assert auth_session_store.for_run(record.run_id) == []

    def test_completed_run_writes_configured_artifacts(self, tmp_path: Path) -> None:
        """Successful API/browser lifecycle runs persist configured artifacts.

        Args:
            tmp_path: Pytest temporary directory used for result and log files.
        """
        from datetime import datetime

        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        result_path = tmp_path / "out" / "result.json"
        log_path = tmp_path / "out" / "execution-log.ndjson"
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=result_path,
            execution_log_path=log_path,
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            return_value=fake_result,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "completed"
        assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "passed"
        assert log_path.exists()

    def test_failed_run_also_discards_awaiting_auth_sessions(self) -> None:
        """Sessions are discarded even when the run raises internally."""
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig

        record = run_store.create_run()
        auth_session_store.register(record.run_id)
        assert len(auth_session_store.for_run(record.run_id)) == 1

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=RuntimeError("boom"),
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(record.run_id) == []
        assert run_store.get_run(record.run_id).status == "failed"  # type: ignore[union-attr]

    def test_other_runs_auth_sessions_are_not_discarded(self) -> None:
        """The hook is run-scoped: sibling runs' sessions are untouched."""
        from datetime import datetime
        from pathlib import Path

        from conformance.api.auth_session_store import auth_session_store
        from conformance.api.run_lifecycle import _execute_run
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        finishing = run_store.create_run()
        other_run_id = "other-run-id"
        auth_session_store.register(finishing.run_id)
        auth_session_store.register(other_run_id)

        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            return_value=fake_result,
        ):
            _execute_run(finishing.run_id, config, manifest=None, plan=None)

        assert auth_session_store.for_run(finishing.run_id) == []
        assert len(auth_session_store.for_run(other_run_id)) == 1

    def test_browser_psu_prompt_flag_wraps_execution_logger(self) -> None:
        """Browser-launched runs mirror raw PSU URLs into transient run state."""
        from datetime import datetime
        from pathlib import Path

        from conformance.api.run_lifecycle import _execute_run
        from conformance.execution_log import ExecutionLogger
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        raw_url = "https://auth.example.com/authorize?client_id=client-123&request=raw-jws-value&state=browser-state"
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )

        def emit_browser_action(
            config: ModelBankConfig,
            *,
            execution_logger: ExecutionLogger,
        ) -> SmokeCheckResult:
            """Assert the lifecycle provided the browser logger and emit a PSU URL.

            Args:
                config: Runtime model-bank configuration passed to the smoke check.
                execution_logger: Logger supplied by the lifecycle.

            Returns:
                The fake successful smoke-check result.
            """
            assert isinstance(execution_logger, BrowserParticipantActionLogger)
            execution_logger.emit("psu-authorization-url", step_id="psu", payload={"url": raw_url})
            return fake_result

        with patch(
            "conformance.api.run_lifecycle.run_model_bank_smoke_check",
            side_effect=emit_browser_action,
        ):
            _execute_run(record.run_id, config, manifest=None, plan=None, browser_psu_prompts=True)

        updated = run_store.get_run(record.run_id)
        assert updated is not None
        assert updated.status == "completed"
        log_bytes = run_store.get_run_log_bytes(record.run_id)
        assert log_bytes is not None
        assert raw_url.encode("utf-8") not in log_bytes

    def test_manifest_run_passes_runtime_config_to_executor(self) -> None:
        """Manifest runs receive safe config placeholder values from the lifecycle."""
        from datetime import datetime
        from pathlib import Path

        import httpx

        from conformance.api.run_lifecycle import _execute_run
        from conformance.approved_releases import ApprovedReleasePolicy
        from conformance.manifest import parse_manifest
        from conformance.model_bank_config import ModelBankConfig
        from conformance.results import SmokeCheckResult

        record = run_store.create_run()
        approved_release_policy = ApprovedReleasePolicy(
            schema_version=APPROVED_RELEASE_POLICY_SCHEMA_VERSION,
            approved_tool_versions=("1.2.3",),
        )
        config = ModelBankConfig(
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("results.json"),
            approved_release_policy=approved_release_policy,
        )
        manifest = parse_manifest(
            {
                "schemaVersion": "v1",
                "name": "runtime config",
                "steps": [
                    {
                        "id": "config-driven",
                        "name": "Config-driven request",
                        "request": {"method": "GET", "url": "${config.discoveryUrl}"},
                        "assertions": [{"type": "http_status", "expected": 200}],
                    }
                ],
            }
        )
        fake_result = SmokeCheckResult(
            status="passed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            steps=(),
        )
        with (
            httpx.Client() as fake_client,
            patch("conformance.api.run_lifecycle.build_json_http_client", return_value=fake_client),
            patch("conformance.api.run_lifecycle.run_manifest", return_value=fake_result) as mock_run_manifest,
        ):
            _execute_run(record.run_id, config, manifest=manifest, plan=None)

        assert mock_run_manifest.call_args is not None
        runtime_config = mock_run_manifest.call_args.kwargs["runtime_config"]
        assert runtime_config.discovery_url == "https://example.com/.well-known/openid-configuration"
        assert mock_run_manifest.call_args.kwargs["approved_release_policy"] is approved_release_policy
