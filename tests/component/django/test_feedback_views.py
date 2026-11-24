"""Offline browser feedback flows, inspecting the downloadable diagnostic ZIP."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import catalogue_scope_hierarchy
from conformance.api.feedback_store import feedback_store
from conformance.api.run_store import run_store
from conformance.catalogue import PlanDocumentBoundary

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _reset_stores() -> Iterator[None]:
    run_store.reset()
    feedback_store.reset()
    yield
    run_store.reset()
    feedback_store.reset()


def _data(**overrides: str) -> dict[str, str]:
    return {
        "source": "/",
        "kind": "page",
        "context_id": "",
        "category": "Bug",
        "summary": "A test issue",
        "description": "What happened",
        **overrides,
    }


def _download(client: Client, data: dict[str, str]) -> tuple[str, dict[str, bytes]]:
    response = client.post("/feedback/", data)
    assert response.status_code == 302, response.content.decode()
    location = str(response["Location"])
    preview = client.get(location)
    assert preview.status_code == 200
    download = client.get(location + "bundle.zip")
    assert download.status_code == 200
    assert download["Content-Type"] == "application/zip"
    assert "no-store" in download["Cache-Control"]
    assert download["Content-Disposition"].startswith('attachment; filename="beta-feedback-')
    assert download["X-Content-Type-Options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    assert manifest["files"] == list(files)
    assert manifest["capturedAt"] == json.loads(files["context.json"])["capturedAt"]
    return location, files


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.feedback: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        href = attributes.get("href") or ""
        if tag == "a" and href.startswith("/feedback/?"):
            self.feedback.append(attributes)


def _feedback_link(content: str) -> dict[str, str]:
    parser = _Links()
    parser.feed(content)
    assert len(parser.feedback) == 1
    attributes = parser.feedback[0]
    assert attributes["target"] == "_blank"
    assert attributes["hx-boost"] == "false"
    assert attributes["rel"] == "noopener noreferrer"
    href = attributes["href"]
    assert isinstance(href, str)
    return {key: value[0] for key, value in parse_qs(urlsplit(href).query).items()}


def test_page_only_feedback_is_structured_and_selectable_without_javascript() -> None:
    """No JS, SMTP or mail client is needed to prepare and download a report."""
    client = Client()
    page = client.get("/feedback/")
    assert page.status_code == 200
    content = page.content.decode()
    assert '<option value="" selected>Choose a category</option>' in content
    assert "Nothing is uploaded or sent automatically" in content
    assert 'name="expected"' not in content
    assert 'name="actual"' not in content
    location, files = _download(
        client,
        _data(
            category="Suggestion",
            summary="A & B <script>",
            environment="VM with no mail client",
            expected="Obsolete expected field",
            actual="Obsolete actual field",
        ),
    )
    assert set(files) == {"context.json", "manifest.json", "feedback.txt"}
    context = json.loads(files["context.json"])
    assert context["pageType"] == "page"
    assert context["pageUrl"] == "http://testserver/"
    assert context["environmentNotes"] == "VM with no mail client"
    message = files["feedback.txt"].decode()
    assert "Expected:" not in message and "Actual:" not in message
    assert "Obsolete" not in message
    assert "Automatic masking cannot guarantee" not in message
    assert "manually attach" not in message
    preview = client.get(location).content.decode()
    assert "A &amp; B &lt;script&gt;" in preview
    assert "readonly" in preview and "feedback.txt" in preview
    assert "Clipboard access failed" in preview
    assert "field.select()" in preview
    assert "execCommand" not in preview
    assert "approved process" in preview
    assert "Automatic masking cannot guarantee" in preview
    assert "manually attach it" in preview
    assert "mailto:standardsteam@openbanking.org.uk?" in preview
    assert "Give beta feedback <span" not in preview


def test_completed_developer_mode_run_is_remasked_in_every_file_and_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All run evidence is exported, but developer-mode credentials/actions are not."""
    monkeypatch.setenv("CONFORMANCE_DEVELOPER_MODE", "true")
    monkeypatch.setenv("CONFORMANCE_TOOL_VERSION", "feedback-test-version")
    record = run_store.create_run(
        plan_snapshot={
            "config": {
                "clientSecret": "fixture-credential",  # pragma: allowlist secret - synthetic masking fixture
                "signingPrivateKeyPath": "/keys/key.pem",
            },
            "businessTestData": {"ais": {"accountIds": ["account-keep"]}},
        },
        validation_result={"valid": True, "message": "access_token=fixture-credential"},
    )
    assert record.execution_logger is not None
    record.execution_logger.emit(
        "request-sent",
        payload={
            "headers": {"Authorization": "Bearer fixture-credential", "Cookie": "fixture-credential"},
            "body": '{"access_token":"fixture-credential"}',
            "url": "https://user:fixture-credential@example.test/?request=fixture-credential&accountId=account-keep",  # pragma: allowlist secret - synthetic URL credential fixture
        },
    )
    run_store.mark_running(record.run_id)
    run_store.mark_completed(
        record.run_id,
        result={
            "status": "passed",
            "certificatePem": "fixture-credential",
            "accountId": "account-keep",
            "error": "client_secret=fixture-credential",
        },
    )
    client = Client()
    location, files = _download(
        client,
        _data(
            source=f"/runs/{record.run_id}/",
            kind="run",
            context_id=record.run_id,
            description="Authorization: Bearer fixture-credential",
            environment="password=fixture-credential",
        ),
    )
    assert set(files) == {
        "context.json",
        "manifest.json",
        "feedback.txt",
        "run-status.json",
        "execution-log.ndjson",
        "result.json",
        "test-plan.json",
        "validation.json",
    }
    assert json.loads(files["context.json"])["toolVersion"] == "feedback-test-version"
    assert json.loads(files["run-status.json"])["status"] == "completed"
    assert json.loads(files["result.json"])["accountId"] == "account-keep"
    plan = json.loads(files["test-plan.json"])
    assert plan["config"]["signingPrivateKeyPath"] == "/keys/key.pem"
    assert plan["businessTestData"]["ais"]["accountIds"] == ["account-keep"]
    assert json.loads(files["manifest.json"])["unavailable"] == {}
    event = json.loads(files["execution-log.ndjson"])
    assert event["type"] == "request-sent"
    assert event["payload"]["headers"]["Authorization"] == "***"
    assert event["payload"]["url"] == "https://example.test/?request=***&accountId=account-keep"
    for content in files.values():
        assert b"fixture-credential" not in content
    assert b"fixture-credential" not in client.get(location).content
    # Export masking must not change developer-mode logging itself.
    assert b"fixture-credential" in record.execution_logger.to_ndjson_bytes()


@pytest.mark.parametrize("status", ["pending", "running", "failed", "completed"])
def test_run_lifecycle_and_missing_evidence_are_explicit(status: str) -> None:
    """Empty logs differ from absent plans/results, in every lifecycle state."""
    record = run_store.create_run()
    if status != "pending":
        run_store.mark_running(record.run_id)
    if status == "failed":
        run_store.mark_failed(record.run_id, error="Failure: access_token=fixture-credential")
    if status == "completed":
        run_store.mark_completed(record.run_id, result={"status": "passed"})
    _, files = _download(Client(), _data(source=f"/runs/{record.run_id}/", kind="run", context_id=record.run_id))
    assert json.loads(files["run-status.json"])["status"] == status
    assert files["execution-log.ndjson"] == b""
    missing = json.loads(files["manifest.json"])["unavailable"]
    assert "test-plan.json" in missing and "validation.json" in missing
    assert ("result.json" in missing) == (status != "completed")
    assert b"fixture-credential" not in files["run-status.json"]


def test_prepared_run_is_a_detached_snapshot_even_after_run_finishes_and_store_resets() -> None:
    """Download and preview refer to preparation time, never the latest run state."""
    record = run_store.create_run()
    assert record.execution_logger is not None
    client = Client()
    location, files = _download(client, _data(source=f"/runs/{record.run_id}/", kind="run", context_id=record.run_id))
    record.execution_logger.emit("request-sent", payload={"url": "https://example.test/after"})
    run_store.mark_running(record.run_id)
    run_store.mark_completed(record.run_id, result={"status": "passed"})
    run_store.reset()
    downloaded = client.get(location + "bundle.zip")
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == files
    assert b"No final result at capture time" in client.get(location).content


def test_legacy_missing_logger_is_not_reported_as_an_empty_log_and_psu_actions_are_excluded() -> None:
    """Legacy absent loggers and private authorization actions are handled explicitly."""
    record = run_store.create_run()
    record.execution_logger = None
    run_store.set_participant_action(
        record.run_id,
        step_id="authorize",
        url="https://participant-auth.example/never-export",
    )
    _, files = _download(Client(), _data(source=f"/runs/{record.run_id}/", kind="run", context_id=record.run_id))
    assert "execution-log.ndjson" not in files
    assert "No execution logger" in json.loads(files["manifest.json"])["unavailable"]["execution-log.ndjson"]
    assert all(b"participant-auth.example" not in value for value in files.values())


def test_unknown_run_and_cross_session_draft_fail_instead_of_exporting_unrelated_evidence() -> None:
    """Invalid/pruned context never falls back to the latest run or another draft."""
    client = Client()
    unknown_id = "a" * 32
    response = client.post("/feedback/", _data(source=f"/runs/{unknown_id}/", kind="run", context_id=unknown_id))
    assert response.status_code == 400
    assert b"Run not found" in response.content
    session = client.session
    draft = SessionBuilderDraftStore(session).create()
    session.save()
    data = _data(source=f"/builder/{draft.draft_id}/review/", kind="draft", context_id=draft.draft_id)
    response = Client().post("/feedback/", data)
    assert response.status_code == 400
    assert b"not found in this session" in response.content


def test_incomplete_saved_draft_exports_labelled_state_not_an_executable_plan() -> None:
    """Saved draft fallback masks credentials, keeps IDs/paths, and omits helper state."""
    client = Client()
    session = client.session
    store = SessionBuilderDraftStore(session)
    draft = (
        store.create()
        .with_config(
            config={
                "clientSecret": "fixture-credential",  # pragma: allowlist secret - synthetic masking fixture
                "signingPrivateKeyPath": "/keys/key.pem",
                "accountId": "keep",
            }
        )
        .with_discovery_metadata(discovery_metadata={"raw": "do-not-export"})
    )
    store.save(draft)
    session.save()
    _, files = _download(
        client, _data(source=f"/builder/{draft.draft_id}/catalogue/", kind="draft", context_id=draft.draft_id)
    )
    assert "builder-draft.json" in files and "test-plan.json" not in files
    saved = json.loads(files["builder-draft.json"])
    assert saved["config"]["accountId"] == "keep"
    assert saved["config"]["signingPrivateKeyPath"] == "/keys/key.pem"
    assert "discoveryMetadata" not in saved
    assert b"fixture-credential" not in files["builder-draft.json"]
    assert "cannot be converted" in json.loads(files["manifest.json"])["unavailable"]["test-plan.json"]


def test_convertible_saved_draft_exports_canonical_safe_plan() -> None:
    """The current session's convertible saved draft gets canonical plan evidence."""
    client = Client()
    session = client.session
    store = SessionBuilderDraftStore(session)
    hierarchy = catalogue_scope_hierarchy(
        PlanDocumentBoundary("open-banking-uk", "read-write", "4.0.1"),
        selected_resource_group_ids=("account-and-transaction",),
    )
    group = next(group for group in hierarchy.resource_groups if group.api == "ais" and group.endpoints)
    endpoint = next(endpoint for endpoint in group.endpoints if endpoint.path == "/open-banking/v4.0/aisp/accounts")
    draft = (
        store.create()
        .with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
        )
        .with_scope_selection(
            resource_group_ids=(group.id,),
            endpoint_ids=(endpoint.id,),
            endpoint_capability_ids={},
        )
        .with_config(
            config={
                "clientSecret": "fixture-credential",  # pragma: allowlist secret - synthetic masking fixture
                "resourceServer": {"baseUrl": "https://resource.example.test"},
                "discoveryUrl": "https://aspsp.example.test/.well-known/openid-configuration",
                "fapiSigning": {"signingPrivateKeyPath": "/keys/key.pem"},
            }
        )
    )
    store.save(draft)
    session.save()
    _, files = _download(
        client, _data(source=f"/builder/{draft.draft_id}/review/", kind="draft", context_id=draft.draft_id)
    )
    assert "test-plan.json" in files and "builder-draft.json" not in files
    assert json.loads(files["test-plan.json"])["schemaVersion"] == "1.0"
    assert b"fixture-credential" not in files["test-plan.json"]
    assert b"/keys/key.pem" in files["test-plan.json"]


@pytest.mark.parametrize(
    "changes",
    [
        {"category": ""},
        {"category": "Invalid"},
        {"summary": "one\nsecond"},
        {"source": "https://elsewhere.test/"},
        {"source": "//elsewhere.test/"},
        {"source": "/\\elsewhere.test/"},
        {"source": "/bad\x01path"},
        {"source": "https://[invalid/"},
        {"kind": "unexpected"},
        {"context_id": "../secrets"},
        {"context_id": "a" * 32},
        {"kind": "run", "context_id": "a" * 32, "source": "/"},
        {"kind": "run", "context_id": "", "source": "/runs/"},
        {"description": "x" * 10001},
    ],
)
def test_invalid_context_or_narrative_is_rejected(changes: dict[str, str]) -> None:
    """Forms validate all submitted metadata/narrative before capturing evidence."""
    response = Client().post("/feedback/", _data(**changes))
    assert response.status_code == 400
    assert b"errorlist" in response.content


def test_malformed_get_context_is_an_explicit_error() -> None:
    """Initial context errors are not silently converted into page-only feedback."""
    response = Client().get("/feedback/", {"source": "https://elsewhere.test/"})
    assert response.status_code == 400
    assert b"Feedback unavailable" in response.content
    assert "no-store" in response["Cache-Control"]


def test_source_url_discards_query_and_fragment_and_page_only_does_not_attach_latest_run() -> None:
    """OAuth response data is excluded from metadata; no unrelated run is exported."""
    run_store.create_run()
    _, files = _download(Client(), _data(source="/callback/?code=fixture-credential#access_token=fixture-credential"))
    assert json.loads(files["context.json"])["pageUrl"] == "http://testserver/callback/"
    assert set(files) == {"context.json", "manifest.json", "feedback.txt"}
    assert all(b"fixture-credential" not in content for content in files.values())


def test_report_download_is_session_owned_and_expiry_restart_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prepared report is inaccessible to another session and after TTL/restart."""
    client = Client()
    location, _ = _download(client, _data())
    for url in (location, location + "bundle.zip"):
        response = Client().get(url)
        assert response.status_code == 404
        assert b"Report unavailable" in response.content
        assert "no-store" in response["Cache-Control"]
    monkeypatch.setattr("conformance.api.feedback_store.REPORT_TTL_SECONDS", 0)
    assert client.get(location + "bundle.zip").status_code == 404
    monkeypatch.setattr("conformance.api.feedback_store.REPORT_TTL_SECONDS", 1800)
    location, _ = _download(client, _data())
    feedback_store.reset()
    assert client.get(location).status_code == 404


def test_csrf_and_http_method_guards() -> None:
    """Preparation requires CSRF and report/download routes reject mutations."""
    client = Client(enforce_csrf_checks=True)
    assert client.post("/feedback/", _data()).status_code == 403
    page = client.get("/feedback/")
    assert page.status_code == 200
    response = client.post("/feedback/", {**_data(), "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
    assert response.status_code == 302
    location = str(response["Location"])
    assert client.post(location, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}).status_code == 405
    assert (
        client.post(location + "bundle.zip", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}).status_code
        == 405
    )
    assert Client().delete("/feedback/").status_code == 405


def test_bundle_size_failure_is_visible_without_a_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preparation failures are displayed rather than downloading a partial ZIP."""
    monkeypatch.setattr("conformance.feedback.MAX_BUNDLE_BYTES", 1)
    response = Client().post("/feedback/", _data())
    assert response.status_code == 400
    assert b"No report was prepared" in response.content


def test_shared_banner_carries_explicit_run_draft_or_page_context() -> None:
    """Feedback opens separately, avoids HTMX swaps, and omits callback query data."""
    client = Client()
    record = run_store.create_run()
    link = _feedback_link(client.get(f"/runs/{record.run_id}/").content.decode())
    assert link == {"source": f"/runs/{record.run_id}/", "kind": "run", "context_id": record.run_id}
    created = client.post("/builder/new/")
    path = str(created["Location"])
    draft_id = path.split("/")[2]
    link = _feedback_link(client.get(path).content.decode())
    assert link == {"source": path, "kind": "draft", "context_id": draft_id}
    for path in ("/", "/builder/import/", "/missing-page/"):
        assert _feedback_link(client.get(path).content.decode()) == {"source": path}
    callback = client.get("/callback/?code=fixture-credential&state=fixture-state")
    assert _feedback_link(callback.content.decode()) == {"source": "/callback/"}
