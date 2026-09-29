"""DCR registration response validation against the Open Banking DCR 3.4 cardinality rules."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from joserfc import jwt

from conformance.dcr_execution import (
    DcrCatalogueExecutionAdapter,
    DcrExecutionError,
    DcrScenarioState,
)
from conformance.json_types import JsonObject, JsonValue
from tests.support.dcr_execution import build_adapter as _adapter
from tests.support.dcr_test_service import DcrProtocolService

pytestmark = pytest.mark.unit

_MANDATORY_POST_CASE_IDS = (
    "DCR-001-C01",
    "DCR-002-C01",
    "DCR-002-C02",
    "DCR-004-C01",
    "DCR-004-C02",
    "DCR-004-C03",
    "DCR-004-C04",
    "DCR-004-C05",
    "DCR-011-C01",
)
_MANDATORY_POST_STEP_IDS = (
    "DCR-001-C01-S01",
    "DCR-002-C01-S01",
    "DCR-002-C01-S02",
    "DCR-002-C01-S03",
    "DCR-002-C01-S04",
    "DCR-002-C02-S01",
    "DCR-004-C01-S01",
    "DCR-004-C01-S02",
    "DCR-004-C01-S03",
    "DCR-004-C02-S01",
    "DCR-004-C02-S02",
    "DCR-004-C02-S03",
    "DCR-004-C03-S01",
    "DCR-004-C03-S02",
    "DCR-004-C03-S03",
    "DCR-004-C04-S01",
    "DCR-004-C04-S02",
    "DCR-004-C04-S03",
    "DCR-004-C05-S01",
    "DCR-004-C05-S02",
    "DCR-004-C05-S03",
    "DCR-011-C01-S01",
    "DCR-011-C01-S02",
    "DCR-011-C01-S03",
    "DCR-011-C01-S04",
)


def _registration_validation_fixture(
    service: DcrProtocolService,
    root: Path,
    *,
    request_redirects: list[str] | None = None,
    request_grants: list[str] | None = None,
    request_response_types: list[str] | None = None,
) -> tuple[DcrCatalogueExecutionAdapter, JsonObject, DcrScenarioState]:
    """Build response-validation state with a two-URI SSA master set.

    Args:
        service: Running deterministic DCR service.
        root: Directory receiving runtime references.
        request_redirects: Redirect URIs requested by the client.
        request_grants: Grant types requested by the client.
        request_response_types: Response types requested by the client.

    Returns:
        Adapter, valid response body, and corresponding scenario state.
    """
    master_redirects = [
        "https://client.example.test/callback",
        "https://client.example.test/alternate-callback",
    ]
    software_statement = jwt.encode(
        {"alg": "PS256", "kid": "fixture-signing-key"},
        {
            "software_id": "fixturesoftwareid",
            "software_redirect_uris": master_redirects,
        },
        service.protocol.signing_key,
        algorithms=["PS256"],
    )
    with service.client() as client:
        adapter = _adapter(service, root, client=client)
        adapter._require_discovery()  # noqa: SLF001 - focused validation-unit fixture.
        _, claims = adapter.build_registration_jose()
    effective_redirects = request_redirects or master_redirects[:1]
    effective_grants = request_grants or ["client_credentials", "authorization_code"]
    effective_response_types = request_response_types or ["code id_token"]
    claims["software_statement"] = software_statement
    claims["redirect_uris"] = cast("JsonValue", effective_redirects)
    claims["grant_types"] = cast("JsonValue", effective_grants)
    claims["response_types"] = cast("JsonValue", effective_response_types)
    body: JsonObject = {
        "client_id": "response-semantics-client",
        "client_secret": "response-semantics-secret",  # pragma: allowlist secret
        "client_id_issued_at": 1_800_000_000,
        "client_secret_expires_at": 0,
        "registration_access_token": "response-semantics-registration-token",
        "registration_client_uri": f"{service.registration_endpoint}/response-semantics-client",
        "application_type": claims["application_type"],
        "redirect_uris": cast("JsonValue", list(effective_redirects)),
        "grant_types": cast("JsonValue", list(effective_grants)),
        "response_types": cast("JsonValue", list(effective_response_types)),
        "scope": claims["scope"],
        "software_statement": software_statement,
        "software_id": "fixturesoftwareid",
        "id_token_signed_response_alg": claims["id_token_signed_response_alg"],
        "request_object_signing_alg": claims["request_object_signing_alg"],
        "token_endpoint_auth_method": claims["token_endpoint_auth_method"],
        "tls_client_auth_subject_dn": claims["tls_client_auth_subject_dn"],
    }
    return adapter, body, DcrScenarioState(registration_claims=claims)


def test_registration_response_array_order_is_semantically_irrelevant(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
) -> None:
    """Reordered set-valued registration metadata remains conformant."""
    redirects = [
        "https://client.example.test/callback",
        "https://client.example.test/alternate-callback",
    ]
    adapter, body, state = _registration_validation_fixture(
        dcr_protocol_service,
        tmp_path,
        request_redirects=redirects,
        request_response_types=["code", "code id_token"],
    )
    body["redirect_uris"] = list(reversed(redirects))
    body["grant_types"] = ["authorization_code", "client_credentials"]
    body["response_types"] = ["code id_token", "code"]

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


def test_registration_response_accepts_omitted_optional_issuance_and_secret_fields(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
) -> None:
    """Legacy-optional issuance timestamps and client secret may be omitted."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    for field in ("client_id_issued_at", "client_secret_expires_at", "client_secret"):
        body.pop(field)

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


def test_registration_response_allows_rfc7592_management_fields_to_be_omitted(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
) -> None:
    """Open Banking responses need not include RFC 7592 management credentials."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body.pop("registration_access_token")
    body.pop("registration_client_uri")

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("client_id_issued_at", "1800000000"),
        ("client_id_issued_at", True),
        ("client_secret_expires_at", "0"),
        ("client_secret_expires_at", False),
        ("client_secret", None),
        ("client_secret", 7),
        ("client_secret", ""),
        ("client_secret", "s" * 37),
    ],
)
def test_registration_response_rejects_invalid_present_optional_credential_fields(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
    field: str,
    replacement: JsonValue,
) -> None:
    """Present optional issuance and secret fields retain type and size rules."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body[field] = replacement

    with pytest.raises(DcrExecutionError, match=field):
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )


@pytest.mark.parametrize(
    ("field", "permitted"),
    [
        ("redirect_uris", False),
        ("grant_types", False),
        ("response_types", True),
    ],
)
def test_registration_response_array_omission_follows_dcr_occurrence(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
    field: str,
    permitted: bool,
) -> None:
    """Only the optional DCR response-types array may be omitted."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body.pop(field)

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


def test_registration_response_nonempty_array_subsets_are_permitted(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
) -> None:
    """Supported subsets describe the metadata actually registered by the AS."""
    adapter, body, state = _registration_validation_fixture(
        dcr_protocol_service,
        tmp_path,
        request_redirects=[
            "https://client.example.test/callback",
            "https://client.example.test/alternate-callback",
        ],
        request_response_types=["code", "code id_token"],
    )
    body["redirect_uris"] = ["https://client.example.test/callback"]
    body["grant_types"] = ["client_credentials"]
    body["response_types"] = ["code"]

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


def test_registration_response_allows_software_statement_replacement(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
) -> None:
    """The returned software statement is validated by shape, not request equality."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body["software_statement"] = "server-registered-software-statement"

    adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
        body,
        state,
        require_consistency=True,
    )


@pytest.mark.parametrize(
    ("field", "permitted"),
    [
        ("redirect_uris", False),
        ("grant_types", False),
        ("response_types", True),
    ],
)
def test_registration_response_empty_array_subsets_follow_dcr_cardinality(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
    field: str,
    permitted: bool,
) -> None:
    """Empty subsets are accepted only for the zero-cardinality response field."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body[field] = []

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


@pytest.mark.parametrize(
    ("field", "replacement", "permitted"),
    [
        ("redirect_uris", ["https://client.example.test/alternate-callback"], True),
        ("redirect_uris", ["https://outside-ssa.example.test/callback"], False),
        ("grant_types", ["urn:openid:params:grant-type:ciba"], True),
        ("grant_types", ["urn:ietf:params:oauth:grant-type:jwt-bearer"], True),
        ("grant_types", ["unsupported_grant"], False),
        ("response_types", ["code"], True),
        ("response_types", ["token"], False),
    ],
)
def test_registration_response_array_replacement_follows_normative_constraints(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
    field: str,
    replacement: list[str],
    permitted: bool,
) -> None:
    """AS replacements are accepted only within DCR values and the SSA URI set."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body[field] = cast("JsonValue", replacement)

    if permitted:
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
    else:
        with pytest.raises(DcrExecutionError, match=field):
            adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
                body,
                state,
                require_consistency=True,
            )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("application_type", "mobile"),
        ("software_id", "not-valid-hyphen"),
        ("scope", ""),
        ("id_token_signed_response_alg", "PS256X"),
        ("request_object_signing_alg", "none!"),
        ("tls_client_auth_subject_dn", "O=Missing leading common name"),
        ("registration_client_uri", "http://client.example.test/register/client"),
        ("redirect_uris", ["http://localhost/callback"]),
    ],
)
def test_registration_response_rejects_complete_schema_boundary_violations(
    dcr_protocol_service: DcrProtocolService,
    tmp_path: Path,
    field: str,
    replacement: JsonValue,
) -> None:
    """DCR 3.4 scalar, URI, algorithm, and subject-DN rules are mandatory."""
    adapter, body, state = _registration_validation_fixture(dcr_protocol_service, tmp_path)
    body[field] = replacement

    with pytest.raises(DcrExecutionError):
        adapter._validate_registration_response(  # noqa: SLF001 - directly tests the reviewed validation boundary.
            body,
            state,
            require_consistency=True,
        )
