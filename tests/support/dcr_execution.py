"""Builders for executing the Open Banking DCR 3.4 adapter against a fixture service.

The same compiled plan, typed configuration, and adapter construction are used
by the in-process protocol unit tests and by the loopback mTLS component test,
so they live here rather than being duplicated per category.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from conformance.approved_releases import ApprovedReleasePolicy
from conformance.catalogue import CompiledTestPlan, PlanDocumentV2, compile_test_plan_document, parse_test_plan_document
from conformance.catalogues.dcr import DCR_3_4_CATALOGUE
from conformance.dcr_execution import DcrCatalogueExecutionAdapter
from conformance.execution_log import BufferedExecutionLogger, NullExecutionLogger
from conformance.plan_configuration import DcrPlanConfiguration, parse_dcr_plan_configuration
from tests.support.dcr_test_service import DcrProtocolService

FIXED_TIME = datetime.fromtimestamp(1_800_000_000, tz=UTC)
"""Frozen JOSE issue time so signed DCR requests stay byte-for-byte reproducible."""


def build_compiled_plan(methods: Iterable[str]) -> CompiledTestPlan:
    """Compile a DCR plan selecting the requested endpoint methods.

    Args:
        methods: Direct registration/management methods.

    Returns:
        Compiled deterministic DCR catalogue plan.
    """
    endpoints = [
        {
            "method": method,
            "path": "/register" if method == "POST" else "/register/{ClientId}",
            "required": method == "POST",
            "locked": method == "POST",
        }
        for method in methods
    ]
    document = parse_test_plan_document(
        {
            "schemaVersion": "1.0",
            "specification": {
                "family": "OBL_DCR",
                "scheme": "open-banking-uk",
                "name": "dynamic-client-registration",
                "version": "3.4",
            },
            "securityEnvironment": {"discoveryUrl": "https://aspsp.example.test/.well-known/openid-configuration"},
            "dynamicClientRegistration": {},
            "endpoints": endpoints,
            "metadata": {},
        }
    )
    assert isinstance(document, PlanDocumentV2)
    return compile_test_plan_document(document, (DCR_3_4_CATALOGUE,))


def build_config(service: DcrProtocolService, root: Path, auth_method: str = "tls_client_auth") -> DcrPlanConfiguration:
    """Build executable typed configuration for the deterministic service.

    Args:
        service: Running mTLS DCR service.
        root: Directory receiving the SSA file.
        auth_method: Token endpoint client-auth method.

    Returns:
        Validated DCR plan configuration.
    """
    ssa_path = root / "ssa.jwt"
    ssa_path.write_text(service.protocol.software_statement_assertion, encoding="utf-8")
    return parse_dcr_plan_configuration(
        {
            "discoveryUrl": service.discovery_url,
            "clientAuthMethod": auth_method,
            "signingPrivateKeyPath": str(service.protocol.signing_private_key_path),
            "signingKeyId": "fixture-signing-key",
            "mtls": {
                "certificatePath": str(service.tls.client_certificate_path),
                "privateKeyPath": str(service.tls.client_private_key_path),
                "caBundlePath": str(service.tls.ca_certificate_path),
            },
        },
        {
            "softwareStatementAssertionPath": str(ssa_path),
            "registrationAudience": "aspsp123",
        },
        {},
    )


def build_adapter(
    service: DcrProtocolService,
    root: Path,
    *,
    methods: Iterable[str] = ("POST",),
    auth_method: str = "tls_client_auth",
    logger: BufferedExecutionLogger | NullExecutionLogger | None = None,
    client: httpx.Client | None = None,
    approved_release_policy: ApprovedReleasePolicy | None = None,
) -> DcrCatalogueExecutionAdapter:
    """Build an adapter with deterministic JOSE time and ids.

    Args:
        service: Running deterministic DCR service.
        root: Directory receiving runtime references.
        methods: Selected direct DCR endpoint methods.
        auth_method: Token endpoint client-auth method.
        logger: Optional execution logger.
        client: Optional caller-owned HTTP client.
        approved_release_policy: Optional certification release policy.

    Returns:
        Configured execution adapter.
    """
    ids = iter(f"test-jti-{index}" for index in range(1000))
    return DcrCatalogueExecutionAdapter(
        compiled_plan=build_compiled_plan(methods),
        config=build_config(service, root, auth_method),
        execution_logger=logger or NullExecutionLogger(),
        client=client,
        clock=lambda: FIXED_TIME,
        jwt_id_factory=lambda: next(ids),
        approved_release_policy=approved_release_policy,
    )
