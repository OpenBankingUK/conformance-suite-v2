"""Parsing, validation, and export of canonical and legacy v2 test-plan documents."""

from __future__ import annotations

import pytest

from conformance.catalogue import (
    CatalogueError,
    CatalogueKey,
    PlanDocumentV2,
    parse_test_plan_document,
    parse_test_plan_spec,
    plan_document_to_json_object,
)
from conformance.json_types import JsonValue

pytestmark = pytest.mark.unit

CATALOGUE_KEY = CatalogueKey(standard="open-banking", version="v4.0", api="ais")


def test_parse_v2_plan_derives_runtime_inputs_from_structured_config() -> None:
    """Structured v2 config keeps fixture data separate from runtime inputs."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {
                "resourceServer": {"baseUrl": "https://rs.example.com"},
                "ais": {
                    "resourceIds": {"accountIds": [{"accountId": "account-123"}]},
                    "transactionFromDate": "2026-01-01T00:00:00Z",
                    "transactionToDate": "2026-01-31T23:59:59Z",
                },
                "cbpii": {
                    "debtorAccount": {
                        "schemeName": "UK.OBIE.SortCodeAccountNumber",
                        "identification": "12345678901234",
                        "name": "Model Bank Account",
                    }
                },
            },
        }
    )

    assert isinstance(document, PlanDocumentV2)
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["consentedAccountId"] == "account-123"
    assert document.runtime_inputs["fromBookingDateTime"] == "2026-01-01T00:00:00Z"
    assert document.runtime_inputs["toBookingDateTime"] == "2026-01-31T23:59:59Z"
    assert document.runtime_inputs["debtorAccountSchemeName"] == "UK.OBIE.SortCodeAccountNumber"
    assert document.runtime_inputs["debtorAccountIdentification"] == "12345678901234"
    assert document.runtime_inputs["debtorAccountName"] == "Model Bank Account"


def test_parse_test_plan_spec_validates_exportable_json_shape() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "get",
                "path": "/open-banking/v4.0/aisp/accounts/",
                "resourceGroup": "Accounts",
                "operationId": "GetAccounts",
                "capabilities": ["accounts.balances"],
            }
        ],
        "runtimeInputs": {"resourceBaseUrl": "https://rs.example.com"},
        "deselectedTestCaseIds": ["optional-accounts-extension"],
        "assertionOverrides": [
            {
                "testCaseId": "accounts-read",
                "assertionId": "status-200",
                "reason": "Local non-certifying diagnostic",
            }
        ],
    }

    spec = parse_test_plan_spec(raw_spec)

    assert spec.catalogue_key == CATALOGUE_KEY
    assert spec.implemented_endpoints[0].method == "GET"
    assert spec.implemented_endpoints[0].path == "/open-banking/v4.0/aisp/accounts"
    assert spec.implemented_endpoints[0].capability_ids == ("accounts.balances",)
    assert spec.deselected_test_case_ids == ("optional-accounts-extension",)
    assert spec.assertion_overrides[0].assertion_id == "status-200"


def test_parse_test_plan_document_v2_serializes_nested_scope_and_config() -> None:
    """Legacy v2 plan documents serialize back to the canonical JSON-first shape."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v2",
        "scheme": "open-banking-uk",
        "specification": "read-write",
        "version": "4.0.1",
        "securityProfile": "fapi1-advanced",
        "scope": {
            "resourceGroups": [
                {
                    "id": "ais.accounts",
                    "label": "Accounts",
                    "endpoints": [
                        {
                            "method": "get",
                            "path": "/open-banking/v4.0/aisp/accounts/",
                            "capabilities": ["ais.accounts.list.core"],
                        }
                    ],
                }
            ]
        },
        "config": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://rs.example.com",
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        },
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.scheme == "open-banking-uk"
    assert document.specification == "read-write"
    assert document.version == "4.0.1"
    assert document.resource_groups[0].resource_group_id == "ais.accounts"
    assert document.resource_groups[0].endpoints[0].method == "GET"
    assert document.resource_groups[0].endpoints[0].path == "/open-banking/v4.0/aisp/accounts"
    assert document.resource_groups[0].endpoints[0].capability_ids == ("ais.accounts.list.core",)
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["accessToken"] == "secret-access-token"
    assert plan_document_to_json_object(document) == {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "certification",
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": [
            {
                "id": "AIS",
                "label": "Accounts",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/open-banking/v4.0/aisp/accounts",
                        "capabilities": ["ais.accounts.list.core"],
                    }
                ],
            }
        ],
        "businessTestData": {
            "runtimeInputs": {"resourceBaseUrl": "https://rs.example.com"},
        },
        "metadata": {},
    }


def test_legacy_v2_to_canonical_export_allows_missing_discovery_url() -> None:
    """Legacy documents can serialize manual security config without discovery."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {"resourceServer": {"baseUrl": "https://rs.example.com"}},
        }
    )

    assert isinstance(document, PlanDocumentV2)
    exported = plan_document_to_json_object(document)
    assert exported["securityEnvironment"] == {"resourceBaseUrl": "https://rs.example.com"}


def test_legacy_v2_to_canonical_export_omits_empty_mtls_block() -> None:
    """Legacy TLS config without recognised fields does not emit empty mTLS metadata."""
    document = parse_test_plan_document(
        {
            "schemaVersion": "v2",
            "scheme": "open-banking-uk",
            "specification": "read-write",
            "version": "4.0.1",
            "securityProfile": "fapi1-advanced",
            "scope": {"resourceGroups": []},
            "config": {
                "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
                "tls": {},
            },
        }
    )

    assert isinstance(document, PlanDocumentV2)
    exported = plan_document_to_json_object(document)
    security_environment = exported["securityEnvironment"]
    assert isinstance(security_environment, dict)
    assert "mtls" not in security_environment


def test_parse_canonical_plan_document_maps_prd_business_and_security_fields() -> None:
    """Canonical PRD-shaped test plans derive runner config and runtime inputs."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "name": "Primary Authorization Server",
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "clientAuthMethod": "private_key_jwt",
            "signingAlgorithm": "PS256",
            "resourceBaseUrl": "https://rs.example.com",
            "mtls": {"enabled": True, "certificatePath": "/absolute/path/transport.pem"},
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {
            "ais": {
                "accountIds": ["account-123"],
                "transactionFromDate": "2026-01-01T00:00:00Z",
            },
            "inputs": {"accessToken": {"value": "secret-access-token"}},
        },
        "metadata": {"aspspName": "Example Bank", "brandName": "Example Retail"},
        "executionMode": "development",
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.schema_version == "1.0"
    assert document.execution_mode == "development"
    assert document.security_profile == "fapi1-advanced"
    assert document.resource_groups[0].resource_group_id == "account-and-transaction"
    assert document.resource_groups[0].select_all is True
    assert document.config["discoveryUrl"] == "https://auth.example.com/.well-known/openid-configuration"
    assert document.config["resourceServer"] == {"baseUrl": "https://rs.example.com"}
    ais_config = document.config["ais"]
    assert isinstance(ais_config, dict)
    resource_ids = ais_config["resourceIds"]
    assert isinstance(resource_ids, dict)
    assert resource_ids["accountIds"] == [{"accountId": "account-123"}]
    assert document.runtime_inputs["resourceBaseUrl"] == "https://rs.example.com"
    assert document.runtime_inputs["consentedAccountId"] == "account-123"
    assert document.runtime_inputs["accessToken"] == "secret-access-token"


def test_parse_canonical_plan_document_rejects_security_timeout() -> None:
    """Canonical security environments do not accept configurable HTTP timeouts."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "timeoutSeconds": 60,
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match="Unknown testPlan.securityEnvironment field\\(s\\): timeoutSeconds"):
        parse_test_plan_document(raw_spec)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("openBankingIntentId", "consent-789"),
        ("acrValuesSupported", ["urn:openbanking:psd2:sca"]),
        ("jwksUri", "https://auth.example.com/jwks"),
        ("tppSignatureIssuer", "synthetic-org-id"),
        ("tppSignatureTan", "openbanking.org.uk"),
        ("xFapiFinancialId", "financial-id"),
        ("sendXFapiCustomerIpAddress", True),
        ("xFapiCustomerIpAddress", "203.0.113.10"),
    ],
)
def test_parse_canonical_plan_document_rejects_removed_security_fields(
    field_name: str,
    value: JsonValue,
) -> None:
    """Canonical security environments reject removed participant config fields."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            field_name: value,
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match=rf"Unknown testPlan.securityEnvironment field\(s\): {field_name}"):
        parse_test_plan_document(raw_spec)


def test_parse_canonical_plan_document_rejects_mtls_certificate_path_root() -> None:
    """Canonical mTLS config uses direct path fields rather than a shared root."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
        },
        "securityEnvironment": {
            "discoveryUrl": "https://auth.example.com/.well-known/openid-configuration",
            "mtls": {"certificatePathRoot": "/absolute/path/certs"},
        },
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(
        CatalogueError, match=r"Unknown testPlan.securityEnvironment\.mtls field\(s\): certificatePathRoot"
    ):
        parse_test_plan_document(raw_spec)


def test_parse_canonical_plan_document_requires_business_data_and_metadata() -> None:
    """Canonical parser enforces required empty-object sections without relying on JSON Schema."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {"family": "OBL_READ_WRITE", "version": "4.0.1"},
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
    }

    with pytest.raises(CatalogueError, match="testPlan.businessTestData is required"):
        parse_test_plan_document(raw_spec)

    raw_spec["businessTestData"] = {}
    with pytest.raises(CatalogueError, match="testPlan.metadata is required"):
        parse_test_plan_document(raw_spec)


def test_parse_canonical_plan_document_rejects_conflicting_profile_aliases() -> None:
    """Canonical specification profile aliases must not disagree."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
            "securityProfile": "FAPI2",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match="profile and testPlan.specification.securityProfile must match"):
        parse_test_plan_document(raw_spec)


def test_parse_canonical_plan_document_accepts_matching_profile_aliases() -> None:
    """Canonical specification profile aliases may both be supplied when equivalent."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI1_ADVANCED",
            "securityProfile": "fapi1-advanced",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    document = parse_test_plan_document(raw_spec)

    assert isinstance(document, PlanDocumentV2)
    assert document.security_profile == "fapi1-advanced"


def test_parse_canonical_plan_document_rejects_profile_not_declared_by_version() -> None:
    """Canonical Read/Write plans reject profiles not declared by the registry version."""
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "4.0.1",
            "profile": "FAPI2",
        },
        "securityEnvironment": {"discoveryUrl": "https://auth.example.com/.well-known/openid-configuration"},
        "resourceGroups": ["AIS"],
        "businessTestData": {},
        "metadata": {},
    }

    with pytest.raises(CatalogueError, match=r"profile must be one of: FAPI1_ADVANCED for OBL_READ_WRITE 4\.0\.1"):
        parse_test_plan_document(raw_spec)


def test_parse_plan_document_uses_neutral_root_for_schema_version_errors() -> None:
    """Plan-document schema-version errors do not refer to the legacy planSpec root."""
    with pytest.raises(CatalogueError, match="planDocument.schemaVersion"):
        parse_test_plan_document({"schemaVersion": "v99"})


def test_parse_test_plan_spec_rejects_duplicate_endpoint_capability_selection() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {
                "method": "GET",
                "path": "/accounts",
                "resourceGroup": "Accounts",
                "capabilities": ["accounts.balances", "accounts.balances"],
            }
        ],
    }

    with pytest.raises(CatalogueError, match="duplicates capability 'accounts.balances'"):
        parse_test_plan_spec(raw_spec)


def test_parse_test_plan_spec_rejects_duplicate_endpoint_selection() -> None:
    raw_spec: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "catalogue": {"standard": "open-banking", "version": "v4.0", "api": "ais"},
        "securityProfile": "fapi1-advanced",
        "implementedEndpoints": [
            {"method": "GET", "path": "/accounts", "resourceGroup": "Accounts"},
            {"method": "get", "path": "/accounts/", "resourceGroup": "Accounts"},
        ],
    }

    with pytest.raises(CatalogueError, match="duplicates implemented endpoint GET /accounts"):
        parse_test_plan_spec(raw_spec)
