"""URL routes for the local conformance suite Django app."""

from django.contrib import admin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import include, path
from django.views.generic import RedirectView

from conformance.api.callback_views import callback_view
from conformance.api.ui_views import (
    plan_builder,
    plan_launch,
    plan_preview,
    run_detail,
    run_log_download,
    run_log_partial,
    run_result_download,
    run_result_partial,
    run_status_partial,
    run_steps_partial,
    run_wait,
)


def health(request: HttpRequest) -> JsonResponse:
    """Return the lightweight application health response.

    Args:
        request: The incoming HTTP request (unused; endpoint is unconditional).

    Returns:
        JSON object with a single ``status`` key indicating service health.
    """
    return JsonResponse({"status": "ok"})


def not_found(request: HttpRequest, unmatched_path: str) -> HttpResponse:
    """Return the correct 404 shape for unmatched browser and API routes.

    Args:
        request: The incoming HTTP request.
        unmatched_path: URL path segment that did not match any earlier route.

    Returns:
        JSON 404 response for API namespace misses, otherwise a friendly
        HTML response with HTTP 404 status.
    """
    if unmatched_path == "api" or unmatched_path.startswith("api/"):
        return JsonResponse({"error": "API endpoint not found"}, status=404)
    return render(
        request,
        "conformance/not_found.html",
        {"unmatched_path": f"/{unmatched_path}"},
        status=404,
    )


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="plan-builder", permanent=False), name="home"),
    path("health/", health, name="health"),
    path("plan/", plan_builder, name="plan-builder"),
    path("plan/preview/", plan_preview, name="plan-preview"),
    path("plan/launch/", plan_launch, name="plan-launch"),
    path("runs/<str:run_id>/", run_detail, name="ui-run-detail"),
    path("runs/<str:run_id>/status/", run_status_partial, name="ui-run-status"),
    path("runs/<str:run_id>/steps/", run_steps_partial, name="ui-run-steps"),
    path("runs/<str:run_id>/log/", run_log_partial, name="ui-run-log"),
    path("runs/<str:run_id>/log.json", run_log_download, name="ui-run-log-download"),
    path("runs/<str:run_id>/result/", run_result_partial, name="ui-run-result"),
    path("runs/<str:run_id>/result.json", run_result_download, name="ui-run-result-download"),
    path("runs/<str:run_id>/wait/", run_wait, name="ui-run-wait"),
    path("api/", include("conformance.api.urls")),
    # PSU authorization callback. Deliberately mounted at the project
    # root (not under ``/api/``) so it is NOT loopback-guarded — the
    # ASPSP redirects a participant browser here and that browser may
    # traverse a reverse proxy. Security relies on ``state``
    # unguessability + one-shot consumption (see callback_views.py).
    path("callback/", callback_view, name="psu-callback"),
    path("conformancesuite/callback", callback_view, name="legacy-psu-callback"),
    path("conformancesuite/callback/", callback_view, name="legacy-psu-callback-slash"),
    path("admin/", admin.site.urls),
    path("<path:unmatched_path>", not_found, name="not-found"),
]
