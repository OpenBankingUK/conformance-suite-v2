"""Resolve plan-spec catalogue keys to bundled Open Banking test catalogues."""

from __future__ import annotations

from conformance.catalogue import CatalogueError, CatalogueKey, TestCatalogue, format_catalogue_key
from conformance.catalogues import (
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    CBPII_FCS_CATALOGUE,
    CBPII_V31_FCS_CATALOGUE,
    DCR_3_4_CATALOGUE,
    PIS_PAYMENT_CATALOGUE,
    PIS_V31_PAYMENT_CATALOGUE,
    VRP_LEGACY_FCS_CATALOGUE,
    VRP_V31_LEGACY_FCS_CATALOGUE,
)
from conformance.catalogues.read_write_v4_0_1 import (
    AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    CBPII_V401_FCS_CATALOGUE,
    PIS_V401_PAYMENT_CATALOGUE,
    VRP_V401_LEGACY_FCS_CATALOGUE,
)

_BUNDLED_CATALOGUES: tuple[TestCatalogue, ...] = (
    AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    PIS_V401_PAYMENT_CATALOGUE,
    CBPII_V401_FCS_CATALOGUE,
    VRP_V401_LEGACY_FCS_CATALOGUE,
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    PIS_PAYMENT_CATALOGUE,
    CBPII_FCS_CATALOGUE,
    VRP_LEGACY_FCS_CATALOGUE,
    AIS_V31_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    PIS_V31_PAYMENT_CATALOGUE,
    CBPII_V31_FCS_CATALOGUE,
    VRP_V31_LEGACY_FCS_CATALOGUE,
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
    supported = ", ".join(format_catalogue_key(catalogue.key) for catalogue in _BUNDLED_CATALOGUES)
    requested = format_catalogue_key(key)
    raise CatalogueError(f"Unsupported catalogue: {requested}. Supported catalogues: {supported}")
