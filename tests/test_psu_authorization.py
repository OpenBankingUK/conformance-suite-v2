"""Unit tests for OAuth 2.0 PSU authorisation helpers."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest

from conformance.psu_authorization import build_authorization_url


@pytest.mark.unit
def test_build_authorization_url_replaces_endpoint_oauth_parameters() -> None:
    """Executor-owned OAuth parameters override stale endpoint query values."""
    authorization_url = build_authorization_url(
        endpoint=(
            "https://auth.example.com/authorize?"
            "client_id=stale-client&foo=bar&request=stale.jwt&scope=old-scope&foo=baz"
        ),
        client_id="client-123",
        redirect_uri="https://conformance.example.com/callback",
        response_type="code id_token",
        scope="openid accounts",
        state="state-123",
        request_object="signed.request.jwt",
    )

    query_items = parse_qsl(urlsplit(authorization_url).query, keep_blank_values=True)
    assert query_items == [
        ("foo", "bar"),
        ("foo", "baz"),
        ("client_id", "client-123"),
        ("redirect_uri", "https://conformance.example.com/callback"),
        ("response_type", "code id_token"),
        ("scope", "openid accounts"),
        ("state", "state-123"),
        ("request", "signed.request.jwt"),
    ]


@pytest.mark.unit
def test_build_authorization_url_removes_endpoint_request_when_no_request_object() -> None:
    """An endpoint-supplied JAR request is not preserved when the step omits it."""
    authorization_url = build_authorization_url(
        endpoint="https://auth.example.com/authorize?request=stale.jwt&foo=bar",
        client_id="client-123",
        redirect_uri="https://conformance.example.com/callback",
        response_type="code id_token",
        scope="openid accounts",
        state="state-123",
    )

    query_items = parse_qsl(urlsplit(authorization_url).query, keep_blank_values=True)
    assert query_items == [
        ("foo", "bar"),
        ("client_id", "client-123"),
        ("redirect_uri", "https://conformance.example.com/callback"),
        ("response_type", "code id_token"),
        ("scope", "openid accounts"),
        ("state", "state-123"),
    ]
