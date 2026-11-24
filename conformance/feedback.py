"""Local beta feedback bundles with mandatory OAuth/OIDC credential redaction.

This export boundary is independent of developer-mode logging. Identifiers and
file references are retained; arbitrary narrative/business data still needs
participant review before sharing.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from conformance import FEEDBACK_EMAIL
from conformance.catalogue import CompiledTestPlan, PlanDocumentV2, plan_document_to_json_object
from conformance.json_types import JsonObject, JsonValue
from conformance.masking import MASKED_VALUE, SENSITIVE_HEADER_NAMES, SENSITIVE_JSON_KEYS, mask_free_text
from conformance.test_plan_validation import safe_test_plan_snapshot
from conformance.version import resolve_conformance_tool_version

RECIPIENT = FEEDBACK_EMAIL
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
REVIEW_WARNING = (
    "Credentials and certificates are masked, including developer-mode evidence. "
    "Account/customer identifiers, URLs and local file paths are retained. "
    "Automatic masking cannot guarantee arbitrary text or business data is safe. Review every file before sharing."
)
_SECRET_NAMES = {
    re.sub(r"[-_]", "", key).lower()
    for key in SENSITIVE_JSON_KEYS | SENSITIVE_HEADER_NAMES
    if key not in {"x-fapi-customer-ip-address", "x-fapi-financial-id"}
} | {"apikey", "certificate", "certificatechain", "cabundle", "x5c", "state"}
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_JWS = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]{8,}(?![\w-])")
_JWE = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*){4}(?![\w.-])")
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[^\s,;\"']+", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"""(?P<key>[\w-]+)["']?\s*[:=]\s*""", re.IGNORECASE)
_VALUE = re.compile(r""""[^"]*"|'[^']*'|[^\s&,;]+""")
_HEADER = re.compile(
    r"(?im)\b(?P<key>authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-jws-signature)\s*:[^\r\n]*"
)


class FeedbackExportError(ValueError):
    """Feedback evidence cannot be exported within local resource limits."""


def _sensitive(key: str) -> bool:
    normalized = re.sub(r"[-_]", "", key).lower()
    if normalized.endswith(("path", "file", "filename")):
        return False
    return (
        normalized in _SECRET_NAMES
        or any(part in normalized for part in ("secret", "password", "privatekey"))
        or normalized.endswith(("token", "pem", "certificate", "certificates", "certificatechain", "cabundle"))
    )


def redact_feedback(value: JsonValue) -> JsonValue:
    """Detach evidence and redact credentials without reading referenced files."""
    if isinstance(value, dict):
        is_jwk = "kty" in value
        credential_record = isinstance(name := value.get("name"), str) and _sensitive(name)
        return {
            key: _masked_credential(item)
            if _sensitive(key)
            or (is_jwk and key in {"d", "p", "q", "dp", "dq", "qi", "k"})
            or ((value.get("sensitive") is True or credential_record) and key in {"value", "values"})
            else redact_feedback(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_feedback(item) for item in value]
    if isinstance(value, str):
        return redact_feedback_text(value)
    return value


def _masked_credential(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: redact_feedback(item)
            if key.lower().endswith(("path", "file", "filename"))
            else _masked_credential(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_masked_credential(item) for item in value]
    return MASKED_VALUE


def feedback_plan_snapshot(document: PlanDocumentV2, compiled_plan: CompiledTestPlan) -> JsonObject:
    """Reuse plan masking, retaining participant identifiers/file references.

    Normal plan exports additionally remove some identifiers and key paths.
    Feedback deliberately retains those, but never restores schema-declared
    sensitive runtime inputs or inline credential material.
    """
    sensitive_ids = {trace.input_id for trace in compiled_plan.traceability.runtime_input_snapshot if trace.sensitive}
    snapshot = safe_test_plan_snapshot(document, compiled_plan=compiled_plan)
    _restore_references(snapshot, plan_document_to_json_object(document), sensitive_ids)
    return snapshot


def _restore_references(target: JsonValue, source: JsonValue, sensitive_ids: set[str]) -> None:
    if isinstance(target, dict) and isinstance(source, dict):
        for key, item in source.items():
            if key in sensitive_ids:
                continue
            normalized = re.sub(r"[-_]", "", key).lower()
            if normalized.endswith(("path", "file", "filename")) or normalized in {
                "accountid",
                "accountids",
                "identification",
                "xfapifinancialid",
                "xfapicustomeripaddress",
            }:
                target[key] = redact_feedback(item)
            elif key in target:
                _restore_references(target[key], item, sensitive_ids)
    elif isinstance(target, list) and isinstance(source, list):
        for masked_item, original_item in zip(target, source, strict=True):
            _restore_references(masked_item, original_item, sensitive_ids)


def _redact_url(match: re.Match[str]) -> str:
    try:
        parts = urlsplit(match.group())
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode(
            [
                (key, MASKED_VALUE if _sensitive(key) else redact_feedback_text(item))
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ],
            safe="*",
        )
        # OAuth fragments can contain tokens or authorization response data.
        return urlunsplit(parts._replace(netloc=netloc, query=query, fragment=""))
    except ValueError:
        return MASKED_VALUE


def redact_feedback_text(text: str) -> str:
    """Mask PEM, JWS, URL credentials and recognized free-text credential forms."""
    text = mask_free_text(text)
    # JSON HTTP bodies often arrive as strings rather than structured objects.
    if text.lstrip().startswith(("{", "[")):
        try:
            parsed: JsonValue = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, (dict, list)):
                return json.dumps(redact_feedback(parsed), ensure_ascii=True)
    text = _URL.sub(_redact_url, text)
    text = _JWE.sub(MASKED_VALUE, text)
    text = _JWS.sub(MASKED_VALUE, text)
    text = _AUTH.sub(lambda match: f"{match[1]} {MASKED_VALUE}", text)
    text = _HEADER.sub(lambda match: f"{match['key']}: {MASKED_VALUE}", text)
    output: list[str] = []
    cursor = 0
    # Match key markers separately so a prefix such as "Failure:" cannot
    # consume and hide a subsequent credential assignment.
    for match in _ASSIGNMENT.finditer(text):
        if match.start() < cursor or not _sensitive(match["key"]):
            continue
        value = _VALUE.match(text, match.end())
        if value is not None:
            output.extend((text[cursor : match.end()], MASKED_VALUE))
            cursor = value.end()
    output.append(text[cursor:])
    return "".join(output)


@dataclass(frozen=True)
class FeedbackReport:
    """Immutable prepared report, shared by the preview and ZIP download."""

    report_id: str
    subject: str
    body: str
    mailto: str
    bundle: bytes
    files: tuple[str, ...]
    unavailable: tuple[tuple[str, str], ...]
    captured_at: str


def prepare_feedback(
    *,
    narrative: Mapping[str, str],
    page_url: str,
    kind: str,
    context_id: str,
    evidence: Mapping[str, JsonValue],
    unavailable: Mapping[str, str],
) -> FeedbackReport:
    """Capture a versioned diagnostic ZIP; no network or host inspection occurs."""
    report_id = uuid4().hex
    captured_at = datetime.now(UTC).isoformat()
    page_url = redact_feedback_text(page_url)
    version = redact_feedback_text(resolve_conformance_tool_version())
    fields = {key: redact_feedback_text(value) for key, value in narrative.items()}
    missing = {key: redact_feedback_text(value) for key, value in unavailable.items()}
    context: JsonObject = {
        "schemaVersion": "1.0",
        "reportId": report_id,
        "capturedAt": captured_at,
        "toolVersion": version,
        "pageUrl": page_url,
        "pageType": kind,
        "contextId": context_id,
        "environmentNotes": fields.get("environment", ""),
    }
    subject = f"[Conformance beta {version}] {fields['category']}: {fields['summary']}"
    body = "\n\n".join(
        [
            f"Report: {report_id}\nTool version: {version}\nPage: {page_url}\nContext: {kind} {context_id}",
            *(f"{key.replace('_', ' ').title()}:\n{value}" for key, value in fields.items() if value),
            "Evidence included:\n" + "\n".join(["feedback.txt", "context.json", "manifest.json", *evidence]),
            "Unavailable evidence:\n" + ("\n".join(f"{key}: {value}" for key, value in missing.items()) or "None"),
        ]
    )
    files: dict[str, bytes] = {
        "feedback.txt": f"To: {RECIPIENT}\nSubject: {subject}\n\n{body}\n".encode(),
        "context.json": _json_bytes(context),
    }
    for name, value in evidence.items():
        masked = redact_feedback(value)
        if name == "execution-log.ndjson":
            if not isinstance(masked, list):
                raise FeedbackExportError("Execution log must contain an event array.")
            files[name] = b"".join(_json_bytes(item, indent=None) + b"\n" for item in masked)
        else:
            files[name] = _json_bytes(masked)
    manifest: JsonObject = {
        "schemaVersion": "1.0",
        "capturedAt": captured_at,
        "files": [*files, "manifest.json"],
        "unavailable": dict(missing),
        "maskingPolicy": REVIEW_WARNING,
        "captureNotes": (
            "Run evidence is a point-in-time snapshot; active runs may have incomplete results. "
            "Builder evidence contains saved state only, not unsaved browser edits. "
            "A saved test plan is not a guarantee of executable configuration."
        ),
    }
    files["manifest.json"] = _json_bytes(manifest)
    if sum(len(content) for content in files.values()) > MAX_BUNDLE_BYTES:
        raise FeedbackExportError("Diagnostic evidence exceeds the 16 MiB limit. No report was prepared.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    compact_subject = f"[Conformance beta] {fields['category']} report {report_id}"
    compact_body = (
        f"Feedback report {report_id} for tool {version}.\n"
        f"{fields['category']}: {fields['summary']}\n"
        "The complete report is in feedback.txt in the diagnostic ZIP."
    )
    return FeedbackReport(
        report_id,
        subject,
        body,
        f"mailto:{RECIPIENT}?" + urlencode({"subject": compact_subject, "body": compact_body}, quote_via=quote),
        output.getvalue(),
        tuple(files),
        tuple(missing.items()),
        captured_at,
    )


def _json_bytes(value: JsonValue, *, indent: int | None = 2) -> bytes:
    return json.dumps(value, indent=indent, sort_keys=True, ensure_ascii=True).encode()
