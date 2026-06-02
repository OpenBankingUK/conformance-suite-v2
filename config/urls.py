"""URL routes for the local conformance suite Django app."""

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path

from conformance.api.callback_views import callback_view


def health(request: HttpRequest) -> JsonResponse:
    """Return the lightweight application health response.

    Args:
        request: The incoming HTTP request (unused; endpoint is unconditional).

    Returns:
        JSON object with a single ``status`` key indicating service health.
    """
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health, name="health"),
    path("api/", include("conformance.api.urls")),
    # PSU authorization callback. Deliberately mounted at the project
    # root (not under ``/api/``) so it is NOT loopback-guarded — the
    # ASPSP redirects a participant browser here and that browser may
    # traverse a reverse proxy. Security relies on ``state``
    # unguessability + one-shot consumption (see callback_views.py).
    path("callback/", callback_view, name="psu-callback"),
    path("admin/", admin.site.urls),
]
