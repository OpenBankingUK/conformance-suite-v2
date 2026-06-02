"""URL routes for the conformance run REST API."""

from django.urls import path

from conformance.api.views import (
    create_run,
    get_auth_session,
    get_run_log,
    get_run_result,
    get_run_status,
    register_auth_session,
)

urlpatterns = [
    path("runs/", create_run, name="api-create-run"),
    path("runs/<str:run_id>/", get_run_status, name="api-run-status"),
    path("runs/<str:run_id>/result/", get_run_result, name="api-run-result"),
    path("runs/<str:run_id>/log/", get_run_log, name="api-run-log"),
    path(
        "runs/<str:run_id>/auth-sessions/",
        register_auth_session,
        name="api-register-auth-session",
    ),
    path(
        "runs/<str:run_id>/auth-sessions/<str:state>/",
        get_auth_session,
        name="api-get-auth-session",
    ),
]
