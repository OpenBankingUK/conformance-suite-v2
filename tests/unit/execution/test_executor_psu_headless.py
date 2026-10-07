"""PSU authorisation in headless mode: signed request objects and redirect handling."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from joserfc import jwk, jwt

from conformance.api.auth_session_store import AuthSessionStore
from conformance.context import ExecutionContext, RequestRecord, ResponseRecord, record_step
from conformance.credentials import credential_bytes
from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import _execute_v1_psu_step
from conformance.manifest import (
    GeneratedRequestObject,
)
from tests.support.executor_psu import FakeClock, psu_headless_step
from tests.support.executor_signing import executor_signing_config

pytestmark = pytest.mark.unit


def test_psu_headless_step_uses_signed_request_object_for_authorization_redirect(tmp_path: Path) -> None:
    """Headless PSU mode sends a generated JAR request and masks persisted evidence.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-psu-signed-headless", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state=state, request_object=GeneratedRequestObject(source="fapi-signing")),
            context=ExecutionContext(),
            client=client,
            run_id="run-psu-signed-headless",
            auth_session_store=AuthSessionStore(),
            execution_logger=execution_logger,
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert result.url is not None
    assert "request=***" in result.url
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "headless-code"
    assert len(observed_urls) == 1
    assert "request=ey" in observed_urls[0]
    assert "request=***" not in observed_urls[0]

    psu_url_events = [event for event in execution_logger.events() if event.type == "psu-authorization-url"]
    assert len(psu_url_events) == 1
    assert psu_url_events[0].payload["request_object"] == "***"
    assert "request=***" in cast(str, psu_url_events[0].payload["url"])


def test_psu_headless_step_resolves_openbanking_intent_id_into_generated_request_object(tmp_path: Path) -> None:
    """Generated PSU request objects embed a resolved Open Banking consent id.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []
    signing_config = executor_signing_config(tmp_path)
    context = record_step(
        ExecutionContext(),
        "account-access-consent",
        RequestRecord(method="POST", url="https://rs.example.com/account-access-consents"),
        ResponseRecord(status_code=201, body={"Data": {"ConsentId": "consent-456"}}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound PSU authorisation URL for later JWT inspection.

        Args:
            request: Browser-like authorisation redirect emitted by the executor.

        Returns:
            Redirect response completing the headless PSU flow.
        """
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, _ = _execute_v1_psu_step(
            psu_headless_step(
                state=state,
                request_object=GeneratedRequestObject(
                    source="fapi-signing",
                    openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
                ),
            ),
            context=context,
            client=client,
            run_id="run-psu-signed-intent-headless",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-psu-signed-intent-headless", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
            fapi_signing_config=signing_config,
        )

    request_params = dict(parse_qsl(urlsplit(observed_urls[0]).query))
    public_key = jwk.import_key(
        credential_bytes(signing_config.signing_certificate, label="FAPI signing certificate"), key_type="RSA"
    )
    decoded_request_object = jwt.decode(request_params["request"], public_key, algorithms=["PS256"])
    claims = decoded_request_object.claims

    assert result.status == "passed"
    assert len(observed_urls) == 1
    assert isinstance(claims, dict)
    assert claims["claims"] == {
        "id_token": {
            "openbanking_intent_id": {
                "essential": True,
                "value": "consent-456",
            }
        }
    }


def test_psu_headless_step_captures_code_from_redirect() -> None:
    """Headless mode parses a 3xx Location and records code for placeholders."""
    state = "h" * 32
    store = AuthSessionStore()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=headless-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-headless", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless",
            auth_session_store=store,
            execution_logger=execution_logger,
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "headless-code"
    assert len(requested_urls) == 1
    requested_url = urlsplit(requested_urls[0])
    assert requested_url.scheme == "https"
    assert requested_url.netloc == "auth.example.com"
    assert requested_url.path == "/authorize"
    query = dict(parse_qsl(requested_url.query, keep_blank_values=True))
    assert query["existing"] == "1"
    assert query["client_id"] == "client-123"
    assert query["redirect_uri"] == "https://conformance.example.com/callback"
    assert query["response_type"] == "code id_token"
    assert query["scope"] == "openid accounts"
    assert query["state"] == state
    assert len(query["nonce"]) >= 32
    redirect_events = [
        event for event in execution_logger.events() if event.type == "psu-authorization-redirect-received"
    ]
    assert len(redirect_events) == 1
    assert redirect_events[0].payload == {"state": state, "status": 302}


def test_psu_headless_step_records_authorization_error_redirect() -> None:
    """Headless mode converts an ASPSP error redirect into a failed step."""
    state = "e" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            303,
            headers={
                "Location": (
                    "https://conformance.example.com/callback"
                    f"?state={state}&error=access_denied&error_description=PSU+declined"
                )
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-error",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-error", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.details["error"] == "access_denied"
    assert result.details["error_description"] == "PSU declined"
    assert context.steps["psu"].response is None


def test_psu_headless_step_fails_when_redirect_location_missing() -> None:
    """Headless mode fails cleanly when a 3xx response omits Location."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(302))) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state="l" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-missing-location",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-missing-location", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 302
    assert "Location" in result.message
    assert context.steps["psu"].response is None


def test_psu_headless_step_fails_on_mismatched_redirect_target() -> None:
    """Headless mode rejects redirects to any host/path other than redirectUri."""
    state = "x" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://evil.example.com/callback?state={state}&code=bad-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-mismatch",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-mismatch", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    rendered = json.dumps(result.to_json_object())
    assert result.status == "failed"
    assert "redirect target" in result.message
    assert "evil.example.com" not in rendered
    assert context.steps["psu"].response is None


def test_psu_headless_step_accepts_redirect_with_explicit_default_https_port() -> None:
    """Headless redirect matching treats omitted HTTPS port and ``:443`` as equivalent."""
    state = "p" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com:443/callback?state={state}&code=headless-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-default-port",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-default-port", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None


def test_psu_headless_step_fails_when_authorization_endpoint_returns_ok() -> None:
    """Headless mode fails on 200 OK rather than attempting consent automation."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))) as client:
        result, context = _execute_v1_psu_step(
            psu_headless_step(state="o" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-ok",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-headless-ok", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 200
    assert "did not return a redirect" in result.message
    assert context.steps["psu"].response is None


def _run_headless(
    handler: Callable[[httpx.Request], httpx.Response], *, state: str
) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
    """Run one headless PSU step and return its result JSON and log events."""
    execution_logger = BufferedExecutionLogger(run_id="run-headless-evidence", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _context = _execute_v1_psu_step(
            psu_headless_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-headless-evidence",
            auth_session_store=AuthSessionStore(),
            execution_logger=execution_logger,
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )
    events = [(event.type, dict(event.payload)) for event in execution_logger.events()]
    return result.to_json_object(), events


def test_psu_headless_login_page_is_logged_and_diagnosed() -> None:
    """An HTML login page response is logged and its title recorded as evidence."""
    page = "<html><head><title> Ozone &amp; Bank\n Login </title></head><body>secret-form</body></html>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "session=abc"},
            text=page,
        )

    result, events = _run_headless(handler, state="t" * 32)

    event_types = [event_type for event_type, _payload in events]
    assert event_types.index("request-sent") < event_types.index("response-received")
    request_payload = dict(events)["request-sent"]
    response_payload = dict(events)["response-received"]
    assert request_payload["method"] == "GET"
    assert str(request_payload["url"]).startswith("https://auth.example.com/authorize?")
    assert response_payload["statusCode"] == 200
    assert response_payload["contentType"] == "text/html; charset=utf-8"
    details = cast("dict[str, dict[str, object]]", result["details"])
    response = details["response"]
    assert response["htmlTitle"] == "Ozone & Bank Login"
    assert "secret-form" not in json.dumps(result)
    assert "abc" not in json.dumps(response["headers"])


def test_psu_headless_json_error_body_is_recorded_masked() -> None:
    """A JSON error from the authorisation endpoint is kept, with sensitive keys masked."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_request", "id_token": "leak"})

    result, events = _run_headless(handler, state="j" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert details["response"]["body"] == {"error": "invalid_request", "id_token": "***"}
    assert ("response-received", {"statusCode": 400, "url": result["url"], "contentType": "application/json"}) in events


def test_psu_headless_success_attaches_masked_redirect_evidence() -> None:
    """A successful headless redirect keeps evidence but masks the authorisation code."""
    state = "s" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=secret-code"},
        )

    result, _events = _run_headless(handler, state=state)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert result["status"] == "passed"
    assert details["response"]["redirectsToRedirectUri"] is True
    assert "code=***" in str(details["response"]["location"])
    assert "secret-code" not in json.dumps(result)


def test_psu_headless_off_target_redirect_is_flagged_without_echoing_target() -> None:
    """A redirect elsewhere (for example a login page) is flagged but its target is not recorded."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://login.aspsp.example/start?x=1"})

    result, _events = _run_headless(handler, state="o" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert details["response"]["redirectsToRedirectUri"] is False
    assert "location" not in details["response"]
    assert "login.aspsp.example" not in json.dumps(result)


def test_psu_headless_step_reads_hybrid_flow_fragment_response() -> None:
    """OIDC hybrid-flow redirects return ``code``/``id_token`` in the fragment; both are read and masked."""
    state = "f" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={
                "Location": f"https://conformance.example.com/callback#code=frag-code&id_token=frag-idt&state={state}"
            },
        )

    result, _events = _run_headless(handler, state=state)

    details = cast("dict[str, dict[str, object]]", result["details"])
    rendered = json.dumps(result)
    assert result["status"] == "passed"
    assert "frag-code" not in rendered
    assert "frag-idt" not in rendered
    assert "#code=***&id_token=***" in str(details["response"]["location"])
