"""Unit tests for runner config requirements derived from a compiled Read/Write plan."""

from __future__ import annotations

import pytest

from conformance.api.builder_wizard import _runtime_requirements_for_boundary, plan_document_with_runtime_placeholders
from conformance.catalogue import (
    PlanDocumentBoundary,
    PlanDocumentV2,
    compile_test_plan_document,
    parse_test_plan_document,
)
from conformance.catalogue_registry import supported_catalogues
from conformance.executor import compiled_plan_run_config_requirements
from conformance.run_config_requirements import missing_run_config, required_run_config_keys

pytestmark = pytest.mark.unit

_OAUTH_AND_SIGNING = {
    "fapiSigning",
    "oauth.authorizationEndpoint",
    "oauth.clientId",
    "oauth.issuer",
    "oauth.redirectUri",
    "oauth.tokenEndpoint",
    "resourceBaseUrl",
}


def _requirements_for(group: str) -> set[str]:
    """Compile one whole Read/Write resource group and derive its runner config.

    Args:
        group: Resource-group id.

    Returns:
        Derived requirement names.
    """
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_READ_WRITE",
                "version": "4.0.1",
                "profile": "FAPI1_ADVANCED",
                "openApiDocumentUpdate": "Update-1",
            },
            "securityEnvironment": {"discoveryUrl": "https://a.example.com/.well-known/openid-configuration"},
            "resourceGroups": [group],
            "businessTestData": {},
            "metadata": {},
        }
    )
    assert isinstance(document, PlanDocumentV2)
    boundary = PlanDocumentBoundary(document.scheme, document.specification, document.version)
    document = plan_document_with_runtime_placeholders(document, _runtime_requirements_for_boundary(boundary).values())
    compiled = compile_test_plan_document(document, supported_catalogues())
    return set(compiled_plan_run_config_requirements(compiled))


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("AIS", _OAUTH_AND_SIGNING | {"discoveryUrl"}),
        ("PIS", _OAUTH_AND_SIGNING | {"discoveryUrl"}),
        ("VRP", _OAUTH_AND_SIGNING | {"discoveryUrl"}),
        ("CBPII", _OAUTH_AND_SIGNING),
    ],
)
def test_compiled_plan_run_config_requirements_per_resource_group(group: str, expected: set[str]) -> None:
    """Each Read/Write resource group derives the OAuth, signing, and discovery values its steps use."""
    assert _requirements_for(group) == expected


def test_required_run_config_keys_adds_mtls_pair_only_for_tls_client_auth() -> None:
    """FAPI signing expands to its fields, plus the mTLS pair only for tls_client_auth."""
    keys = required_run_config_keys(["fapiSigning"], {})
    assert "fapiSigning.kid" in keys
    assert "tls.clientCertificate" not in keys

    tls_keys = required_run_config_keys(
        ["fapiSigning"], {}, security_environment={"clientAuthMethod": "tls_client_auth"}
    )
    assert {"tls.clientCertificate", "tls.clientPrivateKey"} <= set(tls_keys)


def test_missing_run_config_accepts_config_or_canonical_locations() -> None:
    """A value supplied in raw config or canonical securityEnvironment counts as present."""
    from_config = missing_run_config(["oauth.clientId"], {"oauth": {"clientId": "abc"}})
    from_environment = missing_run_config(["oauth.clientId"], {}, security_environment={"clientId": "abc"})
    missing = missing_run_config(["oauth.clientId"], {}, security_environment={"clientId": "  "})

    assert from_config == ()
    assert from_environment == ()
    assert [item.key for item in missing] == ["oauth.clientId"]
    assert missing[0].message.startswith("securityEnvironment.clientId is required to run because")
