"""Open Banking Read/Write 4.0.1 catalogues.

Read/Write 4.0.0 and 4.0.1 share ``v4.0`` endpoint paths but are separate
participant-facing specification versions. These modules are deliberate,
independent copies of the 4.0.0 case definitions so 4.0.1 coverage can diverge
without affecting 4.0.0.
"""

from conformance.catalogues.read_write_v4_0_1.ais import (
    AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE,
    AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY,
)
from conformance.catalogues.read_write_v4_0_1.cbpii import CBPII_V401_CATALOGUE_KEY, CBPII_V401_FCS_CATALOGUE
from conformance.catalogues.read_write_v4_0_1.pis import PIS_V401_PAYMENT_CATALOGUE, PIS_V401_PAYMENT_CATALOGUE_KEY
from conformance.catalogues.read_write_v4_0_1.vrp import CVRP_V401_LEGACY_FCS_CATALOGUE, VRP_V401_LEGACY_FCS_CATALOGUE

__all__ = [
    "AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE",
    "AIS_V401_ACCOUNTS_TRANSACTIONS_CATALOGUE_KEY",
    "CBPII_V401_CATALOGUE_KEY",
    "CBPII_V401_FCS_CATALOGUE",
    "PIS_V401_PAYMENT_CATALOGUE",
    "PIS_V401_PAYMENT_CATALOGUE_KEY",
    "CVRP_V401_LEGACY_FCS_CATALOGUE",
    "VRP_V401_LEGACY_FCS_CATALOGUE",
]
