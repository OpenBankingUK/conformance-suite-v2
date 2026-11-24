"""Offline tests inspecting beta feedback ZIPs and mandatory redaction."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from conformance.api.feedback_store import FeedbackStore
from conformance.feedback import (
    REVIEW_WARNING,
    FeedbackExportError,
    prepare_feedback,
    redact_feedback,
    redact_feedback_text,
)
from conformance.json_types import JsonObject, JsonValue
from conformance.masking import MASKED_VALUE

pytestmark = pytest.mark.unit

_NARRATIVE = {"category": "Bug", "summary": "Example & details", "description": "Expected something else."}


def _report(evidence: dict[str, JsonValue] | None = None) -> bytes:
    return prepare_feedback(
        narrative=_NARRATIVE,
        page_url="https://tool.example/runs/run/",
        kind="run",
        context_id="run",
        evidence=evidence or {},
        unavailable={"validation.json": "Legacy validation unavailable"},
    ).bundle


def test_bundle_has_versioned_inventory_and_structured_files() -> None:
    """Actual ZIP files contain capture context, evidence and a complete email."""
    with zipfile.ZipFile(
        io.BytesIO(_report({"execution-log.ndjson": [], "result.json": {"status": "passed"}}))
    ) as zip_:
        context = json.loads(zip_.read("context.json"))
        manifest = json.loads(zip_.read("manifest.json"))
        assert context["schemaVersion"] == manifest["schemaVersion"] == "1.0"
        assert context["capturedAt"] == manifest["capturedAt"]
        assert context["toolVersion"]
        assert manifest["files"] == zip_.namelist()
        assert zip_.read("execution-log.ndjson") == b""
        assert json.loads(zip_.read("result.json")) == {"status": "passed"}
        assert manifest["unavailable"] == {"validation.json": "Legacy validation unavailable"}
        assert "standardsteam@openbanking.org.uk" in zip_.read("feedback.txt").decode()
        message = zip_.read("feedback.txt").decode()
        assert REVIEW_WARNING not in message
        assert "Nothing has been sent" not in message
        assert "manually attach" not in message
        assert manifest["maskingPolicy"] == REVIEW_WARNING


@pytest.mark.parametrize(
    "text",
    [
        "access_token=fixture-credential",
        "Failure: access_token=fixture-credential",
        "Error: Cookie: session=fixture-credential; another=also-sensitive",
        "ClientSecret: 'fixture-credential'",  # pragma: allowlist secret - synthetic masking fixture
        "Authorization: Bearer fixture-credential",
        "Bearer fixture-credential",
        "Basic fixture-credential",
        "Cookie: session=fixture-credential; next=also-sensitive",
        "https://user:fixture-credential@example.test/path?ACCESS_TOKEN=fixture-credential&account=keep#token",  # pragma: allowlist secret - synthetic URL credential fixture
        '{"body":{"client_secret":"fixture-credential"}}',  # pragma: allowlist secret - synthetic masking fixture
        '{"Authorization":"fixture-credential"}',
        "-----BEGIN CERTIFICATE-----\nfixture-credential\n-----END CERTIFICATE-----",
        "-----BEGIN PRIVATE KEY-----\nfixture-credential",  # pragma: allowlist secret - deliberately invalid PEM fixture
    ],
)
def test_recognized_free_text_credentials_are_removed(text: str) -> None:
    """Narrative/error strings use export masking, not developer-mode settings."""
    assert "fixture-credential" not in redact_feedback_text(text)


def test_nested_credentials_headers_jwk_and_signed_material_are_masked() -> None:
    """Credential forms are absent while participant identifiers/paths survive."""
    payload: JsonObject = {
        "nested": {
            "clientSecret": "fixture-credential",  # pragma: allowlist secret - synthetic masking fixture
            "AUTHORIZATION": "fixture-credential",
            "privateKey": {"path": "/keys/signing.pem", "inline": "fixture-credential"},
            "privateKeys": [{"path": "/keys/another.pem", "inline": "fixture-credential"}],
            "certificatePem": "fixture-credential",
            "accountIds": ["participant-account"],
            "client_id": "participant-client",
            "signingPrivateKeyPath": "/keys/signing.pem",
            "headers": [
                {
                    "name": "X-API-Key",
                    "value": "fixture-credential",
                    "extra": {"password": "fixture-credential"},  # pragma: allowlist secret - synthetic masking fixture
                }
            ],
            "runtimeInputs": [{"id": "custom-name", "sensitive": True, "value": "fixture-credential"}],
            "jwk": {"kty": "RSA", "d": "fixture-credential", "x5c": ["fixture-credential"], "kid": "keep-kid"},
            "url": "https://user:fixture-credential@example.test/?code=fixture-credential&accountId=participant-account",  # pragma: allowlist secret - synthetic URL credential fixture
        },
        "body": '{"refresh_token": "fixture-credential"}',
        "jws": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",  # pragma: allowlist secret - unsigned synthetic JWS fixture
        "detachedJws": "eyJhbGciOiJSUzI1NiJ9..c2lnbmF0dXJl",
        "jwe": "eyJhbGciOiJSUzI1NiJ9.a2V5.aXY.Y2lwaGVy.dGFn",
    }
    original = json.dumps(payload)
    with zipfile.ZipFile(io.BytesIO(_report({"result.json": payload}))) as zip_:
        result_text = zip_.read("result.json").decode()
        assert "fixture-credential" not in result_text
        assert "eyJhbGci" not in result_text
        assert "participant-account" in result_text
        assert "participant-client" in result_text
        assert "/keys/signing.pem" in result_text
        result = json.loads(result_text)
        assert result["nested"]["privateKey"]["path"] == "/keys/signing.pem"
        assert result["nested"]["privateKey"]["inline"] == "***"
        assert result["nested"]["privateKeys"][0]["path"] == "/keys/another.pem"
        assert result["nested"]["headers"][0]["extra"]["password"] == MASKED_VALUE
    assert json.dumps(payload) == original


def test_email_fields_are_encoded_and_compact() -> None:
    """Mailto remains short and cannot inject arbitrary email query parameters."""
    report = prepare_feedback(
        narrative={**_NARRATIVE, "description": "A long description &bcc=someone\n" * 500},
        page_url="https://tool.example/",
        kind="page",
        context_id="",
        evidence={},
        unavailable={},
    )
    query = parse_qs(urlsplit(report.mailto).query)
    assert set(query) == {"subject", "body"}
    assert "+" not in report.mailto
    assert len(report.mailto) < 1500
    assert "A long description" in report.body
    assert "A long description" not in query["body"][0]
    assert "complete report is in feedback.txt" in query["body"][0]
    assert "manually attach" not in query["body"][0]


def test_empty_optional_sections_are_omitted_from_email() -> None:
    """An email contains supplied feedback, not empty form placeholders."""
    report = prepare_feedback(
        narrative={**_NARRATIVE, "reproduction": "", "environment": ""},
        page_url="https://tool.example/",
        kind="page",
        context_id="",
        evidence={},
        unavailable={},
    )
    assert "Description:\nExpected something else." in report.body
    assert "Reproduction:" not in report.body
    assert "Environment:" not in report.body


def test_oversized_bundle_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """No success-shaped partial diagnostic bundle is returned on size failure."""
    monkeypatch.setattr("conformance.feedback.MAX_BUNDLE_BYTES", 10)
    with pytest.raises(FeedbackExportError, match="exceeds"):
        _report()


def test_invalid_log_shape_fails_explicitly() -> None:
    """NDJSON exports require structured log events."""
    with pytest.raises(FeedbackExportError, match="event array"):
        _report({"execution-log.ndjson": "not an event array"})


def test_url_redaction_preserves_identifiers_and_handles_malformed_urls() -> None:
    """URL credentials/fragments are removed, not query identifiers."""
    text = redact_feedback_text("https://user:pass@example.test/p?code=hide&accountId=keep#access_token=hide")
    assert text == "https://example.test/p?code=***&accountId=keep"
    assert redact_feedback_text("https://[invalid/") == "***"
    assert redact_feedback({"count": 1, "passed": True, "nothing": None}) == {
        "count": 1,
        "passed": True,
        "nothing": None,
    }


def test_store_enforces_ownership_expiry_and_session_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the preparing browser owns a report, and retention is bounded."""
    clock = [100.0]
    monkeypatch.setattr("conformance.api.feedback_store.monotonic", lambda: clock[0])
    store = FeedbackStore()
    reports = [
        prepare_feedback(
            narrative=_NARRATIVE,
            page_url="https://tool.example/",
            kind="page",
            context_id="",
            evidence={},
            unavailable={},
        )
        for _ in range(4)
    ]
    for report in reports:
        store.put("session-one", report)
    assert store.get("session-one", reports[0].report_id) is None
    assert store.get("session-one", reports[-1].report_id) == reports[-1]
    assert store.get("session-two", reports[-1].report_id) is None
    clock[0] += 1800
    assert store.get("session-one", reports[-1].report_id) is None
    store.put("session-one", reports[-1])
    store.reset()
    assert store.get("session-one", reports[-1].report_id) is None


def test_global_capacity_and_byte_limit_evict_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Global report count and stored ZIP bytes have independent bounds."""
    store = FeedbackStore()
    report = prepare_feedback(
        narrative=_NARRATIVE, page_url="https://tool.example/", kind="page", context_id="", evidence={}, unavailable={}
    )
    other = replace(report, report_id="other")
    monkeypatch.setattr("conformance.api.feedback_store.MAX_REPORTS", 1)
    store.put("one", report)
    store.put("two", other)
    assert store.get("one", report.report_id) is None
    assert store.get("two", other.report_id) == other
    monkeypatch.setattr("conformance.api.feedback_store.MAX_REPORTS", 20)
    monkeypatch.setattr("conformance.api.feedback_store.MAX_STORED_BYTES", len(report.bundle))
    store.put("one", report)
    assert store.get("two", other.report_id) is None
    assert store.get("one", report.report_id) == report
