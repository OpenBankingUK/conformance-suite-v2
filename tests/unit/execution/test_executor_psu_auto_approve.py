"""PSU authorisation in auto-approve mode: signed request objects and redirect handling."""

import json
from collections.abc import Callable
from dataclasses import replace
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
from conformance.model_bank_config import TokenEndpointClientAuthMode
from tests.support.executor_psu import FakeClock, psu_auto_approve_step
from tests.support.executor_signing import executor_signing_config

pytestmark = pytest.mark.unit


def test_psu_auto_approve_step_uses_signed_request_object_for_authorization_redirect(tmp_path: Path) -> None:
    """Auto-approve PSU mode sends a generated JAR request and masks persisted evidence.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=auto-approve-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-psu-signed-auto-approve", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state=state, request_object=GeneratedRequestObject(source="fapi-signing")),
            context=ExecutionContext(),
            client=client,
            run_id="run-psu-signed-auto-approve",
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
    assert context.steps["psu"].response.body["code"] == "auto-approve-code"
    assert len(observed_urls) == 1
    assert "request=ey" in observed_urls[0]
    assert "request=***" not in observed_urls[0]

    psu_url_events = [event for event in execution_logger.events() if event.type == "psu-authorization-url"]
    assert len(psu_url_events) == 1
    assert psu_url_events[0].payload["request_object"] == "***"
    assert "request=***" in cast(str, psu_url_events[0].payload["url"])


@pytest.mark.parametrize("auth_method", ["private_key_jwt", "tls_client_auth"])
def test_psu_auto_approve_step_resolves_openbanking_intent_id_into_generated_request_object(
    tmp_path: Path,
    auth_method: TokenEndpointClientAuthMode,
) -> None:
    """Generated PSU request objects embed a resolved Open Banking consent id.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
    """
    state = "h" * 32
    observed_urls: list[str] = []
    signing_config = replace(
        executor_signing_config(tmp_path),
        token_endpoint_auth_method=auth_method,
        client_assertion_subject="" if auth_method == "tls_client_auth" else "client-123",
    )
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
            Redirect response completing the auto-approve PSU flow.
        """
        observed_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=auto-approve-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, _ = _execute_v1_psu_step(
            psu_auto_approve_step(
                state=state,
                request_object=GeneratedRequestObject(
                    source="fapi-signing",
                    openbanking_intent_id="${steps.account-access-consent.response.body.Data.ConsentId}",
                ),
            ),
            context=context,
            client=client,
            run_id="run-psu-signed-intent-auto-approve",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-psu-signed-intent-auto-approve", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
            fapi_signing_config=signing_config,
        )

    request_params = dict(parse_qsl(urlsplit(observed_urls[0]).query))
    assert signing_config.signing_certificate is not None
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


def test_psu_auto_approve_step_captures_code_from_redirect() -> None:
    """Auto-approve mode parses a 3xx Location and records code for placeholders."""
    state = "h" * 32
    store = AuthSessionStore()
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=auto-approve-code"},
        )

    execution_logger = BufferedExecutionLogger(run_id="run-auto-approve", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve",
            auth_session_store=store,
            execution_logger=execution_logger,
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None
    assert context.steps["psu"].response.body["code"] == "auto-approve-code"
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


def test_psu_auto_approve_step_records_authorization_error_redirect() -> None:
    """Auto-approve mode converts an ASPSP error redirect into a failed step."""
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
            psu_auto_approve_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-error",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-auto-approve-error", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.details["error"] == "access_denied"
    assert result.details["error_description"] == "PSU declined"
    assert context.steps["psu"].response is None


def test_psu_auto_approve_step_fails_when_redirect_location_missing() -> None:
    """Auto-approve mode fails cleanly when a 3xx response omits Location."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(302))) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state="l" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-missing-location",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-auto-approve-missing-location", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 302
    assert "Location" in result.message
    assert context.steps["psu"].response is None


def test_psu_auto_approve_step_fails_on_mismatched_redirect_target() -> None:
    """Auto-approve mode rejects redirects to any host/path other than redirectUri."""
    state = "x" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://evil.example.com/callback?state={state}&code=bad-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-mismatch",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-auto-approve-mismatch", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    rendered = json.dumps(result.to_json_object())
    assert result.status == "failed"
    assert "not the configured redirectUri" in result.message
    assert "https://evil.example.com/callback" in result.message
    assert "bad-code" not in rendered
    assert context.steps["psu"].response is None


def test_psu_auto_approve_step_accepts_redirect_with_explicit_default_https_port() -> None:
    """Auto-approve redirect matching treats omitted HTTPS port and ``:443`` as equivalent."""
    state = "p" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com:443/callback?state={state}&code=auto-approve-code"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-default-port",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-auto-approve-default-port", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "passed"
    assert context.steps["psu"].response is not None


def test_psu_auto_approve_step_fails_when_authorization_endpoint_returns_ok() -> None:
    """Auto-approve mode fails on 200 OK rather than attempting consent automation."""
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))) as client:
        result, context = _execute_v1_psu_step(
            psu_auto_approve_step(state="o" * 32),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-ok",
            auth_session_store=AuthSessionStore(),
            execution_logger=BufferedExecutionLogger(run_id="run-auto-approve-ok", developer_mode=False),
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )

    assert result.status == "failed"
    assert result.status_code == 200
    assert "did not complete with a redirect (got HTTP 200)" in result.message
    assert context.steps["psu"].response is None


def _run_auto_approve(
    handler: Callable[[httpx.Request], httpx.Response], *, state: str
) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
    """Run one auto-approve PSU step and return its result JSON and log events."""
    execution_logger = BufferedExecutionLogger(run_id="run-auto-approve-evidence", developer_mode=False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _context = _execute_v1_psu_step(
            psu_auto_approve_step(state=state),
            context=ExecutionContext(),
            client=client,
            run_id="run-auto-approve-evidence",
            auth_session_store=AuthSessionStore(),
            execution_logger=execution_logger,
            clock=FakeClock().monotonic,
            sleep=FakeClock().sleep,
        )
    events: list[tuple[str, dict[str, object]]] = [
        (event.type, dict(event.payload)) for event in execution_logger.events()
    ]
    return cast("dict[str, object]", result.to_json_object()), events


def test_psu_auto_approve_login_page_is_logged_and_diagnosed() -> None:
    """An HTML login page response is logged and its title recorded as evidence."""
    page = "<html><head><title> Ozone &amp; Bank\n Login </title></head><body>secret-form</body></html>"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "session=abc"},
            text=page,
        )

    result, events = _run_auto_approve(handler, state="t" * 32)

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


def test_psu_auto_approve_json_error_body_is_recorded_masked() -> None:
    """A JSON error from the authorisation endpoint is kept, with sensitive keys masked."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_request", "id_token": "leak"})

    result, events = _run_auto_approve(handler, state="j" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert details["response"]["body"] == {"error": "invalid_request", "id_token": "***"}
    assert ("response-received", {"statusCode": 400, "url": result["url"], "contentType": "application/json"}) in events


def test_psu_auto_approve_success_attaches_masked_redirect_evidence() -> None:
    """A successful auto-approve redirect keeps evidence but masks the authorisation code."""
    state = "s" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": f"https://conformance.example.com/callback?state={state}&code=secret-code"},
        )

    result, _events = _run_auto_approve(handler, state=state)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert result["status"] == "passed"
    assert details["response"]["redirectsToRedirectUri"] is True
    assert "code=***" in str(details["response"]["location"])
    assert "secret-code" not in json.dumps(result)


def test_psu_auto_approve_off_target_redirect_records_target_without_parameters() -> None:
    """A redirect elsewhere (for example an ASPSP error page) records origin and path only."""

    def handler(_request: httpx.Request) -> httpx.Response:
        userinfo = "operator@"
        return httpx.Response(
            302, headers={"Location": f"https://{userinfo}login.aspsp.example:8443/perry/error?session=s3cret#frag"}
        )

    result, _events = _run_auto_approve(handler, state="o" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    rendered = json.dumps(result)
    assert details["response"]["redirectsToRedirectUri"] is False
    assert "location" not in details["response"]
    assert details["response"]["redirectTarget"] == "https://login.aspsp.example:8443/perry/error"
    assert "https://login.aspsp.example:8443/perry/error" in str(result["message"])
    assert "s3cret" not in rendered
    assert "frag" not in rendered
    assert "operator" not in rendered


def test_psu_auto_approve_step_reads_hybrid_flow_fragment_response() -> None:
    """OIDC hybrid-flow redirects return ``code``/``id_token`` in the fragment; both are read and masked."""
    state = "f" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={
                "Location": f"https://conformance.example.com/callback#code=frag-code&id_token=frag-idt&state={state}"
            },
        )

    result, _events = _run_auto_approve(handler, state=state)

    details = cast("dict[str, dict[str, object]]", result["details"])
    rendered = json.dumps(result)
    assert result["status"] == "passed"
    assert "frag-code" not in rendered
    assert "frag-idt" not in rendered
    assert "#code=***&id_token=***" in str(details["response"]["location"])


def test_psu_auto_approve_same_origin_error_page_is_followed_and_summarised() -> None:
    """A same-origin redirect to an ASPSP error page is followed with its session cookie and its text captured."""
    error_page = (
        "<html><head><title>OBL Error</title><style>.x{}</style></head><body>"
        "<script>var token='script-secret';</script>"
        "<form><input type='hidden' name='csrf' value='hidden-secret'></form>"
        "<p>That is an error.</p><pre>invalid_request: nonce_not_specified</pre></body></html>"
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/authorize":
            return httpx.Response(
                302, headers={"Location": "/perry/error?ref=abc", "Set-Cookie": "connect.sid=sess-1; Path=/"}
            )
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=error_page)

    result, events = _run_auto_approve(handler, state="e" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    page = cast("dict[str, object]", details["response"]["errorPage"])
    rendered = json.dumps(result)
    assert details["response"]["redirectTarget"] == "https://auth.example.com/perry/error"
    assert page["url"] == "https://auth.example.com/perry/error"
    assert page["statusCode"] == 200
    assert page["htmlTitle"] == "OBL Error"
    assert page["text"] == "That is an error. invalid_request: nonce_not_specified"
    assert "nonce_not_specified" in str(result["message"])
    assert seen[1].headers["Cookie"] == "connect.sid=sess-1"
    for secret in ("script-secret", "hidden-secret", "sess-1", "ref=abc"):
        assert secret not in rendered
    followed = [payload for event_type, payload in events if payload.get("followedRedirect")]
    assert [payload["url"] for payload in followed] == ["https://auth.example.com/perry/error"] * 2


def test_psu_auto_approve_cross_origin_redirect_is_not_followed() -> None:
    """Redirects to another origin are reported but never requested."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://login.other.example/error"})

    result, _events = _run_auto_approve(handler, state="c" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    assert len(seen) == 1
    assert "errorPage" not in details["response"]


def test_psu_auto_approve_error_redirect_following_is_bounded() -> None:
    """A same-origin redirect loop stops after the hop limit."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": f"/loop/{len(seen)}"})

    result, _events = _run_auto_approve(handler, state="l" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    page = cast("dict[str, object]", details["response"]["errorPage"])
    assert len(seen) == 4
    assert len(cast("list[object]", page["followedRedirects"])) == 3
    assert "text" not in page


def test_psu_auto_approve_error_page_transport_error_is_recorded() -> None:
    """A transport failure while following the error redirect is recorded, not raised."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(302, headers={"Location": "/perry/error"})
        raise httpx.ConnectError("boom", request=request)

    result, _events = _run_auto_approve(handler, state="t" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    page = cast("dict[str, object]", details["response"]["errorPage"])
    assert result["status"] == "failed"
    assert page["followedRedirects"] == [{"url": "https://auth.example.com/perry/error", "error": "boom"}]
    assert "url" not in page


def test_psu_auto_approve_error_page_json_body_is_masked() -> None:
    """A JSON error page reached by redirect is recorded with sensitive keys masked."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(302, headers={"Location": "/oauth/error"})
        return httpx.Response(400, json={"error": "invalid_request", "access_token": "leaky"})

    result, _events = _run_auto_approve(handler, state="j" * 32)

    details = cast("dict[str, dict[str, object]]", result["details"])
    page = cast("dict[str, object]", details["response"]["errorPage"])
    assert cast("dict[str, object]", page["body"])["error"] == "invalid_request"
    assert "leaky" not in json.dumps(result)
