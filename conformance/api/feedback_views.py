"""Browser-only local feedback preparation and session-owned downloads."""

from __future__ import annotations

from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from django import forms
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import plan_document_from_draft
from conformance.api.feedback_store import feedback_store
from conformance.api.run_store import run_store
from conformance.catalogue import CatalogueError, compile_test_plan_document
from conformance.catalogue_registry import supported_catalogues
from conformance.feedback import (
    RECIPIENT,
    REVIEW_WARNING,
    FeedbackExportError,
    feedback_plan_snapshot,
    prepare_feedback,
    redact_feedback_text,
)
from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError

_OWNER_KEY = "conformance_feedback_owner"
_NARRATIVE_FIELDS = ("category", "summary", "description", "reproduction", "environment")
_REPORT_MISSING = (
    "Report unavailable: it may have expired, been evicted, or its server process restarted. Prepare again."
)


class FeedbackContextForm(forms.Form):
    """Validate local source metadata; it is never fetched or redirected to."""

    source = forms.CharField(max_length=2048, widget=forms.HiddenInput)
    kind = forms.ChoiceField(
        choices=(("page", "Page only"), ("run", "Run"), ("draft", "Saved builder draft")),
        widget=forms.HiddenInput,
    )
    context_id = forms.RegexField(r"^[a-f0-9]{32}$", required=False, widget=forms.HiddenInput)

    def clean_source(self) -> str:
        """Accept only local absolute paths and discard query/fragment data."""
        source = cast(str, self.cleaned_data["source"])
        try:
            parts = urlsplit(source)
        except ValueError as error:
            raise forms.ValidationError("Invalid source page.") from error
        if parts.scheme or parts.netloc or not parts.path.startswith("/") or "\\" in parts.path:
            raise forms.ValidationError("Source must be a local application path.")
        if any(ord(char) < 32 for char in source) or parts.path.startswith("//"):
            raise forms.ValidationError("Invalid source page.")
        return parts.path

    def clean(self) -> dict[str, object]:
        """Ensure a run/draft belongs to the explicitly selected source path."""
        data = super().clean()
        if data is None:
            return {}
        source = data.get("source")
        kind = data.get("kind")
        context_id = data.get("context_id")
        if kind in {"run", "draft"}:
            prefix = "runs" if kind == "run" else "builder"
            if not context_id or not isinstance(source, str) or not source.startswith(f"/{prefix}/{context_id}/"):
                raise forms.ValidationError("Feedback context does not match its source page.")
        elif context_id:
            raise forms.ValidationError("Page-only feedback cannot attach a run or draft.")
        return cast(dict[str, object], data)


class FeedbackForm(FeedbackContextForm):
    """Participant feedback with a required, unselected classification."""

    category = forms.ChoiceField(
        choices=(
            ("", "Choose a category"),
            ("Bug", "Bug"),
            ("Suggestion", "Suggestion"),
            ("Issue", "Issue"),
            ("Other", "Other"),
        ),
    )
    summary = forms.CharField(max_length=160)
    description = forms.CharField(max_length=10000, widget=forms.Textarea)
    reproduction = forms.CharField(
        label="Steps to reproduce",
        max_length=10000,
        required=False,
        widget=forms.Textarea,
    )
    environment = forms.CharField(
        label="Environment notes",
        max_length=3000,
        required=False,
        widget=forms.Textarea,
        help_text="Optional: Docker/source, VM, OS/browser, proxy or network restrictions. Do not include credentials.",
    )

    def clean_summary(self) -> str:
        """Keep email subject text on one line."""
        summary = cast(str, self.cleaned_data["summary"])
        if "\r" in summary or "\n" in summary:
            raise forms.ValidationError("Use a single-line summary.")
        return summary


def _owner(request: HttpRequest) -> str:
    owner = request.session.get(_OWNER_KEY)
    if not isinstance(owner, str):
        owner = uuid4().hex
        request.session[_OWNER_KEY] = owner
    return owner


def _capture(kind: str, context_id: str) -> tuple[dict[str, JsonValue], dict[str, str]]:
    if kind != "run":
        return {}, {}
    snapshot = run_store.feedback_snapshot(context_id)
    if snapshot is None:
        raise FeedbackExportError("Run not found: it may have been pruned or its server process restarted.")
    evidence: dict[str, JsonValue] = {}
    missing: dict[str, str] = {}
    for name, value in snapshot.items():
        if value is None:
            missing[name] = {
                "result.json": "No final result at capture time (active or failed run, or legacy evidence).",
                "test-plan.json": "No launch-time test plan snapshot (legacy run).",
                "validation.json": "No launch-time validation snapshot (legacy run).",
                "execution-log.ndjson": "No execution logger attached (legacy run).",
            }[name]
        else:
            evidence[name] = value
    return evidence, missing


def _capture_draft(request: HttpRequest, context_id: str) -> tuple[dict[str, JsonValue], dict[str, str]]:
    draft = SessionBuilderDraftStore(request.session).get(context_id)
    if draft is None:
        raise FeedbackExportError("Saved builder draft not found in this session. It may have expired or been pruned.")
    try:
        document = plan_document_from_draft(draft)
        compiled = compile_test_plan_document(document, supported_catalogues())
    except (CatalogueError, ConfigError) as error:
        saved = draft.to_session_object()
        # Discovery helper state is not executable plan evidence.
        saved.pop("discoveryMetadata", None)
        return {"builder-draft.json": saved}, {"test-plan.json": f"Saved draft cannot be converted: {error}"}
    return {"test-plan.json": feedback_plan_snapshot(document, compiled)}, {}


@never_cache
@require_http_methods(["GET", "POST"])
def feedback_prepare(request: HttpRequest) -> HttpResponse:
    """Render or prepare feedback without sending anything off the deployment."""
    status = 200
    if request.method == "GET":
        context_form = FeedbackContextForm({"source": "/", "kind": "page", "context_id": "", **request.GET.dict()})
        if not context_form.is_valid():
            return render(
                request,
                "conformance/feedback.html",
                {"context_errors": context_form.errors, "feedback_page": True},
                status=400,
            )
        form = FeedbackForm(initial=context_form.cleaned_data)
    else:
        form = FeedbackForm(request.POST)
        if form.is_valid():
            kind = cast(str, form.cleaned_data["kind"])
            context_id = cast(str, form.cleaned_data["context_id"])
            try:
                evidence, missing = (
                    _capture_draft(request, context_id) if kind == "draft" else _capture(kind, context_id)
                )
                report = prepare_feedback(
                    narrative={key: cast(str, form.cleaned_data[key]) for key in _NARRATIVE_FIELDS},
                    page_url=request.build_absolute_uri(cast(str, form.cleaned_data["source"])),
                    kind=kind,
                    context_id=context_id,
                    evidence=evidence,
                    unavailable=missing,
                )
            except FeedbackExportError as error:
                form.add_error(None, redact_feedback_text(str(error)))
            else:
                feedback_store.put(_owner(request), report)
                return redirect("feedback-report", report_id=report.report_id)
        status = 400
    return render(
        request,
        "conformance/feedback.html",
        {"form": form, "recipient": RECIPIENT, "review_warning": REVIEW_WARNING, "feedback_page": True},
        status=status,
    )


def _stored_report_response(request: HttpRequest, report_id: str, *, download: bool) -> HttpResponse:
    owner = request.session.get(_OWNER_KEY)
    report = feedback_store.get(owner, report_id) if isinstance(owner, str) else None
    if report is None:
        return render(
            request,
            "conformance/feedback.html",
            {"report_error": _REPORT_MISSING, "feedback_page": True},
            status=404,
        )
    if download:
        response = HttpResponse(report.bundle, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="beta-feedback-{report.report_id}.zip"'
        response["X-Content-Type-Options"] = "nosniff"
        return response
    return render(
        request,
        "conformance/feedback.html",
        {"report": report, "recipient": RECIPIENT, "review_warning": REVIEW_WARNING, "feedback_page": True},
    )


@never_cache
@require_GET
def feedback_report(request: HttpRequest, report_id: str) -> HttpResponse:
    """Preview exactly the immutable report prepared by this browser session."""
    return _stored_report_response(request, report_id, download=False)


@never_cache
@require_GET
def feedback_download(request: HttpRequest, report_id: str) -> HttpResponse:
    """Download the prepared snapshot, without re-reading live runs or drafts."""
    return _stored_report_response(request, report_id, download=True)
