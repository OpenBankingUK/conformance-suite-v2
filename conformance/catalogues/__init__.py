"""Bundled conformance catalogues compiled from Open Banking UK standards coverage."""

from conformance.catalogues.ais import (
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
    get_ais_accounts_transactions_catalogue,
)
from conformance.catalogues.cbpii import CBPII_CATALOGUE_KEY, CBPII_FCS_CATALOGUE
from conformance.catalogues.dcr import DCR_3_4_CATALOGUE, DCR_CATALOGUE_KEY
from conformance.catalogues.pis import PIS_PAYMENT_CATALOGUE, PIS_PAYMENT_CATALOGUE_KEY, get_pis_payment_catalogue
from conformance.catalogues.vrp import VRP_LEGACY_FCS_CATALOGUE

__all__ = [
    "AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE",
    "AIS_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY",
    "CBPII_CATALOGUE_KEY",
    "CBPII_FCS_CATALOGUE",
    "DCR_3_4_CATALOGUE",
    "DCR_CATALOGUE_KEY",
    "PIS_PAYMENT_CATALOGUE",
    "PIS_PAYMENT_CATALOGUE_KEY",
    "VRP_LEGACY_FCS_CATALOGUE",
    "get_ais_accounts_transactions_catalogue",
    "get_pis_payment_catalogue",
]
