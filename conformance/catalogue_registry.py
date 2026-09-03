"""Resolve plan-spec catalogue keys to bundled Open Banking test catalogues."""

from __future__ import annotations

from conformance.catalogue import CatalogueError, CatalogueKey, TestCatalogue
from conformance.catalogues import (
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    CBPII_FCS_CATALOGUE,
    DCR_3_4_CATALOGUE,
    PIS_PAYMENT_CATALOGUE,
    VRP_LEGACY_FCS_CATALOGUE,
)

_BUNDLED_CATALOGUES: tuple[TestCatalogue, ...] = (
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    PIS_PAYMENT_CATALOGUE,
    CBPII_FCS_CATALOGUE,
    VRP_LEGACY_FCS_CATALOGUE,
    DCR_3_4_CATALOGUE,
)
"""Catalogue set available to plan-spec compilation without external plugins."""


def supported_catalogues() -> tuple[TestCatalogue, ...]:
    """Return bundled catalogues available to the compiler.

    Returns:
        Tuple of bundled catalogue objects in stable display order.
    """
    return _BUNDLED_CATALOGUES


def resolve_catalogue(key: CatalogueKey) -> TestCatalogue:
    """Resolve a catalogue key to a bundled catalogue.

    Args:
        key: Standard/version/API key from a parsed plan spec.

    Returns:
        Matching bundled catalogue.

    Raises:
        CatalogueError: If no bundled catalogue matches the requested key.
    """
    for catalogue in _BUNDLED_CATALOGUES:
        if catalogue.key == key:
            return catalogue
    supported = ", ".join(
        f"{catalogue.key.standard}/{catalogue.key.version}/{catalogue.key.api}" for catalogue in _BUNDLED_CATALOGUES
    )
    requested = f"{key.standard}/{key.version}/{key.api}"
    raise CatalogueError(f"Unsupported catalogue: {requested}. Supported catalogues: {supported}")
