"""Tier 2 Ozone integration: provisional headless PSU authorisation flow.

This live-network test exercises the v1 ``psu-authorization`` headless path
against Ozone when, and only when, the environment explicitly opts into the
PRD open-investigation item via ``OZONE_HEADLESS_PSU_SUPPORTED=true``. The
test is skipped by default because Ozone support for fully headless consent
completion is not yet confirmed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from conformance.api.auth_session_store import AuthSessionStore
from conformance.context import RuntimeConfig
from conformance.execution_log import BufferedExecutionLogger, new_run_id
from conformance.executor import run_manifest
from conformance.json_types import JsonObject, JsonValue
from conformance.manifest import parse_manifest
from conformance.masking import MASKED_VALUE
from conformance.test_plan import TestPlan
from tests._ozone import requires_ozone

_REPO_ROOT = Path(__file__).resolve().parents[2]
"""Repository root used to load the bundled PSU example manifest."""

_PSU_EXAMPLE_MANIFEST_PATH = _REPO_ROOT / "config" / "manifest-v1-psu-authorization-example.json"
"""Bundled v1 PSU authorisation example manifest used by the live tier 2 test."""

_OZONE_TIER2 = requires_ozone(2)
"""Skip marker applied to every tier 2 test in this module."""

_INTEGRATION_HTTP_TIMEOUT_SECONDS = 30.0
"""Per-request HTTP timeout for tier 2 integration calls."""

_HEADLESS_STATE = "ozone-headless-psu-state-" + "x" * 32
"""Deterministic caller-supplied state long enough for the auth-session store."""


def _headless_psu_example_manifest() -> JsonObject:
    """Load the bundled PSU example and adapt it for the live Ozone tier 2 run.

    The example remains the source of truth for the discovery -> PSU
    authorisation -> token-exchange flow. Runtime config supplies discovery
    and OAuth values; this helper only flips the PSU step from manual to
    headless so the integration tier can run unattended.

    Returns:
        A v1 manifest JSON object suitable for ``parse_manifest``.
    """
    raw_manifest = cast(
        "JsonObject",
        json.loads(_PSU_EXAMPLE_MANIFEST_PATH.read_text(encoding="utf-8")),
    )
    manifest = copy.deepcopy(raw_manifest)
    steps = cast("list[JsonValue]", manifest["steps"])

    psu_step = cast("JsonObject", steps[1])
    psu_step["mode"] = "headless"
    psu_step["state"] = _HEADLESS_STATE
    return manifest


def _read_ndjson(path: Path) -> list[JsonObject]:
    """Read an NDJSON execution log into JSON objects.

    Args:
        path: Execution-log path produced by ``BufferedExecutionLogger``.

    Returns:
        Parsed JSON objects in file order.
    """
    return [cast("JsonObject", json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.ozone
@_OZONE_TIER2
def test_ozone_tier2_headless_psu_authorisation_masks_token_exchange_code(
    ozone_discovery_url: str,
    ozone_client_id: str,
    ozone_redirect_uri: str,
    tmp_path: Path,
) -> None:
    """Run the bundled PSU example in headless mode and verify log masking."""
    manifest = parse_manifest(_headless_psu_example_manifest())
    plan = TestPlan.default_plan_from_manifest(manifest)
    run_id = new_run_id()
    execution_logger = BufferedExecutionLogger(run_id=run_id, developer_mode=False)
    runtime_config = RuntimeConfig(
        discovery_url=ozone_discovery_url,
        environment="ozone-tier2",
        oauth_client_id=ozone_client_id,
        oauth_redirect_uri=ozone_redirect_uri,
    )

    with httpx.Client(timeout=_INTEGRATION_HTTP_TIMEOUT_SECONDS) as client:
        result = run_manifest(
            manifest,
            environment="ozone-tier2",
            client=client,
            execution_logger=execution_logger,
            plan=plan,
            run_id=run_id,
            auth_session_store=AuthSessionStore(),
            runtime_config=runtime_config,
        )

    log_path = tmp_path / "execution-log.ndjson"
    execution_logger.flush_to_path(log_path)
    events = _read_ndjson(log_path)

    assert result.status == "passed", result.to_json_object()
    assert [step.name for step in result.steps] == ["discovery", "psu-authorization", "token-exchange"]
    assert any(event.get("type") == "psu-authorization-redirect-received" for event in events)

    token_request_events = [
        event for event in events if event.get("type") == "request-sent" and event.get("stepId") == "token-exchange"
    ]
    assert len(token_request_events) == 1
    token_request_payload = cast("JsonObject", token_request_events[0]["payload"])
    token_request_form = cast("JsonObject", token_request_payload["form"])
    assert token_request_form["code"] == MASKED_VALUE
