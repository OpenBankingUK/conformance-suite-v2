"""Durable, non-secret auth bundle and selected-step mapping contract.

An auth bundle is the unit that connects a consent-creation step, a PSU
authorisation step, a token-exchange step, token-endpoint client auth
metadata, required OAuth scopes, required and excluded Open Banking consent
permissions, consuming resource steps, and optional environment capability
requirement references.

This module defines the canonical dataclasses and type aliases for that
contract.  It is intentionally generic across all Open Banking API families
(AIS, PIS, CBPII, VRP) and future suite families.

The contract is non-secret by design: it carries only stable identifiers and
declarative metadata (bundle id, step ids, auth method label, scope strings,
permission strings, capability references).  It must never carry raw access
tokens, refresh tokens, client assertions, private-key material, certificate
paths, or any other credential values.  Validation helpers enforce both the
structural invariants and the non-secret shape constraint.

Relevant standards concepts: OAuth 2.0 (RFC 6749), OIDC Core, FAPI 1.0
Advanced, Open Banking Read/Write API consent model (``account-access-consents``,
``domestic-payment-consents``, ``funds-confirmation-consents``,
``domestic-vrp-consents``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from conformance.model_bank_config import TokenEndpointClientAuthMode


class AuthBundleError(ValueError):
    """Raised when an auth bundle or inventory declaration is invalid."""


# ---------------------------------------------------------------------------
# Type aliases – all non-secret, identifier / declarative-metadata values
# ---------------------------------------------------------------------------

type BundleId = str
"""Stable identifier for an auth bundle.

Allowed characters: ASCII letters, digits, hyphens, and underscores.  Must
begin with a letter or digit.  Must not contain dots (reserved for
placeholder resolution) or characters that could embed JWT or credential
material.

Example: ``"ais-detail"``, ``"pis-domestic-payment"``, ``"vrp-sweep"``.
"""

type StepId = str
"""Manifest step identifier.

Same character constraints as :data:`BundleId`.  Matches the ``id`` fields
present on ``ManifestStep`` and ``PsuAuthorizationStep`` objects.
"""

type OBPermission = str
"""Single Open Banking consent permission string.

Values must match the string literals accepted by the Open Banking Read/Write
API ``Data.Permissions`` array, e.g. ``"ReadAccountsDetail"``,
``"ReadBalances"``, ``"ReadTransactionsDetail"``.
"""

type OAuthScope = str
"""Single OAuth 2.0 scope token as defined by RFC 6749 §3.3.

Values must not contain spaces or NUL characters.  Examples:
``"openid"``, ``"accounts"``, ``"payments"``.
"""

type CapabilityRef = str
"""Environment capability requirement reference key.

A short, stable key that names an environment capability that a bundle
depends on, e.g. ``"psu.manual"``, ``"psu.headless"``,
``"auth.private_key_jwt"``.  Evaluated by the environment-capabilities
subsystem (a separate implementation slice) to detect incompatible
config/environment combinations before launch.
"""

# ---------------------------------------------------------------------------
# Pattern used to validate BundleId and StepId values
# ---------------------------------------------------------------------------

_ID_CHAR_CLASS = r"[A-Za-z0-9][A-Za-z0-9_-]*"
"""Character class for valid bundle and step identifiers.

Starts with a letter or digit; subsequent characters may also include
hyphens and underscores.  Dots are excluded to prevent placeholder resolver
ambiguity.
"""

_ID_PATTERN = re.compile(r"^" + _ID_CHAR_CLASS + r"$")
"""Compiled regex pattern for :data:`BundleId` / :data:`StepId` validation."""

# ---------------------------------------------------------------------------
# Non-secret shape guard
# ---------------------------------------------------------------------------

_JWT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
"""Heuristic pattern that matches compact-serialisation JWTs (``header.payload.sig``).

Used to guard against accidentally embedding token material in auth bundle
metadata fields.  A positive match does not guarantee a string is a JWT, but
is sufficient to reject obviously credential-shaped values at validation time.
"""

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    _JWT_PATTERN,
    re.compile(r"^-----BEGIN", re.MULTILINE),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
)
"""Compiled heuristic patterns used by :func:`_assert_non_secret_string`.

Ordered from cheapest to most specific:

1. Compact-serialisation JWT (``aaa.bbb.ccc``).
2. PEM block header — detects embedded certificates or private keys.
3. Bearer token literal — catches raw ``Authorization`` header values.
"""


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthBundleDeclaration:
    """Non-secret declaration of a single auth bundle.

    Carries all the metadata needed for UI plan previews, execution routing,
    and certification-coverage analysis without exposing any credential
    material.

    Attributes:
        id: Stable, unique identifier for this bundle within its suite.  Used
            as a reference key by :class:`AuthStepRequirement` and in UI/log
            output.
        token_step_id: Step id of the HTTP token-exchange step that mints the
            access token consumed by :attr:`consuming_step_ids`.
        consent_step_id: Step id of the consent-creation step that backs this
            token, or ``None`` when no consent-creation step exists (e.g.
            client-credentials grants or no-token variants).
        psu_step_id: Step id of the PSU authorisation step that completes the
            consent flow, or ``None`` when no PSU step is involved.
        token_endpoint_auth_method: Optional declared token-endpoint client
            authentication method (``"private_key_jwt"`` or
            ``"tls_client_auth"``).  ``None`` means unspecified / inherited
            from suite-level config.
        required_scopes: Required OAuth 2.0 scope tokens for this bundle.
            Must not contain spaces within individual tokens.
        required_ob_permissions: Required Open Banking consent permissions
            declared by the consent-creation step or the bundle author.
        excluded_ob_permissions: Open Banking consent permissions that must
            **not** be present (used for negative / incorrect-token variants).
        consuming_step_ids: Ordered step ids of resource-access steps that
            consume the access token produced by this bundle.
        capability_refs: Environment capability requirement references that
            must be satisfied before this bundle can be executed.  Evaluated
            by the environment-capability subsystem in a later slice.
    """

    id: BundleId
    token_step_id: StepId
    consent_step_id: StepId | None
    psu_step_id: StepId | None
    token_endpoint_auth_method: TokenEndpointClientAuthMode | None
    required_scopes: tuple[OAuthScope, ...]
    required_ob_permissions: tuple[OBPermission, ...]
    excluded_ob_permissions: tuple[OBPermission, ...]
    consuming_step_ids: tuple[StepId, ...]
    capability_refs: tuple[CapabilityRef, ...]


@dataclass(frozen=True)
class AuthStepRequirement:
    """Mapping of one selected manifest step to the auth bundle it consumes.

    Attributes:
        step_id: Selected manifest step id that requires an access token.
        bundle_id: Stable :attr:`AuthBundleDeclaration.id` of the bundle
            whose token satisfies the step's ``Authorization`` header.
    """

    step_id: StepId
    bundle_id: BundleId


@dataclass(frozen=True)
class AuthBundleInventory:
    """Ordered collection of auth bundles and selected-step mappings.

    Represents the full non-secret auth contract for a suite plan: which
    bundles are required, what each bundle needs, and which selected steps
    consume each bundle.

    Attributes:
        bundles: Ordered auth bundle declarations in the sequence they are
            first consumed by the selected plan.
        step_requirements: Per-step bundle mappings in manifest step order,
            covering only selected steps that consume a token.
    """

    bundles: tuple[AuthBundleDeclaration, ...]
    step_requirements: tuple[AuthStepRequirement, ...]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_bundle_id(bundle_id: str) -> None:
    """Assert that a bundle identifier is syntactically valid.

    Args:
        bundle_id: Candidate bundle identifier string.

    Raises:
        AuthBundleError: If the value is empty or contains characters outside
            the allowed set (ASCII letters, digits, hyphens, underscores; must
            start with a letter or digit).
    """
    if not bundle_id or _ID_PATTERN.fullmatch(bundle_id) is None:
        raise AuthBundleError(f"Invalid bundle id {bundle_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]*")


def validate_step_id(step_id: str) -> None:
    """Assert that a step identifier is syntactically valid.

    Args:
        step_id: Candidate step identifier string.

    Raises:
        AuthBundleError: If the value is empty or contains characters outside
            the allowed set (ASCII letters, digits, hyphens, underscores; must
            start with a letter or digit).
    """
    if not step_id or _ID_PATTERN.fullmatch(step_id) is None:
        raise AuthBundleError(f"Invalid step id {step_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]*")


def _assert_non_secret_string(value: str, *, location: str) -> None:
    """Assert that a string value does not contain credential material.

    Checks the value against heuristic patterns for compact-serialisation
    JWTs, PEM blocks, and raw Bearer token literals.  A match causes an
    immediate :class:`AuthBundleError` so that a misauthored bundle cannot
    silently embed a secret in participant-visible output.

    Args:
        value: String value to inspect.
        location: Dot-path location label used in the error message.

    Raises:
        AuthBundleError: If the value matches any secret-material heuristic.
    """
    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            raise AuthBundleError(
                f"{location}: value appears to contain credential material "
                f"and must not be stored in an auth bundle declaration"
            )


def _validate_bundle_declaration(bundle: AuthBundleDeclaration) -> None:
    """Validate structural and non-secret constraints on one bundle declaration.

    Args:
        bundle: Auth bundle declaration to validate.

    Raises:
        AuthBundleError: If any field violates identity, format, or non-secret
            constraints.
    """
    validate_bundle_id(bundle.id)
    validate_step_id(bundle.token_step_id)
    if bundle.consent_step_id is not None:
        validate_step_id(bundle.consent_step_id)
    if bundle.psu_step_id is not None:
        validate_step_id(bundle.psu_step_id)
    for scope in bundle.required_scopes:
        _assert_non_secret_string(scope, location=f"bundle({bundle.id}).required_scopes")
        if not scope or " " in scope:
            raise AuthBundleError(
                f"bundle({bundle.id}).required_scopes: scope {scope!r} must be a "
                "non-empty string without spaces (RFC 6749 §3.3)"
            )
    for perm in bundle.required_ob_permissions:
        _assert_non_secret_string(perm, location=f"bundle({bundle.id}).required_ob_permissions")
    for perm in bundle.excluded_ob_permissions:
        _assert_non_secret_string(perm, location=f"bundle({bundle.id}).excluded_ob_permissions")
    overlap = set(bundle.required_ob_permissions) & set(bundle.excluded_ob_permissions)
    if overlap:
        raise AuthBundleError(
            f"bundle({bundle.id}): permissions appear in both required_ob_permissions "
            f"and excluded_ob_permissions: {sorted(overlap)}"
        )
    for step_id in bundle.consuming_step_ids:
        validate_step_id(step_id)
    for cap in bundle.capability_refs:
        _assert_non_secret_string(cap, location=f"bundle({bundle.id}).capability_refs")


def validate_inventory(
    inventory: AuthBundleInventory,
    *,
    known_step_ids: frozenset[str] | None = None,
) -> None:
    """Validate structural invariants of a full auth bundle inventory.

    Checks performed:

    * Every bundle declaration satisfies :func:`_validate_bundle_declaration`.
    * Bundle ids are unique across all bundle declarations.
    * Every bundle id referenced by a step requirement exists in
      ``inventory.bundles``.
    * No step id appears in more than one step requirement (duplicate mapping).
    * If ``known_step_ids`` is supplied, every step id referenced by a bundle
      declaration, step requirement, and consuming-step list must be present in
      the set.

    Args:
        inventory: Auth bundle inventory to validate.
        known_step_ids: Optional set of all step ids declared by the manifest.
            When supplied, unknown step references are rejected.

    Raises:
        AuthBundleError: If any invariant is violated.
    """
    seen_bundle_ids: set[str] = set()
    for bundle in inventory.bundles:
        _validate_bundle_declaration(bundle)
        if bundle.id in seen_bundle_ids:
            raise AuthBundleError(f"Duplicate bundle id {bundle.id!r} in inventory")
        seen_bundle_ids.add(bundle.id)
        if known_step_ids is not None:
            referenced_step_ids = tuple(
                step_id
                for step_id in (bundle.token_step_id, bundle.consent_step_id, bundle.psu_step_id)
                if step_id is not None
            )
            _assert_known_step_ids(
                step_ids=referenced_step_ids,
                known_step_ids=known_step_ids,
                location=f"bundle({bundle.id}).step_ids",
            )
            _assert_known_step_ids(
                step_ids=bundle.consuming_step_ids,
                known_step_ids=known_step_ids,
                location=f"bundle({bundle.id}).consuming_step_ids",
            )

    seen_step_ids: set[str] = set()
    for req in inventory.step_requirements:
        validate_step_id(req.step_id)
        validate_bundle_id(req.bundle_id)
        if req.step_id in seen_step_ids:
            raise AuthBundleError(f"Duplicate step mapping for step id {req.step_id!r} in inventory")
        seen_step_ids.add(req.step_id)
        if req.bundle_id not in seen_bundle_ids:
            raise AuthBundleError(
                f"Step requirement for step {req.step_id!r} references unknown bundle id {req.bundle_id!r}"
            )
        if known_step_ids is not None and req.step_id not in known_step_ids:
            raise AuthBundleError(f"Step requirement references unknown step id {req.step_id!r}")


def _assert_known_step_ids(
    step_ids: tuple[str, ...],
    *,
    known_step_ids: frozenset[str],
    location: str,
) -> None:
    """Assert that every step id in a tuple is present in a known-step set.

    Args:
        step_ids: Tuple of step ids to check.
        known_step_ids: Full set of step ids declared by the manifest.
        location: Dot-path location label used in error messages.

    Raises:
        AuthBundleError: If any step id is not found in ``known_step_ids``.
    """
    for step_id in step_ids:
        if step_id not in known_step_ids:
            raise AuthBundleError(f"{location}: unknown step id {step_id!r}")
