"""Component tests that exercise DCR over the loopback mTLS listener.

Everything else about DCR protocol and orchestration behaviour runs in process
through :class:`~tests.support.dcr_test_service.DcrProtocolService`. These two
cases are kept on a real TLS listener because the transport itself — fixture CA
trust, client-certificate verification, and the product's own mTLS client
configuration — is what they assert on.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.support.dcr_execution import build_adapter
from tests.support.dcr_test_service import DcrTestService

pytestmark = pytest.mark.component


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


def test_post_primitives_execute_raw_jose_token_and_negative_variants(
    dcr_test_service: DcrTestService,
    tmp_path: Path,
) -> None:
    """POST-only execution passes discovery, registration, token, and negatives.

    This is the one adapter run kept on the loopback listener: the adapter
    builds its own client from the participant's configured certificate, key,
    and CA bundle, so it proves the product's real mTLS client configuration
    completes a full DCR exchange.
    """
    result = build_adapter(dcr_test_service, tmp_path).run()

    assert result.status == "passed"
    compiled_plan = result.compiled_plan
    assert compiled_plan is not None
    assert len(result.steps) == sum(len(case.execution_steps) for case in compiled_plan.test_cases)
    assert all(step.status == "passed" for step in result.steps)
    registration_events = [event for event in dcr_test_service.events if event.path == "/register"]
    assert registration_events
    assert all(event.content_type == "application/jose" for event in registration_events)
    assert all(event.mtls_verified for event in dcr_test_service.events)
    assert any(
        event.path == "/token" and event.content_type == "application/x-www-form-urlencoded"
        for event in dcr_test_service.events
    )
