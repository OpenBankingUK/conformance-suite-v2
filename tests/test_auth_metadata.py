"""Unit tests for the auth bundle and selected-step mapping contract module."""

from __future__ import annotations

import pytest

from conformance.auth_metadata import (
    AuthBundleDeclaration,
    AuthBundleError,
    AuthBundleInventory,
    AuthStepRequirement,
    validate_bundle_id,
    validate_inventory,
    validate_step_id,
)

# ---------------------------------------------------------------------------
# validate_bundle_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_bundle_id_accepts_valid_ids() -> None:
    """Valid bundle ids pass without raising."""
    for bundle_id in ("ais-detail", "pis-domestic", "vrp-sweep", "A", "a1-b2_c3"):
        validate_bundle_id(bundle_id)  # must not raise


@pytest.mark.unit
def test_validate_bundle_id_rejects_empty_string() -> None:
    """Empty string is rejected."""
    with pytest.raises(AuthBundleError, match="Invalid bundle id"):
        validate_bundle_id("")


@pytest.mark.unit
def test_validate_bundle_id_rejects_leading_hyphen() -> None:
    """Leading hyphen is rejected."""
    with pytest.raises(AuthBundleError, match="Invalid bundle id"):
        validate_bundle_id("-bad-id")


@pytest.mark.unit
def test_validate_bundle_id_rejects_dot() -> None:
    """Dot character is rejected (reserved for placeholder resolution)."""
    with pytest.raises(AuthBundleError, match="Invalid bundle id"):
        validate_bundle_id("bundle.with.dot")


# ---------------------------------------------------------------------------
# validate_step_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_step_id_accepts_valid_ids() -> None:
    """Valid step ids pass without raising."""
    for step_id in ("token-exchange", "psu-authorization", "openid-discovery", "s1"):
        validate_step_id(step_id)


@pytest.mark.unit
def test_validate_step_id_rejects_space() -> None:
    """Space character is rejected."""
    with pytest.raises(AuthBundleError, match="Invalid step id"):
        validate_step_id("bad step")


# ---------------------------------------------------------------------------
# _validate_bundle_declaration (via validate_inventory)
# ---------------------------------------------------------------------------


def _make_bundle(
    bundle_id: str = "ais-detail",
    token_step_id: str = "token-exchange",  # noqa: S107 - step id fixture, not a secret
    consent_step_id: str | None = "account-access-consent",
    psu_step_id: str | None = "psu-authorization",
    required_scopes: tuple[str, ...] = ("openid", "accounts"),
    required_ob_permissions: tuple[str, ...] = ("ReadAccountsDetail",),
    excluded_ob_permissions: tuple[str, ...] = (),
    consuming_step_ids: tuple[str, ...] = ("accounts-list",),
    capability_refs: tuple[str, ...] = ("psu.manual",),
) -> AuthBundleDeclaration:
    """Build a minimal valid :class:`AuthBundleDeclaration` for tests.

    Args:
        bundle_id: Bundle identifier.
        token_step_id: Token exchange step id.
        consent_step_id: Consent step id (optional).
        psu_step_id: PSU authorisation step id (optional).
        required_scopes: OAuth scopes.
        required_ob_permissions: Required OB permissions.
        excluded_ob_permissions: Excluded OB permissions.
        consuming_step_ids: Consuming step ids.
        capability_refs: Capability requirement references.

    Returns:
        Constructed :class:`AuthBundleDeclaration`.
    """
    return AuthBundleDeclaration(
        id=bundle_id,
        token_step_id=token_step_id,
        consent_step_id=consent_step_id,
        psu_step_id=psu_step_id,
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
        required_scopes=required_scopes,
        required_ob_permissions=required_ob_permissions,
        excluded_ob_permissions=excluded_ob_permissions,
        consuming_step_ids=consuming_step_ids,
        capability_refs=capability_refs,
    )


def _make_inventory(
    bundles: tuple[AuthBundleDeclaration, ...] | None = None,
    step_requirements: tuple[AuthStepRequirement, ...] | None = None,
) -> AuthBundleInventory:
    """Build a minimal valid :class:`AuthBundleInventory` for tests.

    Args:
        bundles: Tuple of bundle declarations; defaults to a single valid bundle.
        step_requirements: Tuple of step requirements; defaults to one matching
            the default bundle.

    Returns:
        Constructed :class:`AuthBundleInventory`.
    """
    if bundles is None:
        bundles = (_make_bundle(),)
    if step_requirements is None:
        step_requirements = (AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail"),)
    return AuthBundleInventory(bundles=bundles, step_requirements=step_requirements)


# ---------------------------------------------------------------------------
# validate_inventory – happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_accepts_valid_inventory() -> None:
    """A well-formed inventory passes validation without raising."""
    validate_inventory(_make_inventory())


@pytest.mark.unit
def test_validate_inventory_accepts_empty_inventory() -> None:
    """An empty inventory (no bundles, no requirements) is valid."""
    validate_inventory(AuthBundleInventory(bundles=(), step_requirements=()))


@pytest.mark.unit
def test_validate_inventory_accepts_multiple_bundles() -> None:
    """Multiple distinct bundles are accepted when IDs are unique."""
    b1 = _make_bundle(bundle_id="ais-detail", consuming_step_ids=("accounts-list",))
    b2 = _make_bundle(
        bundle_id="ais-basic",
        consuming_step_ids=("transactions-list",),
        required_ob_permissions=("ReadAccountsBasic",),
    )
    inv = AuthBundleInventory(
        bundles=(b1, b2),
        step_requirements=(
            AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail"),
            AuthStepRequirement(step_id="transactions-list", bundle_id="ais-basic"),
        ),
    )
    validate_inventory(inv)


@pytest.mark.unit
def test_validate_inventory_accepts_no_consent_or_psu() -> None:
    """A client-credentials bundle with no consent or PSU step is valid."""
    bundle = AuthBundleDeclaration(
        id="cc-only",
        token_step_id="client-credentials-token",  # noqa: S106 - step id fixture, not a secret
        consent_step_id=None,
        psu_step_id=None,
        token_endpoint_auth_method="tls_client_auth",  # noqa: S106 - auth-method enum fixture, not a secret
        required_scopes=("openid",),
        required_ob_permissions=(),
        excluded_ob_permissions=(),
        consuming_step_ids=("accounts-check",),
        capability_refs=(),
    )
    inv = AuthBundleInventory(
        bundles=(bundle,),
        step_requirements=(AuthStepRequirement(step_id="accounts-check", bundle_id="cc-only"),),
    )
    validate_inventory(inv)


# ---------------------------------------------------------------------------
# validate_inventory – duplicate bundle id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_duplicate_bundle_id() -> None:
    """Duplicate bundle ids are rejected."""
    b1 = _make_bundle(bundle_id="ais-detail", consuming_step_ids=("accounts-list",))
    b2 = _make_bundle(bundle_id="ais-detail", consuming_step_ids=("balances-list",))
    with pytest.raises(AuthBundleError, match="Duplicate bundle id"):
        validate_inventory(
            AuthBundleInventory(
                bundles=(b1, b2),
                step_requirements=(),
            )
        )


# ---------------------------------------------------------------------------
# validate_inventory – unknown bundle reference
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_step_requirement_with_unknown_bundle() -> None:
    """Step requirements that reference a non-existent bundle are rejected."""
    bundle = _make_bundle()
    req = AuthStepRequirement(step_id="accounts-list", bundle_id="no-such-bundle")
    with pytest.raises(AuthBundleError, match="unknown bundle id"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=(req,)))


# ---------------------------------------------------------------------------
# validate_inventory – duplicate step mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_duplicate_step_requirement() -> None:
    """The same step id appearing in two requirements is rejected."""
    bundle = _make_bundle()
    req1 = AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail")
    req2 = AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail")
    with pytest.raises(AuthBundleError, match="Duplicate step mapping"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=(req1, req2)))


# ---------------------------------------------------------------------------
# validate_inventory – known_step_ids enforcement
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_unknown_step_in_requirement_when_known_given() -> None:
    """Unknown step id in a requirement is rejected when known_step_ids supplied."""
    bundle = _make_bundle(consuming_step_ids=("accounts-list",))
    req = AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail")
    with pytest.raises(AuthBundleError, match="unknown step id"):
        validate_inventory(
            AuthBundleInventory(bundles=(bundle,), step_requirements=(req,)),
            known_step_ids=frozenset(),  # empty – no known steps
        )


@pytest.mark.unit
def test_validate_inventory_rejects_unknown_consuming_step_when_known_given() -> None:
    """Unknown consuming_step_id is rejected when known_step_ids supplied."""
    bundle = _make_bundle(consuming_step_ids=("ghost-step",))
    with pytest.raises(AuthBundleError, match="unknown step id"):
        validate_inventory(
            AuthBundleInventory(bundles=(bundle,), step_requirements=()),
            known_step_ids=frozenset({"token-exchange", "accounts-list"}),
        )


@pytest.mark.unit
def test_validate_inventory_rejects_unknown_bundle_producer_steps_when_known_given() -> None:
    """Unknown token, consent, and PSU step ids are rejected when known_step_ids supplied."""
    bundle = _make_bundle(
        token_step_id="missing-token-step",  # noqa: S106 - step id fixture, not a secret
        consent_step_id="missing-consent-step",
        psu_step_id="missing-psu-step",
        consuming_step_ids=("accounts-list",),
    )

    with pytest.raises(AuthBundleError, match="unknown step id"):
        validate_inventory(
            AuthBundleInventory(bundles=(bundle,), step_requirements=()),
            known_step_ids=frozenset({"accounts-list"}),
        )


@pytest.mark.unit
def test_validate_inventory_accepts_valid_known_step_ids() -> None:
    """Inventory passes when all step ids are present in known_step_ids."""
    bundle = _make_bundle(consuming_step_ids=("accounts-list",))
    req = AuthStepRequirement(step_id="accounts-list", bundle_id="ais-detail")
    validate_inventory(
        AuthBundleInventory(bundles=(bundle,), step_requirements=(req,)),
        known_step_ids=frozenset({"account-access-consent", "psu-authorization", "token-exchange", "accounts-list"}),
    )


# ---------------------------------------------------------------------------
# Non-secret shape validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_jwt_in_scope() -> None:
    """A JWT-shaped string in required_scopes is rejected."""
    bundle = _make_bundle(
        required_scopes=("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.dGVzdA",),  # pragma: allowlist secret
    )
    with pytest.raises(AuthBundleError, match="credential material"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))


@pytest.mark.unit
def test_validate_inventory_rejects_bearer_token_in_capability_ref() -> None:
    """A raw Bearer token value embedded in a capability ref is rejected."""
    bundle = _make_bundle(capability_refs=("Bearer somerawtoken123",))
    with pytest.raises(AuthBundleError, match="credential material"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))


@pytest.mark.unit
def test_validate_inventory_rejects_pem_block_in_permission() -> None:
    """A PEM block embedded in a permission string is rejected."""
    bundle = _make_bundle(
        required_ob_permissions=("-----BEGIN PRIVATE KEY-----\nMIIEvgIBAD...",),  # pragma: allowlist secret
    )
    with pytest.raises(AuthBundleError, match="credential material"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))


# ---------------------------------------------------------------------------
# Overlapping required/excluded permissions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_overlapping_permissions() -> None:
    """A permission in both required and excluded is rejected."""
    bundle = _make_bundle(
        required_ob_permissions=("ReadAccountsDetail", "ReadBalances"),
        excluded_ob_permissions=("ReadBalances",),
    )
    with pytest.raises(AuthBundleError, match="both required_ob_permissions and excluded_ob_permissions"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))


# ---------------------------------------------------------------------------
# Scope format validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_inventory_rejects_scope_with_space() -> None:
    """A scope token containing a space is rejected (RFC 6749 §3.3)."""
    bundle = _make_bundle(required_scopes=("openid accounts",))
    with pytest.raises(AuthBundleError, match="non-empty string without spaces"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))


@pytest.mark.unit
def test_validate_inventory_rejects_empty_scope() -> None:
    """An empty string scope token is rejected."""
    bundle = _make_bundle(required_scopes=("",))
    with pytest.raises(AuthBundleError, match="non-empty string without spaces"):
        validate_inventory(AuthBundleInventory(bundles=(bundle,), step_requirements=()))
