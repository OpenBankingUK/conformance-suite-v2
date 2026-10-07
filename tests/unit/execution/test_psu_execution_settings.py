"""Plan-level PSU authorisation execution settings: parsing, wiring, and evidence.

Covers the canonical ``execution.psuAuthorization`` block that lets pipelines
choose manual or headless PSU authorisation and supply custom headers (headless
authorisation request only) and authorisation parameters (signed request-object
claims per FAPI 1 Advanced Part 2 §5.2.2, or query parameters when no request
object is generated).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from joserfc import jwk, jwt

from conformance.api.auth_session_store import AuthSessionStore
from conformance.catalogue import (
    CatalogueError,
    PlanDocumentV2,
    parse_test_plan_document,
    plan_document_to_json_object,
)
from conformance.cli_summary import MAX_LISTED_STEPS, render_run_summary
from conformance.context import ExecutionContext
from conformance.credentials import credential_bytes
from conformance.execution_log import BufferedExecutionLogger
from conformance.execution_settings import (
    DEFAULT_EXECUTION_SETTINGS,
    ExecutionSettingsError,
    PlanExecutionSettings,
    PsuAuthorizationSettings,
    parameter_query_value,
    parse_execution_settings,
)
from conformance.executor import (
    _apply_psu_execution_settings,
    _execute_v1_psu_step,
    compiled_plan_requires_psu_callback,
)
from conformance.manifest import GeneratedRequestObject, ManifestRequest, ManifestStep
from conformance.psu_authorization import build_authorization_url
from conformance.results import SmokeCheckResult
from conformance.signing_credentials import load_signing_credentials
from conformance.signing_service import FapiSigningService, JwtSigningError, RequestObjectSigningInput
from conformance.test_plan_validation import TestPlanValidationError as PlanValidationError
from conformance.test_plan_validation import prepare_test_plan_for_run, safe_test_plan_snapshot
from tests.support.executor_psu import FakeClock, psu_headless_step, psu_manual_step
from tests.support.executor_signing import executor_signing_config
from tests.support.run_config import RUN_READY_SECURITY_ENVIRONMENT

pytestmark = pytest.mark.unit

_STATE = "h" * 32
_REDIRECT = f"https://conformance.example.com/callback?state={_STATE}&code=headless-code"


def _plan(execution: object | None = None) -> dict[str, object]:
    """Build a minimal run-ready AIS canonical plan with an optional execution block."""
    plan: dict[str, object] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "3.1.11",
            "openApiDocumentUpdate": "Release-5",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {
            **RUN_READY_SECURITY_ENVIRONMENT,
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://resource.example.com",
        },
        "resourceGroups": [{"id": "AIS", "endpoints": [{"method": "GET", "path": "/open-banking/v3.1/aisp/accounts"}]}],
        "businessTestData": {},
        "metadata": {},
    }
    if execution is not None:
        plan["execution"] = execution
    return plan


# --- parsing -----------------------------------------------------------------


def test_missing_execution_block_defaults_to_manual() -> None:
    assert parse_execution_settings({}) == DEFAULT_EXECUTION_SETTINGS
    assert DEFAULT_EXECUTION_SETTINGS.psu_authorization.mode == "manual"
    assert DEFAULT_EXECUTION_SETTINGS.is_default


def test_headless_needs_no_extra_fields() -> None:
    settings = parse_execution_settings({"psuAuthorization": {"mode": "headless"}})

    assert settings.psu_authorization == PsuAuthorizationSettings(mode="headless")


def test_parses_user_defined_headers_and_parameters() -> None:
    settings = parse_execution_settings(
        {
            "psuAuthorization": {
                "mode": "headless",
                "headers": {"X-Sandbox-Auto-Approve": "true"},
                "parameters": {"headless": True, "psu_id": "user-1", "attempts": 2},
            }
        }
    )

    psu = settings.psu_authorization
    assert psu.headers == (("X-Sandbox-Auto-Approve", "true"),)
    assert psu.parameters == (("headless", True), ("psu_id", "user-1"), ("attempts", 2))
    assert settings.to_json_object() == {
        "psuAuthorization": {
            "mode": "headless",
            "headers": {"X-Sandbox-Auto-Approve": "true"},
            "parameters": {"headless": True, "psu_id": "user-1", "attempts": 2},
        }
    }


def test_manual_mode_allows_parameters() -> None:
    settings = parse_execution_settings({"psuAuthorization": {"parameters": {"prompt_hint": "x"}}})

    assert settings.psu_authorization.mode == "manual"
    assert settings.psu_authorization.parameters == (("prompt_hint", "x"),)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"psuAuthorization": {"mode": "manual", "headers": {"X-A": "1"}}}, "headers"),
        ({"psuAuthorization": {"mode": "auto"}}, "mode"),
        ({"psuAuthorization": {"mode": "headless", "headers": {"Host": "x"}}}, "cannot be overridden"),
        ({"psuAuthorization": {"mode": "headless", "headers": {"X A": "x"}}}, "X A"),
        ({"psuAuthorization": {"mode": "headless", "headers": {"X-A": "a\nb"}}}, "control"),
        ({"psuAuthorization": {"mode": "headless", "headers": {"X-A": 1}}}, "string"),
        ({"psuAuthorization": {"mode": "headless", "headers": {"X-A": "1", "x-a": "2"}}}, "uplicate"),
        ({"psuAuthorization": {"parameters": {"state": "x"}}}, "cannot be overridden"),
        ({"psuAuthorization": {"parameters": {"Request": "x"}}}, "cannot be overridden"),
        ({"psuAuthorization": {"parameters": {"bad name": "x"}}}, "bad name"),
        ({"psuAuthorization": {"parameters": {"a": None}}}, "a"),
        ({"psuAuthorization": {"parameters": {"a": {"nested": 1}}}}, "a"),
        ({"psuAuthorization": {"unknown": 1}}, "unknown"),
        ({"resourceRequests": {}}, "resourceRequests"),
        ({"psuAuthorization": {"parameters": {f"p{i}": "x" for i in range(33)}}}, "32"),
    ],
)
def test_rejects_invalid_execution_settings(raw: object, message: str) -> None:
    with pytest.raises(ExecutionSettingsError, match=message):
        parse_execution_settings(raw)


def test_parameter_query_value_serialises_scalars() -> None:
    assert parameter_query_value(True) == "true"
    assert parameter_query_value(False) == "false"
    assert parameter_query_value(3) == "3"
    assert parameter_query_value("x") == "x"


# --- canonical plan integration ----------------------------------------------


def test_prepare_test_plan_carries_execution_settings_into_compiled_plan() -> None:
    execution = {
        "psuAuthorization": {
            "mode": "headless",
            "headers": {"X-Sandbox-Auto-Approve": "true"},
            "parameters": {"headless": True},
        }
    }

    prepared = prepare_test_plan_for_run(_plan(execution), base_dir=Path.cwd())

    psu = prepared.compiled_plan.execution_settings.psu_authorization
    assert psu.mode == "headless"
    assert psu.headers == (("X-Sandbox-Auto-Approve", "true"),)
    assert psu.parameters == (("headless", True),)
    assert prepared.snapshot["execution"] == execution


def test_prepare_test_plan_rejects_invalid_execution_settings() -> None:
    with pytest.raises(PlanValidationError, match="headers"):
        prepare_test_plan_for_run(
            _plan({"psuAuthorization": {"mode": "manual", "headers": {"X-A": "1"}}}),
            base_dir=Path.cwd(),
        )


def test_default_plan_omits_execution_block() -> None:
    prepared = prepare_test_plan_for_run(_plan(), base_dir=Path.cwd())

    assert prepared.compiled_plan.execution_settings.is_default
    assert "execution" not in prepared.snapshot


def test_plan_document_round_trips_execution_block() -> None:
    execution = {"psuAuthorization": {"mode": "headless", "parameters": {"headless": True}}}
    document = parse_test_plan_document(_plan(execution))

    assert isinstance(document, PlanDocumentV2)
    assert plan_document_to_json_object(document)["execution"] == execution


def test_catalogue_parser_wraps_execution_errors() -> None:
    with pytest.raises(CatalogueError, match="execution"):
        parse_test_plan_document(_plan({"psuAuthorization": {"mode": "nope"}}))


def test_safe_snapshot_blanks_sensitive_header_values_only() -> None:
    plan = _plan(
        {
            "psuAuthorization": {
                "mode": "headless",
                "headers": {"Authorization": "Bearer sandbox", "X-Sandbox-Auto-Approve": "true"},
            }
        }
    )

    document = parse_test_plan_document(copy.deepcopy(plan))
    assert isinstance(document, PlanDocumentV2)
    snapshot = safe_test_plan_snapshot(document)

    headers = snapshot["execution"]["psuAuthorization"]["headers"]  # type: ignore[index, call-overload] - JSON test traversal.
    assert headers == {"Authorization": "", "X-Sandbox-Auto-Approve": "true"}


# --- executor wiring ---------------------------------------------------------


def test_apply_settings_sets_mode_headers_and_parameters_on_psu_steps_only() -> None:
    http_step = ManifestStep(
        id="http", name="HTTP", request=ManifestRequest(method="GET", url="https://x.example"), assertions=()
    )
    settings = PlanExecutionSettings(
        psu_authorization=PsuAuthorizationSettings(
            mode="headless", headers=(("X-A", "1"),), parameters=(("headless", True),)
        )
    )

    steps = _apply_psu_execution_settings((http_step, psu_manual_step()), settings)

    assert steps[0] is http_step
    psu = steps[1]
    assert getattr(psu, "mode", None) == "headless"
    assert getattr(psu, "custom_headers", None) == (("X-A", "1"),)
    assert getattr(psu, "custom_parameters", None) == (("headless", True),)


def test_apply_settings_never_attaches_headers_in_manual_mode() -> None:
    settings = PlanExecutionSettings(
        psu_authorization=PsuAuthorizationSettings(mode="manual", parameters=(("hint", "x"),))
    )

    (psu,) = _apply_psu_execution_settings((psu_manual_step(),), settings)

    assert getattr(psu, "mode", None) == "manual"
    assert getattr(psu, "custom_headers", None) == ()
    assert getattr(psu, "custom_parameters", None) == (("hint", "x"),)


def test_requires_psu_callback_only_for_manual_plans_with_psu_steps() -> None:
    prepared = prepare_test_plan_for_run(_plan(), base_dir=Path.cwd())
    headless = replace(
        prepared.compiled_plan,
        execution_settings=PlanExecutionSettings(psu_authorization=PsuAuthorizationSettings(mode="headless")),
    )

    assert compiled_plan_requires_psu_callback(prepared.compiled_plan) is True
    assert compiled_plan_requires_psu_callback(headless) is False


def test_headless_request_sends_custom_headers_and_records_names_only() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(302, headers={"Location": _REDIRECT})

    step = replace(
        psu_headless_step(state=_STATE),
        custom_headers=(("X-Sandbox-Auto-Approve", "secret-token"),),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = _execute_v1_psu_step(
            step,
            context=ExecutionContext(),
            client=client,
            run_id="run-headers",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headers", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "passed"
    assert observed[0].headers["X-Sandbox-Auto-Approve"] == "secret-token"
    assert "secret-token" not in json.dumps(result.to_json_object())


def test_headless_failure_evidence_masks_custom_header_values() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html>login</html>")

    step = replace(psu_headless_step(state=_STATE), custom_headers=(("X-Api-Token", "secret-token"),))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = _execute_v1_psu_step(
            step,
            context=ExecutionContext(),
            client=client,
            run_id="run-login",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-login", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    serialised = json.dumps(result.to_json_object())
    assert result.status == "failed"
    assert "login or consent page" in result.message
    assert "secret-token" not in serialised
    assert '"X-Api-Token": "***"' in serialised


def test_custom_parameters_become_query_parameters_without_request_object() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(str(request.url))
        return httpx.Response(302, headers={"Location": _REDIRECT})

    step = replace(psu_headless_step(state=_STATE), custom_parameters=(("headless", True), ("psu_id", "u1")))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _execute_v1_psu_step(
            step,
            context=ExecutionContext(),
            client=client,
            run_id="run-query",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-query", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    query = dict(parse_qsl(urlsplit(observed[0]).query))
    assert query["headless"] == "true"
    assert query["psu_id"] == "u1"


def test_custom_parameters_become_signed_claims_with_generated_request_object(tmp_path: Path) -> None:
    observed: list[str] = []
    signing_config = executor_signing_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(str(request.url))
        return httpx.Response(302, headers={"Location": _REDIRECT})

    step = replace(
        psu_headless_step(state=_STATE, request_object=GeneratedRequestObject(source="fapi-signing")),
        custom_parameters=(("headless", True), ("psu_id", "u1")),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = _execute_v1_psu_step(
            step,
            context=ExecutionContext(),
            client=client,
            run_id="run-claims",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-claims", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
            fapi_signing_config=signing_config,
        )

    query = dict(parse_qsl(urlsplit(observed[0]).query))
    public_key = jwk.import_key(
        credential_bytes(signing_config.signing_certificate, label="FAPI signing certificate"), key_type="RSA"
    )
    claims = jwt.decode(query["request"], public_key, algorithms=["PS256"]).claims
    assert result.status == "passed"
    assert "headless" not in query
    assert "psu_id" not in query
    assert claims["headless"] is True
    assert claims["psu_id"] == "u1"


def test_build_authorization_url_ignores_reserved_extra_parameters() -> None:
    url = build_authorization_url(
        endpoint="https://auth.example.com/authorize?headless=old",
        client_id="c",
        redirect_uri="https://r.example/cb",
        response_type="code",
        scope="openid",
        state="s",
        nonce="n",
        extra_query_parameters=(("state", "evil"), ("headless", "true")),
    )

    query = parse_qsl(urlsplit(url).query)
    assert ("state", "s") in query
    assert ("state", "evil") not in query
    assert [value for name, value in query if name == "headless"] == ["true"]


def test_signing_rejects_additional_claim_collisions(tmp_path: Path) -> None:
    signing_config = executor_signing_config(tmp_path)
    service = FapiSigningService(
        signing_config=signing_config, signing_credentials=load_signing_credentials(signing_config)
    )
    with pytest.raises(JwtSigningError, match="collides"):
        service.sign_request_object(
            RequestObjectSigningInput(
                issuer="i",
                audience="a",
                client_id="c",
                redirect_uri="https://r.example/cb",
                response_type="code",
                scope="openid",
                state="s" * 32,
                nonce="n",
                additional_claims=(("exp", 1),),
            )
        )


# --- result evidence and console summary --------------------------------------


def test_result_records_psu_mode_and_names_without_values() -> None:
    prepared = prepare_test_plan_for_run(
        _plan(
            {
                "psuAuthorization": {
                    "mode": "headless",
                    "headers": {"X-Sandbox-Auto-Approve": "secret-value"},
                    "parameters": {"psu_id": "secret-user"},
                }
            }
        ),
        base_dir=Path.cwd(),
    )
    now = datetime.now(UTC)
    result = SmokeCheckResult(
        status="passed", started_at=now, finished_at=now, steps=(), compiled_plan=prepared.compiled_plan
    ).to_json_object()

    assert result["execution"] == {
        "psuAuthorization": {
            "mode": "headless",
            "headerNames": ["X-Sandbox-Auto-Approve"],
            "parameterNames": ["psu_id"],
        }
    }
    assert "secret-value" not in json.dumps(result["execution"])


def _result(steps: list[dict[str, object]], *, status: str = "failed") -> dict[str, object]:
    """Build a minimal structured result JSON object for summary tests."""
    return {
        "status": status,
        "summary": {"total": len(steps), "passed": 0, "failed": len(steps), "warn": 0, "skipped": 0},
        "certificationEligibility": {"eligible": False, "reasons": ["Approved-release policy was not supplied"]},
        "catalogue": {"standard": "open-banking-uk", "version": "3.1.11", "api": "read-write"},
        "execution": {"psuAuthorization": {"mode": "headless", "headerNames": ["X-A"], "parameterNames": ["p"]}},
        "steps": steps,
    }


def test_summary_shows_status_counts_mode_eligibility_and_failures() -> None:
    text = render_run_summary(
        _result([{"name": "accounts", "status": "failed", "statusCode": 403, "message": "Expected 200"}]),  # type: ignore[arg-type] - JSON test fixture.
        run_label="plan.json",
        result_path=Path("out/result.json"),
        execution_log_path=Path("out/log.ndjson"),
    )

    assert text.startswith("Conformance run FAILED: plan.json\n")
    assert "Catalogue: open-banking-uk 3.1.11 read-write" in text
    assert "Steps: 1 total, 0 passed, 1 failed, 0 warn, 0 skipped" in text
    assert "PSU authorisation: headless (headers: X-A; parameters: p)" in text
    assert "Certification eligible: no" in text
    assert "- Approved-release policy was not supplied" in text
    assert "x accounts [HTTP 403]: Expected 200" in text
    assert "Result file:   out/result.json" in text
    assert "python -m conformance.result_gate out/result.json" in text


def test_summary_truncates_long_failure_lists() -> None:
    steps = [{"name": f"s{i}", "status": "failed", "message": "m"} for i in range(MAX_LISTED_STEPS + 3)]

    text = render_run_summary(
        _result(steps),  # type: ignore[arg-type] - JSON test fixture.
        run_label="plan.json",
        result_path=Path("r.json"),
        execution_log_path=Path("l.ndjson"),
    )

    assert "... and 3 more (see result file)" in text
