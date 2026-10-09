"""Read/Write token authentication over a real, offline mutual-TLS connection."""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from conformance.catalogue import (
    CatalogueAssertion,
    CatalogueKey,
    CatalogueRequestStep,
    CatalogueTestCase,
    ImplementedEndpoint,
    RuntimeInputRequirement,
    SecurityProfileApplicability,
    TestCaseApplicability,
    TestCatalogue,
    TestPlanSpec,
    compile_test_plan,
)
from conformance.context import RuntimeConfig
from conformance.executor import run_compiled_test_plan, run_manifest
from conformance.http import build_json_http_client
from conformance.json_types import JsonObject
from conformance.manifest import parse_manifest
from conformance.model_bank_config import parse_model_bank_config
from tests.support.dcr_test_service import DcrFixtureMaterials

pytestmark = pytest.mark.component


@pytest.fixture
def registered_token_server(
    dcr_fixture_materials: DcrFixtureMaterials,
) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """Authenticate a pre-registered client by its certificate, never by JWT.

    A socket fixture is necessary here: mocked HTTP cannot prove that the
    product's SSL context presents the registered client certificate.
    """
    tls = dcr_fixture_materials.tls
    certificate = x509.load_pem_x509_certificate(tls.client_certificate_path.read_bytes())
    expected_certificate = certificate.public_bytes(Encoding.DER)
    forms: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - standard-library handler API
            assert isinstance(self.connection, ssl.SSLSocket)
            form = {
                name: values[0]
                for name, values in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()
            }
            forms.append(form)
            authenticated = (
                self.path == "/token"
                and self.connection.getpeercert(binary_form=True) == expected_certificate
                and form.get("client_id") == "externally-registered-client"
                and not {"client_assertion", "client_assertion_type", "client_secret"} & form.keys()
                and "Authorization" not in self.headers
            )
            payload = (
                {"access_token": "certificate-bound-fixture-token", "token_type": "Bearer"}
                if authenticated
                else {"error": "invalid_client"}
            )
            body = json.dumps(payload).encode()
            self.send_response(200 if authenticated else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - standard-library handler API
            assert self.headers["Authorization"] == "Bearer certificate-bound-fixture-token"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format_string: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(tls.server_certificate_path), str(tls.server_private_key_path))
    context.load_verify_locations(str(tls.ca_certificate_path))
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", forms
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _config(materials: DcrFixtureMaterials, origin: str) -> JsonObject:
    return {
        "oauth": {"clientId": "externally-registered-client", "tokenEndpoint": f"{origin}/token"},
        "fapiSigning": {"tokenEndpointAuthMethod": "tls_client_auth"},
        "tls": {
            "caBundlePath": str(materials.tls.ca_certificate_path),
            "clientCertificatePath": str(materials.tls.client_certificate_path),
            "clientPrivateKeyPath": str(materials.tls.client_private_key_path),
        },
    }


@pytest.mark.parametrize(
    "version,endpoint_version,update",
    [("3.1.11", "v3.1", "Release-5"), ("4.0.0", "v4.0", "Update-5"), ("4.0.1", "v4.0", "Update-1")],
)
def test_compiled_read_write_uses_registered_certificate_without_signing(
    registered_token_server: tuple[str, list[dict[str, str]]],
    dcr_fixture_materials: DcrFixtureMaterials,
    tmp_path: Path,
    version: str,
    endpoint_version: str,
    update: str,
) -> None:
    origin, forms = registered_token_server
    config = parse_model_bank_config(_config(dcr_fixture_materials, origin), base_dir=tmp_path)
    assert config.oauth is not None
    key = CatalogueKey("open-banking", endpoint_version, "ais", specification_version=version)
    path = f"/open-banking/{endpoint_version}/aisp/accounts"
    catalogue = TestCatalogue(
        key=key,
        catalogue_version="fixture",
        test_cases=(
            CatalogueTestCase(
                test_case_id="registered-client-resource",
                name="Resource",
                role="resource",
                compliance_scope=("FAPI 1 Advanced RFC 8705",),
                mandatory=True,
                applicability=TestCaseApplicability(security_profiles=SecurityProfileApplicability(profiles=("all",))),
                runtime_input_requirements=(
                    RuntimeInputRequirement(input_id="resourceBaseUrl", input_type="url", label="Resource base URL"),
                ),
                request_steps=(
                    CatalogueRequestStep(
                        step_id="resource-request",
                        name="Resource",
                        method="GET",
                        path=path,
                        runtime_input_refs=("resourceBaseUrl",),
                        required_token_id="ais-client-credentials",  # noqa: S106 - semantic token identifier, not a secret
                    ),
                ),
                assertions=(
                    CatalogueAssertion(
                        assertion_id="status", kind="http_status", description="Success", rule={"expected": 200}
                    ),
                ),
            ),
        ),
    )
    spec = TestPlanSpec(
        schema_version="v1",
        catalogue_key=key,
        security_profile="fapi1-advanced",
        implemented_endpoints=(ImplementedEndpoint(method="GET", path=path, resource_group="Accounts"),),
        runtime_inputs={"resourceBaseUrl": origin},
        openapi_document_update=update,
    )
    with build_json_http_client(
        ca_bundle=config.tls.ca_bundle,
        client_certificate=config.tls.client_certificate,
        client_private_key=config.tls.client_private_key,
    ) as client:
        result = run_compiled_test_plan(
            compile_test_plan(catalogue, spec),
            runtime_inputs=spec.runtime_inputs,
            runtime_input_base_dir=tmp_path,
            runtime_config=RuntimeConfig(
                oauth_client_id=config.oauth.client_id,
                oauth_token_endpoint=config.oauth.token_endpoint,
            ),
            fapi_signing_config=config.fapi_signing,
            mtls_client_configured=True,
            client=client,
        )
    output = tmp_path / "result.json"
    output.write_text(json.dumps(result.to_json_object()))
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "passed", evidence
    assert forms == [
        {
            "client_id": "externally-registered-client",
            "grant_type": "client_credentials",
            "scope": "accounts",
        }
    ]
    assert "certificate-bound-fixture-token" not in output.read_text()
    assert "PRIVATE KEY" not in output.read_text()


@pytest.mark.parametrize("client_id,status", [("externally-registered-client", "passed"), ("unknown-client", "failed")])
@pytest.mark.parametrize("inline", [False, True])
def test_authorization_code_exchange_authenticates_certificate(
    registered_token_server: tuple[str, list[dict[str, str]]],
    dcr_fixture_materials: DcrFixtureMaterials,
    tmp_path: Path,
    client_id: str,
    status: str,
    inline: bool,
) -> None:
    origin, forms = registered_token_server
    raw_config = _config(dcr_fixture_materials, origin)
    if inline:
        raw_config["tls"] = {
            "caBundlePem": dcr_fixture_materials.tls.ca_certificate_path.read_text(),
            "clientCertificatePem": dcr_fixture_materials.tls.client_certificate_path.read_text(),
            "clientPrivateKeyPem": dcr_fixture_materials.tls.client_private_key_path.read_text(),
        }
    config = parse_model_bank_config(raw_config, base_dir=tmp_path)
    manifest = parse_manifest(
        {
            "schemaVersion": "v1",
            "name": "Certificate-authenticated code exchange",
            "steps": [
                {
                    "id": "token",
                    "name": "Token",
                    "request": {
                        "method": "POST",
                        "url": f"{origin}/token",
                        "body": {
                            "encoding": "form",
                            "fields": {
                                "client_id": client_id,
                                "grant_type": "authorization_code",
                                "code": "fixture-code",
                                "redirect_uri": "https://client.example/callback",
                            },
                        },
                    },
                    "tokenEndpointAuthPolicy": {"source": "fapi-signing"},
                    "assertions": [{"type": "http_status", "expected": 200}],
                }
            ],
        }
    )
    with build_json_http_client(
        ca_bundle=config.tls.ca_bundle,
        client_certificate=config.tls.client_certificate,
        client_private_key=config.tls.client_private_key,
    ) as client:
        result = run_manifest(
            manifest, client=client, fapi_signing_config=config.fapi_signing, mtls_client_configured=True
        )
    output = tmp_path / "result.json"
    output.write_text(json.dumps(result.to_json_object()))
    assert json.loads(output.read_text())["status"] == status
    assert len(forms) == 1
    assert forms[0]["grant_type"] == "authorization_code"
    assert result.steps[0].status_code == (200 if status == "passed" else 401)
    assert "certificate-bound-fixture-token" not in output.read_text()


@pytest.mark.parametrize("invalid", ["malformed", "mismatched"])
def test_tls_client_auth_invalid_transport_credentials_fail_before_dispatch(
    registered_token_server: tuple[str, list[dict[str, str]]],
    dcr_fixture_materials: DcrFixtureMaterials,
    tmp_path: Path,
    invalid: str,
) -> None:
    origin, forms = registered_token_server
    raw_config = _config(dcr_fixture_materials, origin)
    tls = raw_config["tls"]
    assert isinstance(tls, dict)
    if invalid == "malformed":
        tls.pop("clientPrivateKeyPath")
        # pragma: allowlist nextline secret - deliberately invalid PEM, not key material
        tls["clientPrivateKeyPem"] = "-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----"
    else:
        tls["clientPrivateKeyPath"] = str(dcr_fixture_materials.tls.untrusted_client_private_key_path)
    config = parse_model_bank_config(raw_config, base_dir=tmp_path)
    with pytest.raises(ValueError, match="TLS|client certificate"):
        build_json_http_client(
            ca_bundle=config.tls.ca_bundle,
            client_certificate=config.tls.client_certificate,
            client_private_key=config.tls.client_private_key,
        )
    assert not forms
