"""URL routes for the local conformance suite Django app."""

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import path


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
    path("admin/", admin.site.urls),
]
