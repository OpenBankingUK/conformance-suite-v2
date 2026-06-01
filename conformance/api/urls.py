"""URL routes for the conformance run REST API."""

from django.urls import path

from conformance.api.views import create_run, get_run_result, get_run_status

urlpatterns = [
    path("runs/", create_run, name="api-create-run"),
    path("runs/<str:run_id>/", get_run_status, name="api-run-status"),
    path("runs/<str:run_id>/result/", get_run_result, name="api-run-result"),
]
