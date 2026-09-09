"""Executor token-endpoint client authentication and signing-credential lifecycle."""

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import (
    parse_manifest,
)
from conformance.model_bank_config import FapiSigningConfig, TokenEndpointClientAuthMode
from conformance.signing_credentials import SigningCredentials, load_signing_credentials
from tests.support.executor_signing import executor_signing_config, invalid_executor_signing_config

pytestmark = pytest.mark.unit


def test_run_manifest_v1_private_key_jwt_token_auth_policy_adds_client_assertion(tmp_path: Path) -> None:
    """Token-endpoint auth policy appends private-key JWT form fields."""
    captured_form_body: dict[str, str] = {}
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt token auth",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                            "scope": "accounts",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_form_body
        captured_form_body = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert captured_form_body["grant_type"] == "authorization_code"
    assert captured_form_body["code"] == "auth-code"
    assert captured_form_body["redirect_uri"] == "https://app.example.com/callback"
    assert captured_form_body["client_id"] == "client-123"
    assert captured_form_body["scope"] == "accounts"
    assert captured_form_body["client_assertion_type"] == ("urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
    assert captured_form_body["client_assertion"]


@pytest.mark.parametrize(
    ("conflicting_fields", "expected_reserved_fields"),
    [
        pytest.param(
            {"client_assertion": "manifest-client-assertion"},
            "client_assertion",
            id="client-assertion",
        ),
        pytest.param(
            {
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            },
            "client_assertion_type",
            id="client-assertion-type",
        ),
        pytest.param(
            {
                "client_assertion": "manifest-client-assertion",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            },
            "client_assertion, client_assertion_type",
            id="both-fields",
        ),
    ],
)
def test_run_manifest_v1_private_key_jwt_token_auth_policy_rejects_manifest_client_assertion_fields(
    tmp_path: Path,
    conflicting_fields: dict[str, str],
    expected_reserved_fields: str,
) -> None:
    """Token-endpoint auth policy fails fast when manifests supply reserved assertion fields.

    Args:
        tmp_path: Pytest temporary directory used to hold signing credentials.
        conflicting_fields: Manifest-authored token form fields that must be
            rejected when runtime FAPI signing owns client authentication.
        expected_reserved_fields: Deterministic error-message suffix naming the
            conflicting form fields.
    """
    request_seen = False
    raw_form_fields: dict[str, JsonValue] = {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
        **conflicting_fields,
    }
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt token auth conflict",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": raw_form_fields,
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "Token endpoint auth policy reserves these form fields for runtime FAPI signing: "
        f"{expected_reserved_fields}"
    )
    assert dict(result.steps[0].details) == {
        "request": {
            "method": "POST",
            "url": "https://auth.example.com/token",
            "form": {
                "grant_type": "authorization_code",
                "code": "***",
                "redirect_uri": "https://app.example.com/callback",
                "client_id": "client-123",
                **{key: ("***" if key in {"client_assertion"} else value) for key, value in conflicting_fields.items()},
            },
        }
    }


def test_run_manifest_v1_private_key_jwt_token_auth_masks_client_assertion(tmp_path: Path) -> None:
    """Generated client assertions are masked in step evidence and log events."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "private-key-jwt masking",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }
    execution_logger = BufferedExecutionLogger(run_id="token-auth-run", developer_mode=False)
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"error": "invalid_client"}))
    ) as client:
        result = run_manifest(
            manifest,
            client=client,
            execution_logger=execution_logger,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    request_details = cast("dict[str, Any]", result.steps[0].details["request"])
    assert request_details["form"] == {
        "grant_type": "authorization_code",
        "code": "***",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": "***",
    }
    request_event = next(event for event in execution_logger.events() if event.type == "request-sent")
    assert request_event.payload["form"] == request_details["form"]


def test_run_manifest_reuses_signing_credentials_across_signed_http_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor loads runtime signing credentials once across signed HTTP steps.

    Args:
        tmp_path: Pytest temporary directory used to hold generated signing PEM files.
        monkeypatch: Fixture used to replace the credential loader with a counting wrapper.
    """
    load_count = 0
    real_loader = load_signing_credentials
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "shared signing credentials",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            },
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            },
        ],
    }

    def counting_loader(signing_config: FapiSigningConfig) -> SigningCredentials:
        """Count executor credential loads while delegating to the real loader.

        Args:
            signing_config: Validated signing configuration to load.

        Returns:
            Loaded runtime signing credentials.
        """
        nonlocal load_count
        load_count += 1
        return real_loader(signing_config)

    def handler(request: httpx.Request) -> httpx.Response:
        """Return passing responses for the signed consent and token steps.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing mock response for the requested endpoint.
        """
        if str(request.url) == "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents":
            return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})
        return httpx.Response(200, json={"access_token": "access-token"})

    monkeypatch.setattr("conformance.executor.load_signing_credentials", counting_loader)
    manifest = parse_manifest(raw_manifest)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert [step.name for step in result.steps] == ["consent", "token"]
    assert load_count == 1


def test_run_manifest_v1_unsigned_step_ignores_invalid_signing_credentials(tmp_path: Path) -> None:
    """Unsigned HTTP steps do not load invalid PEM credentials just because config exists.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "unsigned step with invalid signing config",
        "steps": [
            {
                "id": "accounts",
                "name": "Accounts list",
                "request": {
                    "method": "GET",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Return a passing response for the unsigned request.

        Args:
            _request: Outbound request emitted by the executor.

        Returns:
            Passing unsigned HTTP response.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"Data": []})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert request_seen is True


def test_run_manifest_v1_detached_jws_invalid_signing_credentials_fail_the_step(tmp_path: Path) -> None:
    """Detached-JWS steps translate invalid PEM files into a failed step result.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "detached jws invalid signing credentials",
        "steps": [
            {
                "id": "consent",
                "name": "Consent",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if the signed request reaches the transport.

        Args:
            _request: Outbound request that should never be dispatched.

        Returns:
            Dummy response if executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply request signing: fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    )


def test_run_manifest_v1_private_key_jwt_invalid_signing_credentials_fail_the_step(tmp_path: Path) -> None:
    """Private-key JWT token auth reports invalid PEM files as a failed step.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "token auth invalid signing credentials",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if the token request reaches the transport.

        Args:
            _request: Outbound request that should never be dispatched.

        Returns:
            Dummy response if executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=invalid_executor_signing_config(tmp_path),
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "fapiSigning.signingCertificatePath must contain a valid PEM certificate"
    )


def test_run_manifest_v1_tls_client_auth_ignores_invalid_signing_credentials_when_mtls_is_configured(
    tmp_path: Path,
) -> None:
    """TLS client auth does not load PEM credentials when mTLS is already configured.

    Args:
        tmp_path: Pytest temporary directory used to hold invalid signing PEM files.
    """
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls client auth invalid signing credentials",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Return a passing token response for the mTLS-authenticated request.

        Args:
            _request: Outbound request emitted by the executor.

        Returns:
            Passing token response.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(
        invalid_executor_signing_config(tmp_path),
        token_endpoint_auth_method=tls_client_auth_mode,
    )
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=tls_signing_config,
            mtls_client_configured=True,
        )

    assert result.status == "passed"
    assert request_seen is True


def test_run_manifest_v1_tls_client_auth_policy_requires_mtls_client(tmp_path: Path) -> None:
    """TLS client auth fails before dispatch when no mTLS client is configured."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls-client-auth missing mtls",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(executor_signing_config(tmp_path), token_endpoint_auth_method=tls_client_auth_mode)
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=tls_signing_config,
        )

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply token endpoint client authentication: "
        "Token endpoint auth policy requires a configured TLS client certificate and private key"
    )


def test_run_manifest_v1_tls_client_auth_policy_dispatches_with_configured_mtls(tmp_path: Path) -> None:
    """TLS client auth preserves the existing token form body when mTLS is present."""
    captured_form_body: dict[str, str] = {}
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "tls-client-auth configured",
        "steps": [
            {
                "id": "token",
                "name": "Token exchange",
                "request": {
                    "method": "POST",
                    "url": "https://auth.example.com/token",
                    "body": {
                        "encoding": "form",
                        "fields": {
                            "grant_type": "authorization_code",
                            "code": "auth-code",
                            "redirect_uri": "https://app.example.com/callback",
                            "client_id": "client-123",
                        },
                    },
                },
                "assertions": [{"type": "http_status", "expected": 200}],
                "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_form_body
        captured_form_body = dict(httpx.QueryParams(request.content.decode("utf-8")))
        return httpx.Response(200, json={"access_token": "access-token"})

    tls_client_auth_mode: TokenEndpointClientAuthMode = "tls_client_auth"
    tls_signing_config = replace(executor_signing_config(tmp_path), token_endpoint_auth_method=tls_client_auth_mode)
    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=tls_signing_config,
            mtls_client_configured=True,
        )

    assert result.status == "passed"
    assert captured_form_body == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": "https://app.example.com/callback",
        "client_id": "client-123",
    }
