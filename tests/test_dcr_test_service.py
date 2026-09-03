"""Direct tests for the deterministic DCR protocol service."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx
import pytest

from conformance.json_types import JsonObject, JsonValue
from tests.dcr_test_service import DcrTestService


def _register(
    service: DcrTestService,
    client: httpx.Client,
    *,
    auth_method: str = "tls_client_auth",
    overrides: Mapping[str, JsonValue] | None = None,
    algorithm: str = "PS256",
) -> JsonObject:
    """Register one fixture client and return its JSON response.

    Args:
        service: Running deterministic service.
        client: Trusted mTLS HTTP client.
        auth_method: Token endpoint auth method to register.
        overrides: Optional registration claim changes.
        algorithm: Compact JOSE signing algorithm.

    Returns:
        Parsed successful registration response.
    """
    response = client.post(
        "/register",
        content=service.sign_registration(
            token_endpoint_auth_method=auth_method,
            overrides=overrides,
            algorithm=algorithm,
        ),
        headers={"Content-Type": "application/jose", "Accept": "application/json"},
    )
    assert response.status_code == 201, response.text
    return cast(JsonObject, response.json())


def _token(
    service: DcrTestService,
    client: httpx.Client,
    registration: Mapping[str, JsonValue],
) -> str:
    """Request and return the deterministic client-credentials token.

    Args:
        service: Running deterministic service.
        client: Trusted mTLS HTTP client.
        registration: Registration response used to build authentication.

    Returns:
        Issued bearer token.
    """
    request = service.token_request(registration)
    response = client.post("/token", data=request.form, headers=request.headers)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


@pytest.mark.integration
def test_discovery_requires_trusted_mtls_and_advertises_protocol(
    dcr_test_service: DcrTestService,
) -> None:
    """Discovery is reachable only with a certificate signed by the fixture CA."""
    with dcr_test_service.client() as client:
        response = client.get("/.well-known/openid-configuration")

    assert response.status_code == 200
    assert response.json() == {
        "issuer": dcr_test_service.base_url,
        "jwks_uri": f"{dcr_test_service.base_url}/jwks",
        "registration_endpoint": dcr_test_service.registration_endpoint,
        "token_endpoint": dcr_test_service.token_endpoint,
        "token_endpoint_auth_methods_supported": [
            "tls_client_auth",
            "private_key_jwt",
            "client_secret_jwt",
            "client_secret_basic",
        ],
        "token_endpoint_auth_signing_alg_values_supported": ["PS256", "HS256"],
        "registration_management_methods_supported": ["DELETE", "GET", "PUT"],
    }
    assert dcr_test_service.events[-1].mtls_verified is True

    with (
        dcr_test_service.client(trusted_client_certificate=False) as client,
        pytest.raises(httpx.TransportError),
    ):
        client.get("/.well-known/openid-configuration")
    with dcr_test_service.untrusted_client() as client, pytest.raises(httpx.TransportError):
        client.get("/.well-known/openid-configuration")


@pytest.mark.integration
def test_registration_accepts_raw_ps256_and_produces_deterministic_state(
    dcr_test_service: DcrTestService,
) -> None:
    """Raw application/jose registration returns complete stable client metadata."""
    compact_jose = dcr_test_service.sign_registration()
    assert compact_jose.count(".") == 2
    with dcr_test_service.client() as client:
        response = client.post(
            "/register",
            content=compact_jose,
            headers={"Content-Type": "application/jose", "Accept": "application/json"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == "fixture-client-0001"
    assert body["registration_client_uri"] == f"{dcr_test_service.registration_endpoint}/fixture-client-0001"
    assert body["token_endpoint_auth_method"] == "tls_client_auth"  # noqa: S105 - protocol enum, not a credential.
    assert body["client_secret_expires_at"] == 0
    assert dcr_test_service.snapshot() == {
        "clients": [
            {
                "client_id": "fixture-client-0001",
                "deleted": False,
                "token_issued": False,
                "token_endpoint_auth_method": "tls_client_auth",
            }
        ]
    }
    event = dcr_test_service.events[-1]
    assert (event.method, event.path, event.status_code, event.content_type, event.error) == (
        "POST",
        "/register",
        201,
        "application/jose",
        None,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"exp": 1_799_999_999}, "invalid_software_statement"),
        ({"iss": "foo.is/invalid"}, "invalid_software_statement"),
        ({"iss": ""}, "invalid_software_statement"),
        ({"iss": "123456789012345678901234567890"}, "invalid_software_statement"),
        ({"aud": "https://wrong.example.test/register"}, "invalid_software_statement"),
        ({"software_statement": "not-the-fixture-ssa"}, "invalid_software_statement"),
        ({"token_endpoint_auth_signing_alg": "RS256"}, "invalid_client_metadata"),
        ({"response_types": ["id_token", "token"]}, "invalid_client_metadata"),
        ({"application_type": "mobile"}, "invalid_client_metadata"),
        ({"redirect_uris": ["http://client.example.test/callback"]}, "invalid_redirect_uri"),
        ({"tls_client_auth_subject_dn": ""}, "invalid_client_metadata"),
    ],
)
def test_registration_rejects_invalid_claim_boundaries(
    dcr_test_service: DcrTestService,
    overrides: Mapping[str, JsonValue],
    expected_error: str,
) -> None:
    """Invalid DCR claims return exact deterministic 400 error categories."""
    with dcr_test_service.client() as client:
        response = client.post(
            "/register",
            content=dcr_test_service.sign_registration(overrides=overrides),
            headers={"Content-Type": "application/jose"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == expected_error
    assert dcr_test_service.snapshot() == {"clients": []}


@pytest.mark.integration
@pytest.mark.parametrize("auth_method", ["client_secret_post", "none", "unknown"])
def test_registration_rejects_unexecutable_auth_methods(
    dcr_test_service: DcrTestService,
    auth_method: str,
) -> None:
    """Discovery and registration share the four executable auth boundaries."""
    with dcr_test_service.client() as client:
        response = client.post(
            "/register",
            content=dcr_test_service.sign_registration(token_endpoint_auth_method=auth_method),
            headers={"Content-Type": "application/jose"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.integration
def test_registration_rejects_transport_and_jose_errors(dcr_test_service: DcrTestService) -> None:
    """Wrong media types, malformed JOSE, and non-PS256 JOSE fail exactly."""
    with dcr_test_service.client() as client:
        wrong_media = client.post("/register", content=dcr_test_service.sign_registration())
        malformed = client.post(
            "/register",
            content="not.compact.jose",
            headers={"Content-Type": "application/jose"},
        )
        rs256 = client.post(
            "/register",
            content=dcr_test_service.sign_registration(algorithm="RS256"),
            headers={"Content-Type": "application/jose"},
        )

    assert (wrong_media.status_code, wrong_media.json()["error"]) == (415, "invalid_request")
    assert (malformed.status_code, malformed.json()["error"]) == (400, "invalid_software_statement")
    assert (rs256.status_code, rs256.json()["error"]) == (400, "invalid_software_statement")


@pytest.mark.integration
@pytest.mark.parametrize(
    "auth_method",
    ["tls_client_auth", "private_key_jwt", "client_secret_jwt", "client_secret_basic"],
)
def test_supported_token_auth_methods_issue_repeatable_client_credentials_token(
    dcr_test_service: DcrTestService,
    auth_method: str,
) -> None:
    """Every advertised executable auth method obtains the same stable token."""
    with dcr_test_service.client() as client:
        registration = _register(dcr_test_service, client, auth_method=auth_method)
        request = dcr_test_service.token_request(registration)
        first = client.post("/token", data=request.form, headers=request.headers)
        second = client.post("/token", data=request.form, headers=request.headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["access_token"] == second.json()["access_token"]
    assert first.headers["cache-control"] == "no-store"
    snapshot_clients = cast(list[JsonObject], dcr_test_service.snapshot()["clients"])
    assert snapshot_clients[0]["token_issued"] is True


@pytest.mark.integration
def test_token_endpoint_rejects_wrong_grant_credentials_and_client_secret_post(
    dcr_test_service: DcrTestService,
) -> None:
    """Token errors distinguish invalid grants from invalid authentication."""
    with dcr_test_service.client() as client:
        registration = _register(dcr_test_service, client, auth_method="client_secret_basic")
        client_id = registration["client_id"]
        client_secret = registration["client_secret"]
        wrong_grant = client.post("/token", data={"grant_type": "authorization_code"})
        secret_post = client.post(
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        unknown = client.post(
            "/token",
            data={"grant_type": "client_credentials", "client_id": "fixture-client-9999"},
        )

    assert (wrong_grant.status_code, wrong_grant.json()["error"]) == (400, "unsupported_grant_type")
    assert (secret_post.status_code, secret_post.json()["error"]) == (401, "invalid_client")
    assert (unknown.status_code, unknown.json()["error"]) == (401, "invalid_client")


@pytest.mark.integration
def test_management_get_put_delete_and_deleted_client_transitions(
    dcr_test_service: DcrTestService,
) -> None:
    """Management calls update state then return 401 after deterministic deletion."""
    with dcr_test_service.client() as client:
        registration = _register(dcr_test_service, client)
        token = _token(dcr_test_service, client, registration)
        client_id = registration["client_id"]
        assert isinstance(client_id, str)
        headers = {"Authorization": f"Bearer {token}"}
        retrieved = client.get(f"/register/{client_id}", headers=headers)
        updated = client.put(
            f"/register/{client_id}",
            content=dcr_test_service.sign_registration(
                overrides={"redirect_uris": ["https://client.example.test/updated-callback"]}
            ),
            headers={**headers, "Content-Type": "application/jose", "Accept": "application/json"},
        )
        deleted = client.delete(f"/register/{client_id}", headers=headers)
        get_deleted = client.get(f"/register/{client_id}", headers=headers)
        put_deleted = client.put(
            f"/register/{client_id}",
            content=dcr_test_service.sign_registration(),
            headers={**headers, "Content-Type": "application/jose"},
        )
        delete_deleted = client.delete(f"/register/{client_id}", headers=headers)
        get_unknown = client.get("/register/fixture-client-9999", headers=headers)

    assert retrieved.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["redirect_uris"] == ["https://client.example.test/updated-callback"]
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert [response.status_code for response in (get_deleted, put_deleted, delete_deleted, get_unknown)] == [
        401,
        401,
        401,
        401,
    ]
    snapshot_clients = cast(list[JsonObject], dcr_test_service.snapshot()["clients"])
    assert snapshot_clients[0]["deleted"] is True


@pytest.mark.integration
@pytest.mark.parametrize("authorization", [None, "Bearer", "Bearer wrong-fixture-token"])
def test_management_rejects_missing_empty_or_wrong_bearer(
    dcr_test_service: DcrTestService,
    authorization: str | None,
) -> None:
    """Management requests require the issued client-credentials bearer token."""
    with dcr_test_service.client() as client:
        registration = _register(dcr_test_service, client)
        _token(dcr_test_service, client, registration)
        client_id = registration["client_id"]
        assert isinstance(client_id, str)
        headers = {} if authorization is None else {"Authorization": authorization}
        response = client.get(f"/register/{client_id}", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


@pytest.mark.integration
def test_optional_management_methods_are_observable_and_deterministic(tmp_path: Path) -> None:
    """Disabled optional methods advertise and return stable 405 boundaries."""
    service = DcrTestService(tmp_path / "optional-service", management_methods=frozenset({"GET"}))
    with service, service.client() as client:
        discovery = client.get("/.well-known/openid-configuration")
        registration = _register(service, client)
        token = _token(service, client, registration)
        client_id = registration["client_id"]
        assert isinstance(client_id, str)
        headers = {"Authorization": f"Bearer {token}"}
        get_response = client.get(f"/register/{client_id}", headers=headers)
        put_response = client.put(f"/register/{client_id}", headers=headers)
        delete_response = client.delete(f"/register/{client_id}", headers=headers)

    assert discovery.json()["registration_management_methods_supported"] == ["GET"]
    assert get_response.status_code == 200
    assert put_response.status_code == delete_response.status_code == 405
    assert put_response.headers["allow"] == "GET"
    assert service.events[-1].error == "method_not_allowed"


@pytest.mark.integration
def test_reset_isolates_scenario_state_and_event_history(dcr_test_service: DcrTestService) -> None:
    """Explicit reset prevents state or identifiers leaking across scenarios."""
    with dcr_test_service.client() as client:
        first = _register(dcr_test_service, client)
        assert dcr_test_service.events
        dcr_test_service.reset()
        second = _register(dcr_test_service, client)

    assert first["client_id"] == second["client_id"] == "fixture-client-0001"
    assert len(dcr_test_service.events) == 1
