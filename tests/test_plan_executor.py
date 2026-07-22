"""Unit tests for :mod:`conformance.plan_executor`.

Covers the singleton registry builder, catalogue-drift detection, RW suite
resolution from a RunPlanV2, and the DCR result JSON serialiser.  DCR run
execution itself is exercised indirectly via mocked ``DcrRunner`` output; the
underlying network calls are mocked out at the runner boundary in dedicated
DCR runner tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from conformance.execution_log import BufferedExecutionLogger
from conformance.masking import MASKED_VALUE
from conformance.model_bank_config import ModelBankConfig
from conformance.plan_executor import (
    _RW_SUITE_MAP,
    build_default_registry,
    check_catalogue_drift,
    compile_catalogue_graph_for_plan,
    dcr_run_result_to_json_object,
    emit_dcr_execution_log,
    execute_dcr_run,
    require_no_catalogue_drift,
    resolve_rw_suite_for_plan,
    run_plan_from_test_target,
    utc_now,
)
from conformance.plugins.dcr.client_state import DcrScenarioResult, DcrStepEvidence
from conformance.plugins.dcr.runner import DcrRunResult
from conformance.plugins.registry import PluginRegistry
from conformance.run_plan_v2 import EndpointSelection, RunPlanV2, RunPlanV2TargetCoordinates
from conformance.target_config import TestTargetConfig as TargetConfig


@pytest.mark.unit
class TestBuildDefaultRegistry:
    """Coverage for :func:`build_default_registry`."""

    def test_returns_singleton(self) -> None:
        """Repeated calls return the same registry instance."""
        first = build_default_registry()
        second = build_default_registry()
        assert first is second
        assert isinstance(first, PluginRegistry)


@pytest.mark.unit
class TestCheckCatalogueDrift:
    """Coverage for :func:`check_catalogue_drift`."""

    def _plan(self, *, catalogue_hash: str) -> RunPlanV2:
        """Return a RW RunPlanV2 with the supplied stored catalogue hash.

        Args:
            catalogue_hash: Value to store in ``target.catalogueHash``.

        Returns:
            A fully constructed RunPlanV2 targeting v4.0.1 AIS.
        """
        return RunPlanV2(
            schema_version="2",
            target=RunPlanV2TargetCoordinates(
                standard="obl",
                specification="read-write",
                security_profile="fapi1-advanced",
                specification_version="v4.0.1",
                catalogue_hash=catalogue_hash,
            ),
            resource_groups=("ais",),
            endpoint_selections=(),
        )

    def test_returns_none_when_hash_missing(self) -> None:
        """A missing/placeholder stored hash returns ``None``."""
        assert check_catalogue_drift(self._plan(catalogue_hash="sha256:unknown")) is None
        assert check_catalogue_drift(self._plan(catalogue_hash="")) is None

    def test_returns_none_when_hash_matches(self) -> None:
        """Matching stored and live hashes return ``None``."""
        from conformance.target_config import TestTargetConfig as _TargetConfig  # noqa: PLC0415

        registry = build_default_registry()
        target = _TargetConfig(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version="v4.0.1",
        )
        live_identity = registry.resolve(target).catalogue_identity(target)
        matching_plan = self._plan(catalogue_hash=live_identity.content_hash)
        assert check_catalogue_drift(matching_plan) is None

    def test_returns_warning_when_hash_differs(self) -> None:
        """A different stored hash surfaces a drift warning string."""
        plan = self._plan(catalogue_hash="sha256:definitely-not-the-live-hash")
        result = check_catalogue_drift(plan)
        assert result is not None
        assert "Catalogue drift detected" in result

    def test_require_no_catalogue_drift_rejects_mismatch(self) -> None:
        """Hard drift enforcement raises before a stale saved test plan can launch."""
        plan = self._plan(catalogue_hash="sha256:definitely-not-the-live-hash")
        with pytest.raises(ValueError, match="Catalogue drift detected"):
            require_no_catalogue_drift(plan)

    def test_returns_none_when_plugin_unknown(self) -> None:
        """Unknown target coordinates return ``None`` without raising."""
        plan = RunPlanV2(
            schema_version="2",
            target=RunPlanV2TargetCoordinates(
                standard="obl",
                specification="read-write",
                security_profile="fapi1-advanced",
                specification_version="v9.9.9",
                catalogue_hash="sha256:mismatch",
            ),
            resource_groups=("ais",),
            endpoint_selections=(),
        )
        assert check_catalogue_drift(plan) is None


@pytest.mark.unit
class TestRunPlanFromTestTarget:
    """Coverage for shared ``testTarget`` to RunPlanV2 derivation."""

    def test_uses_live_catalogue_hash_and_canonical_version(self) -> None:
        """A config target is normalised through plugin catalogue identity."""
        target = TargetConfig(
            standard="obl",
            specification="read-write",
            security_profile="fapi1-advanced",
            specification_version="v4.0",
            resource_groups=("ais",),
        )

        plan = run_plan_from_test_target(target)

        assert plan.target.specification_version == "v4.0.0"
        assert plan.target.catalogue_hash.startswith("sha256:")
        assert plan.target.catalogue_hash != "sha256:unknown"
        assert plan.resource_groups == ("ais",)
        assert check_catalogue_drift(plan) is None


@pytest.mark.unit
class TestResolveRwSuiteForPlan:
    """Coverage for :func:`resolve_rw_suite_for_plan`."""

    def _plan(self, *, version: str, groups: tuple[str, ...]) -> RunPlanV2:
        """Build a RW RunPlanV2 for the given version and resource groups.

        Args:
            version: Specification version (e.g. ``"v4.0.1"``).
            groups: Tuple of resource-group ids.

        Returns:
            A validated RunPlanV2.
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
            resource_groups=groups,
            endpoint_selections=(),
        )

    def test_resolves_ais_v4_0_1(self) -> None:
        """A v4.0.1 AIS plan resolves to the ais-certification-baseline suite."""
        manifest, metadata = resolve_rw_suite_for_plan(self._plan(version="v4.0.1", groups=("ais",)))
        assert manifest is not None
        assert metadata.suite == "ais-certification-baseline"

    def test_rejects_empty_resource_groups(self) -> None:
        """An empty resource-groups tuple raises ValueError."""
        plan = self._plan(version="v4.0.1", groups=())
        with pytest.raises(ValueError, match="at least one resource group"):
            resolve_rw_suite_for_plan(plan)

    def test_rejects_multiple_resource_groups(self) -> None:
        """Multi-resource-group plans raise ValueError instructing per-run submission."""
        plan = self._plan(version="v4.0.1", groups=("ais", "pis"))
        with pytest.raises(ValueError, match="one resource group per run"):
            resolve_rw_suite_for_plan(plan)

    def test_rejects_unmapped_coordinates(self) -> None:
        """Version/resource-group combinations not in the map raise ValueError."""
        plan = self._plan(version="v9.9.9", groups=("ais",))
        with pytest.raises(ValueError, match="No bundled suite available"):
            resolve_rw_suite_for_plan(plan)

    def test_map_is_populated(self) -> None:
        """The suite map contains v4.0.1 AIS/PIS entries used by consumers."""
        assert ("v4.0.1", "ais") in _RW_SUITE_MAP
        assert ("v4.0.1", "pis") in _RW_SUITE_MAP


@pytest.mark.unit
class TestExecuteDcrRun:
    """Coverage for :func:`execute_dcr_run`."""

    def _plan(self, *, selections: tuple[EndpointSelection, ...] = ()) -> RunPlanV2:
        """Build a minimal DCR RunPlanV2.

        Args:
            selections: Optional endpoint selections to include on the plan.

        Returns:
            A validated DCR RunPlanV2.
        """
        return RunPlanV2(
            schema_version="2",
            target=RunPlanV2TargetCoordinates(
                standard="obl",
                specification="dynamic-client-registration",
                security_profile="fapi1-advanced",
                specification_version="3.3",
                catalogue_hash="sha256:unknown",
            ),
            resource_groups=(),
            endpoint_selections=selections,
        )

    def _selection(self, endpoint_id: str, operation: str) -> EndpointSelection:
        """Build a DCR endpoint selection.

        Args:
            endpoint_id: Catalogue-native DCR endpoint identifier.
            operation: HTTP operation selected for the endpoint.

        Returns:
            EndpointSelection for a DCR plan.
        """
        return EndpointSelection(endpoint_id=endpoint_id, operation=operation, selected=True, field_values={})

    def _config(self) -> ModelBankConfig:
        """Build a DCR model-bank config fixture.

        Returns:
            A ModelBankConfig with placeholder DCR paths.
        """
        from conformance.dcr.credentials import DcrCredentialPaths
        from conformance.dcr.transport import DcrTransportConfig
        from conformance.model_bank_config import DcrConfig

        root = Path("dcr-fixture")
        paths = DcrCredentialPaths(
            credential_path_root=root,
            ssa_path=root / "ssa.jwt",
            signing_private_key_path=root / "signing.key",
            signing_certificate_path=root / "signing.crt",
            transport_certificate_path=root / "transport.crt",
            transport_private_key_path=root / "transport.key",
        )
        return ModelBankConfig(
            environment="test",
            discovery_url="https://issuer.example.com",
            result_output_path=Path("out/results.json"),
            dcr=DcrConfig(
                credential_paths=paths,
                transport=DcrTransportConfig(
                    token_endpoint_auth_method="tls_client_auth",  # noqa: S106 - auth-method enum fixture, not a secret
                ),
            ),
        )

    def test_raises_when_config_missing_dcr_section(self) -> None:
        """Executing without a ``dcr`` config section raises ConfigError."""
        from pathlib import Path

        from conformance.model_bank_config import ConfigError, ModelBankConfig

        config = ModelBankConfig(
            environment="test",
            discovery_url="https://example.com/.well-known/openid-configuration",
            result_output_path=Path("out/results.json"),
        )
        with pytest.raises(ConfigError, match="'dcr' section is required"):
            execute_dcr_run(self._plan(), config)

    @pytest.mark.parametrize(
        ("endpoint_id", "operation", "expected_flags"),
        [
            ("dcr.register.get", "GET", {"advertise_get": True, "advertise_put": False, "advertise_delete": False}),
            ("dcr.register.put", "PUT", {"advertise_get": False, "advertise_put": True, "advertise_delete": False}),
            (
                "dcr.register.delete",
                "DELETE",
                {"advertise_get": False, "advertise_put": False, "advertise_delete": True},
            ),
        ],
    )
    def test_dcr_catalogue_endpoint_ids_drive_get_put_delete_selection(
        self,
        endpoint_id: str,
        operation: str,
        expected_flags: dict[str, bool],
    ) -> None:
        """DCR GET, PUT, and DELETE selections use catalogue-native endpoint IDs."""
        from conformance.dcr.credentials import DcrCredentials

        credentials = DcrCredentials(
            ssa_jwt=b"ssa",
            signing_private_key_pem=b"signing-key",
            signing_certificate_pem=b"signing-cert",
            transport_certificate_pem=b"transport-cert",
            transport_private_key_pem=b"transport-key",
        )
        run_result = DcrRunResult(
            discovery=None,
            scenario_results=[],
            cleanup_attempted=False,
            cleanup_succeeded=False,
            cleanup_detail="not run",
        )

        with (
            patch("conformance.plan_executor.load_dcr_credentials", return_value=credentials),
            patch("conformance.plan_executor.DcrRunner") as runner_cls,
        ):
            runner_cls.return_value.run.return_value = run_result

            execute_dcr_run(self._plan(selections=(self._selection(endpoint_id, operation),)), self._config())

        kwargs = runner_cls.call_args.kwargs
        assert kwargs["advertise_get"] is expected_flags["advertise_get"]
        assert kwargs["advertise_put"] is expected_flags["advertise_put"]
        assert kwargs["advertise_delete"] is expected_flags["advertise_delete"]


@pytest.mark.unit
class TestDcrRunResultToJsonObject:
    """Coverage for :func:`dcr_run_result_to_json_object`."""

    def _plan(self) -> RunPlanV2:
        """Return a minimal DCR RunPlanV2 for serialiser tests.

        Returns:
            A DCR RunPlanV2 with target coordinates set.
        """
        return RunPlanV2(
            schema_version="2",
            target=RunPlanV2TargetCoordinates(
                standard="obl",
                specification="dynamic-client-registration",
                security_profile="fapi1-advanced",
                specification_version="3.3",
                catalogue_hash="sha256:unknown",
            ),
            resource_groups=(),
            endpoint_selections=(),
        )

    def _scenario(self, *, outcome: str, evidence: DcrStepEvidence | None = None) -> DcrScenarioResult:
        """Return a synthetic DcrScenarioResult with the requested outcome.

        Args:
            outcome: One of ``passed``, ``failed``, ``skipped``.
            evidence: Optional attached scenario evidence.

        Returns:
            A DcrScenarioResult for use in DcrRunResult fixtures.
        """
        return DcrScenarioResult(
            scenario_id="baseline",
            outcome=outcome,  # type: ignore[arg-type]
            assertion_detail="detail",
            evidence=evidence,
        )

    def _run_result(self, *, results: tuple[DcrScenarioResult, ...]) -> DcrRunResult:
        """Build a synthetic DcrRunResult for serialiser tests.

        Args:
            results: Scenario results to embed.

        Returns:
            A DcrRunResult with a discovery placeholder.
        """
        from conformance.plugins.dcr.discovery import DcrDiscoveryResult

        discovery = DcrDiscoveryResult(
            issuer="https://issuer.example.com",
            registration_endpoint="https://issuer.example.com/register",
            token_endpoint="https://issuer.example.com/token",  # noqa: S106
            jwks_uri="https://issuer.example.com/jwks",
            token_endpoint_auth_methods_supported=["tls_client_auth"],
            selected_auth_method="tls_client_auth",
            response_types_supported=["code"],
            grant_types_supported=["authorization_code"],
            raw={"issuer": "https://issuer.example.com"},
        )
        return DcrRunResult(
            discovery=discovery,
            scenario_results=list(results),
            cleanup_attempted=False,
            cleanup_succeeded=False,
            cleanup_detail="not attempted",
        )

    def test_serialises_passed_run(self) -> None:
        """A run with only passed scenarios yields status=passed."""
        started = datetime(2026, 1, 1, tzinfo=UTC)
        finished = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
        body = dcr_run_result_to_json_object(
            self._run_result(results=(self._scenario(outcome="passed"),)),
            plan=self._plan(),
            environment="test-env",
            run_id="run-1",
            started_at=started,
            finished_at=finished,
        )
        assert body["status"] == "passed"
        summary = body["summary"]
        assert isinstance(summary, dict)
        assert summary["passed"] == 1
        assert summary["failed"] == 0

    def test_serialises_failed_run(self) -> None:
        """Any failed scenario yields status=failed."""
        body = dcr_run_result_to_json_object(
            self._run_result(
                results=(
                    self._scenario(outcome="passed"),
                    self._scenario(outcome="failed"),
                )
            ),
            plan=self._plan(),
            environment="test-env",
            run_id="run-2",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        assert body["status"] == "failed"

    def test_all_skipped_is_failed(self) -> None:
        """A run where every scenario was skipped is treated as failed."""
        body = dcr_run_result_to_json_object(
            self._run_result(results=(self._scenario(outcome="skipped"),)),
            plan=self._plan(),
            environment="test-env",
            run_id="run-3",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        assert body["status"] == "failed"

    def test_empty_scenarios_is_failed(self) -> None:
        """A run with no scenario results is treated as failed."""
        body = dcr_run_result_to_json_object(
            self._run_result(results=()),
            plan=self._plan(),
            environment="test-env",
            run_id="run-4",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        assert body["status"] == "failed"

    def test_includes_evidence_when_present(self) -> None:
        """Scenario evidence is included in the serialised scenario entry."""
        evidence = DcrStepEvidence(
            request_url="https://example.com/register",
            request_method="POST",
            request_content_type="application/jwt",
            request_headers_masked={"Authorization": "***"},
            response_status=201,
            response_headers_masked={"Content-Type": "application/json"},
            response_body_masked={"client_id": "abc"},
        )
        body = dcr_run_result_to_json_object(
            self._run_result(results=(self._scenario(outcome="passed", evidence=evidence),)),
            plan=self._plan(),
            environment="test-env",
            run_id="run-5",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        scenarios = body["scenarios"]
        assert isinstance(scenarios, list)
        first: Any = scenarios[0]
        assert "evidence" in first

    def test_includes_catalogue_readiness_report_when_compiled_run_supplied(self) -> None:
        """DCR result JSON includes endpoint-first readiness from catalogue policy."""
        compiled_run = compile_catalogue_graph_for_plan(self._plan())
        body = dcr_run_result_to_json_object(
            self._run_result(results=(self._scenario(outcome="passed"),)),
            plan=self._plan(),
            environment="test-env",
            run_id="run-6",
            started_at=utc_now(),
            finished_at=utc_now(),
            compiled_run=compiled_run,
        )

        readiness = body["readinessReport"]
        assert isinstance(readiness, dict)
        assert readiness["overallOutcome"] == "non-certifying"
        assert readiness["catalogueHash"] == compiled_run.catalogue_identity.content_hash
        dcr_status = readiness["dcrStatus"]
        assert isinstance(dcr_status, dict)
        assert dcr_status["certifying"] is False
        policy = readiness["readinessPolicy"]
        assert isinstance(policy, dict)
        assert policy["certificationStatus"] == "non-certifying"

    def test_emit_dcr_execution_log_masks_scenario_evidence(self) -> None:
        """DCR scenario evidence is emitted through the shared masked NDJSON logger."""
        evidence = DcrStepEvidence(
            request_url="https://example.com/token",
            request_method="POST",
            request_content_type="application/x-www-form-urlencoded",
            request_headers_masked={"Authorization": "Bearer live-token"},
            response_status=200,
            response_headers_masked={"Content-Type": "application/json"},
            response_body_masked={"access_token": "live-access-token", "token_type": "Bearer"},
        )
        logger = BufferedExecutionLogger(run_id="run-1", developer_mode=False)

        emit_dcr_execution_log(
            self._run_result(results=(self._scenario(outcome="passed", evidence=evidence),)),
            execution_logger=logger,
        )

        rendered = logger.to_ndjson_bytes().decode("utf-8")
        events = [json.loads(line) for line in rendered.splitlines()]
        step_event = next(event for event in events if event["type"] == "step-completed")
        response_body = step_event["payload"]["evidence"]["responseBody"]

        assert response_body["access_token"] == MASKED_VALUE
        assert "live-access-token" not in rendered
        assert "Bearer live-token" not in rendered


@pytest.mark.unit
class TestUtcNow:
    """Coverage for :func:`utc_now`."""

    def test_returns_utc_datetime(self) -> None:
        """``utc_now`` returns a timezone-aware UTC datetime."""
        now = utc_now()
        assert now.tzinfo is UTC
