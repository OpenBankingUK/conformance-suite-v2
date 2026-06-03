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
    run_log_partial,
    run_result_partial,
    run_status_partial,
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
    """Render a friendly HTML 404 page for unmatched browser routes.

    Args:
        request: The incoming HTTP request.
        unmatched_path: URL path segment that did not match any earlier route.

    Returns:
        HTML response with HTTP 404 status.
    """
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
    path("runs/<str:run_id>/log/", run_log_partial, name="ui-run-log"),
    path("runs/<str:run_id>/result/", run_result_partial, name="ui-run-result"),
    path("api/", include("conformance.api.urls")),
    # PSU authorization callback. Deliberately mounted at the project
    # root (not under ``/api/``) so it is NOT loopback-guarded — the
    # ASPSP redirects a participant browser here and that browser may
    # traverse a reverse proxy. Security relies on ``state``
    # unguessability + one-shot consumption (see callback_views.py).
    path("callback/", callback_view, name="psu-callback"),
    path("admin/", admin.site.urls),
    path("<path:unmatched_path>", not_found, name="not-found"),
]
