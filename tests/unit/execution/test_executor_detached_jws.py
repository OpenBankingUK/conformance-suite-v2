"""Executor detached JWS request signing across the Open Banking write profiles."""

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from joserfc import jwk, jws

from conformance.execution_log import BufferedExecutionLogger
from conformance.executor import run_manifest
from conformance.json_types import JsonValue
from conformance.manifest import (
    parse_manifest,
)
from tests.support.executor_signing import executor_signing_config, response_signature_registry

pytestmark = pytest.mark.unit


def test_run_manifest_v1_account_access_consent_adds_masked_detached_jws_header(tmp_path: Path) -> None:
    """Consent creation signs exact JSON bytes and masks the detached JWS header."""
    observed_requests: list[httpx.Request] = []
    execution_logger = BufferedExecutionLogger(run_id="consent-signing-run", developer_mode=False)
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent signing",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "headers": {"Authorization": "Bearer access-token"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request and force a failing assertion."""
        observed_requests.append(request)
        return httpx.Response(400, json={"error": "invalid_request"})

    manifest = parse_manifest(raw_manifest)
    signing_config = executor_signing_config(tmp_path)
    expected_payload = b'{"Data":{"Permissions":["ReadAccountsBasic","ReadBalances"]},"Risk":{}}'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            execution_logger=execution_logger,
            fapi_signing_config=signing_config,
        )

    observed_request = observed_requests[0]
    detached_signature = observed_request.headers["x-jws-signature"]
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=observed_request.content,
    )

    assert result.status == "failed"
    assert observed_request.content == expected_payload
    assert detached_signature.split(".")[1] == ""
    assert verified.headers() == {
        "alg": "PS256",
        "kid": "executor-signing-key",
        "b64": False,
        "crit": ["b64"],
    }
    request_details = cast("dict[str, Any]", result.steps[0].details["request"])
    assert request_details["body"] == {
        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
        "Risk": {},
    }
    assert request_details["headers"] == {
        "Authorization": "***",
        "x-jws-signature": "***",
    }
    request_event = next(event for event in execution_logger.events() if event.type == "request-sent")
    assert request_event.payload["headers"] == request_details["headers"]


def test_run_manifest_v1_account_access_consent_adds_detached_jws_with_doubled_path_separator(
    tmp_path: Path,
) -> None:
    """Consent creation still signs when the resolved URL path contains doubled slashes."""
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent signing with doubled slash",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com//open-banking/v4.0/aisp/account-access-consents/",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request for doubled-slash URL coverage.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing consent response.
        """
        observed_requests.append(request)
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    expected_payload = b'{"Data":{"Permissions":["ReadAccountsBasic","ReadBalances"]},"Risk":{}}'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert observed_requests[0].content == expected_payload
    assert "x-jws-signature" in observed_requests[0].headers


def test_run_manifest_v1_account_access_consent_skips_detached_jws_without_manifest_opt_in(tmp_path: Path) -> None:
    """Consent creation stays unsigned unless the manifest request opts into detached JWS."""
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent without detached-jws",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/account-access-consents",
                    "headers": {"Authorization": "Bearer access-token"},
                    "body": {
                        "Data": {"Permissions": ["ReadAccountsBasic", "ReadBalances"]},
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound consent request for detached-JWS absence checks.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Passing consent response.
        """
        observed_requests.append(request)
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(
            manifest,
            client=client,
            fapi_signing_config=executor_signing_config(tmp_path),
        )

    assert result.status == "passed"
    assert "x-jws-signature" not in observed_requests[0].headers


def test_run_manifest_v1_pis_write_request_uses_ob_v4_detached_jws_profile(tmp_path: Path) -> None:
    """PIS v4 write requests use the OB v3.1.4+/v4 detached-JWS profile.

    Args:
        tmp_path: Pytest temporary directory used for signing material.
    """
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "pis consent signing",
        "steps": [
            {
                "id": "pis-v4-domestic-payment-consent-create-request",
                "name": "Domestic payment consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/pisp/domestic-payment-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "headers": {"Authorization": "******"},
                    "body": {
                        "Data": {
                            "Initiation": {
                                "InstructionIdentification": "FCSV2DomesticPaymentInstruction",
                                "EndToEndIdentification": "FCSV2DomesticPaymentEndToEnd",
                                "InstructedAmount": {"Amount": "1.00", "Currency": "GBP"},
                                "CreditorAccount": {
                                    "SchemeName": "UK.OBIE.SortCodeAccountNumber",
                                    "Identification": "70000170000002",
                                    "Name": "Domestic creditor",
                                },
                            }
                        },
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound PIS request and force a failing assertion."""
        observed_requests.append(request)
        return httpx.Response(400, json={"error": "invalid_request"})

    signing_config = executor_signing_config(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(parse_manifest(raw_manifest), client=client, fapi_signing_config=signing_config)

    observed_request = observed_requests[0]
    detached_signature = observed_request.headers["x-jws-signature"]
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=observed_request.content,
        registry=response_signature_registry(),
    )

    assert result.status == "failed"
    assert detached_signature.split(".")[1] == ""
    headers = verified.headers()
    assert headers["alg"] == "PS256"
    assert headers["kid"] == "executor-signing-key"
    assert headers["typ"] == "JOSE"
    assert headers["cty"] == "application/json"
    assert headers["http://openbanking.org.uk/iss"] == "client-issuer"
    assert headers["http://openbanking.org.uk/tan"] == "openbanking.org.uk"
    assert isinstance(headers["http://openbanking.org.uk/iat"], int)
    assert headers["crit"] == [
        "http://openbanking.org.uk/iat",
        "http://openbanking.org.uk/iss",
        "http://openbanking.org.uk/tan",
    ]
    assert "b64" not in headers
    assert verified.payload == observed_request.content


def test_run_manifest_v1_pis_write_request_can_omit_ob_v4_detached_jws_iss_claim(tmp_path: Path) -> None:
    """PIS negative tests can emit a valid OB v4 detached JWS without iss.

    Args:
        tmp_path: Pytest temporary directory used for signing material.
    """
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "pis consent missing signature claim",
        "steps": [
            {
                "id": "pis-v4-domestic-payment-consent-reject-missing-signature-claim-request",
                "name": "Domestic payment consent missing signature claim",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/pisp/domestic-payment-consents",
                    "detachedJws": {"source": "fapi-signing", "omitProtectedHeaders": ["iss"]},
                    "headers": {"Authorization": "******"},
                    "body": {"Data": {"Initiation": {"InstructionIdentification": "FCSV2"}}, "Risk": {}},
                },
                "assertions": [{"type": "http_status", "expected": 400}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound PIS request and return the expected rejection."""
        observed_requests.append(request)
        return httpx.Response(400, json={"Code": "UK.OBIE.Signature.MissingClaim"})

    signing_config = executor_signing_config(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(parse_manifest(raw_manifest), client=client, fapi_signing_config=signing_config)

    observed_request = observed_requests[0]
    detached_signature = observed_request.headers["x-jws-signature"]
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=observed_request.content,
        registry=response_signature_registry(),
    )

    assert result.status == "passed"
    headers = verified.headers()
    assert "http://openbanking.org.uk/iss" not in headers
    assert headers["crit"] == [
        "http://openbanking.org.uk/iat",
        "http://openbanking.org.uk/tan",
    ]
    assert verified.payload == observed_request.content


def test_run_manifest_v1_vrp_consent_request_uses_ob_v4_detached_jws_profile(tmp_path: Path) -> None:
    """VRP consent creation accepts detached JWS signing on generated resource paths.

    Args:
        tmp_path: Pytest temporary directory used for signing material.
    """
    observed_requests: list[httpx.Request] = []
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "vrp consent signing",
        "steps": [
            {
                "id": "vrp-consent-create-awaiting-authorisation-v4-request",
                "name": "VRP consent creation",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/pisp/domestic-vrp-consents",
                    "detachedJws": {"source": "fapi-signing"},
                    "headers": {"Authorization": "******"},
                    "body": {
                        "Data": {
                            "VRPType": "UK.OBIE.VRPType.Sweeping",
                            "ControlParameters": {
                                "ValidFromDateTime": "2026-08-27T00:00:00+00:00",
                                "ValidToDateTime": "2026-09-27T00:00:00+00:00",
                            },
                            "Initiation": {
                                "CreditorAccount": {
                                    "SchemeName": "UK.OBIE.SortCodeAccountNumber",
                                    "Identification": "70000170000002",
                                    "Name": "VRP creditor",
                                }
                            },
                        },
                        "Risk": {},
                    },
                },
                "assertions": [{"type": "http_status", "expected": 201}],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outbound VRP request and return an authorised consent.

        Args:
            request: Outbound HTTP request emitted by the executor.

        Returns:
            Successful VRP consent response.
        """
        observed_requests.append(request)
        return httpx.Response(201, json={"Data": {"ConsentId": "vrp-consent-123"}, "Risk": {}})

    signing_config = executor_signing_config(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(parse_manifest(raw_manifest), client=client, fapi_signing_config=signing_config)

    observed_request = observed_requests[0]
    detached_signature = observed_request.headers["x-jws-signature"]
    verified = jws.deserialize_compact(
        detached_signature,
        jwk.import_key(signing_config.signing_certificate_path.read_bytes(), key_type="RSA"),
        algorithms=["PS256"],
        payload=observed_request.content,
        registry=response_signature_registry(),
    )

    assert result.status == "passed"
    headers = verified.headers()
    assert headers["typ"] == "JOSE"
    assert headers["cty"] == "application/json"
    assert headers["http://openbanking.org.uk/iss"] == "client-issuer"
    assert verified.payload == observed_request.content


def test_run_manifest_v1_detached_jws_policy_requires_signing_config() -> None:
    """Explicit detached-JWS opt-in fails before dispatch when signing config is absent."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "account access consent missing signing config",
        "steps": [
            {
                "id": "account-access-consent",
                "name": "Account access consent creation",
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
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if an unsigned request reaches the transport.

        Args:
            _request: Outbound request that should never be sent.

        Returns:
            Dummy response if the executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(201, json={"Data": {"ConsentId": "consent-123"}, "Risk": {}})

    manifest = parse_manifest(raw_manifest)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_manifest(manifest, client=client)

    assert result.status == "failed"
    assert request_seen is False
    assert result.steps[0].message == (
        "Unable to apply request signing: Detached request signing requires fapiSigning configuration"
    )


def test_run_manifest_v1_detached_jws_policy_rejects_unsupported_url(tmp_path: Path) -> None:
    """Explicit detached-JWS opt-in fails before dispatch on unsupported endpoints."""
    request_seen = False
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "accounts list with detached-jws",
        "steps": [
            {
                "id": "accounts-list",
                "name": "Accounts list",
                "request": {
                    "method": "POST",
                    "url": "https://resource.example.com/open-banking/v4.0/aisp/accounts",
                    "detachedJws": {"source": "fapi-signing"},
                    "body": {"Data": {"Example": True}},
                },
                "assertions": [{"type": "http_status", "expected": 200}],
            }
        ],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        """Fail the test if an unsupported signed request reaches transport.

        Args:
            _request: Outbound request that should never be sent.

        Returns:
            Dummy response if the executor misbehaves.
        """
        nonlocal request_seen
        request_seen = True
        return httpx.Response(200, json={"Data": {}})

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
        "Unable to apply request signing: "
        "Detached request signing is only supported for AIS consent, PIS, and VRP write requests"
    )
