"""Canonical connection and security values that let a Read/Write plan run.

Validation rejects Read/Write plans whose selected tests need OAuth 2.0 client
values or the FAPI signing group that the plan does not supply, so fixtures
that expect a plan to compile, launch, or run spread these into their
``securityEnvironment``. Credentials are references only.
"""

from __future__ import annotations

from conformance.json_types import JsonObject

RUN_READY_SECURITY_ENVIRONMENT: JsonObject = {
    "clientId": "client-123",
    "redirectUri": "https://tpp.example.com/callback",
    "authorizationEndpoint": "https://auth.example.com/authorize",
    "issuer": "https://auth.example.com",
    "tokenEndpoint": "https://auth.example.com/token",
    "signingCertificatePath": "/certs/signing.pem",
    "signingPrivateKeyPath": "/certs/signing.key",
    "signingKeyId": "signing-kid",
    "clientAssertionIssuer": "client-123",
    "clientAssertionSubject": "client-123",
    "clientAuthMethod": "private_key_jwt",
}
"""OAuth client and FAPI signing references for run-ready fixture plans."""
