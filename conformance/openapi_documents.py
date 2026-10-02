"""Resolve bundled Open Banking Read/Write OpenAPI document snapshots per document update.

Each Open Banking UK Read/Write specification version can have several published
OpenAPI ("swagger") document updates. Catalogue cases reference a *logical*
document such as ``ob-read-write/account-info-openapi``; compilation binds it to
the participant-selected update, producing a *bundled* document identifier such
as ``ob-read-write/v4.0.1-Update-1/account-info-openapi`` that names one pinned
snapshot under ``standards/ob_read_write/openapi/<catalogue-id>/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType

from conformance.specification_registry import OpenApiDocumentUpdate, all_openapi_document_updates

OPENAPI_DOCUMENTS_ROOT = Path(__file__).resolve().parent / "standards" / "ob_read_write" / "openapi"
"""Directory containing one pinned snapshot directory per OpenAPI document update."""

READ_WRITE_OPENAPI_DOCUMENT_NAMES = (
    "account-info-openapi",
    "payment-initiation-openapi",
    "confirmation-funds-openapi",
    "vrp-openapi",
)
"""Read/Write OpenAPI document names bundled for every update."""

_READ_WRITE_DOCUMENT_PREFIX = "ob-read-write/"
"""Namespace prefix shared by logical and bundled Read/Write document identifiers."""


def logical_read_write_document(name: str) -> str:
    """Return the update-independent document identifier used by catalogue cases.

    Args:
        name: Document name from :data:`READ_WRITE_OPENAPI_DOCUMENT_NAMES`.

    Returns:
        Logical identifier, for example ``ob-read-write/vrp-openapi``.

    Raises:
        ValueError: If ``name`` is not a bundled Read/Write document name.
    """
    if name not in READ_WRITE_OPENAPI_DOCUMENT_NAMES:
        raise ValueError(f"Unknown Read/Write OpenAPI document: {name}")
    return f"{_READ_WRITE_DOCUMENT_PREFIX}{name}"


def bundled_read_write_document(update: OpenApiDocumentUpdate, name: str) -> str:
    """Return the bundled document identifier for one update snapshot.

    Args:
        update: Selected OpenAPI document update.
        name: Document name from :data:`READ_WRITE_OPENAPI_DOCUMENT_NAMES`.

    Returns:
        Bundled identifier, for example
        ``ob-read-write/v4.0.1-Update-1/account-info-openapi``.

    Raises:
        ValueError: If ``name`` is not a bundled Read/Write document name.
    """
    if name not in READ_WRITE_OPENAPI_DOCUMENT_NAMES:
        raise ValueError(f"Unknown Read/Write OpenAPI document: {name}")
    return f"{_READ_WRITE_DOCUMENT_PREFIX}{update.catalogue_id}/{name}"


def read_write_openapi_document_path(update: OpenApiDocumentUpdate, name: str) -> Path:
    """Return the on-disk snapshot path for one update document.

    Args:
        update: OpenAPI document update.
        name: Document name from :data:`READ_WRITE_OPENAPI_DOCUMENT_NAMES`.

    Returns:
        Absolute path to the bundled JSON snapshot.

    Raises:
        ValueError: If ``name`` is not a bundled Read/Write document name.
    """
    if name not in READ_WRITE_OPENAPI_DOCUMENT_NAMES:
        raise ValueError(f"Unknown Read/Write OpenAPI document: {name}")
    return OPENAPI_DOCUMENTS_ROOT / update.catalogue_id / f"{name}.json"


def is_logical_read_write_document(document: str) -> bool:
    """Return whether ``document`` is an unbound logical catalogue document identifier.

    Args:
        document: Schema document identifier from an assertion rule.

    Returns:
        True for identifiers such as ``ob-read-write/account-info-openapi``.
    """
    return document.startswith(_READ_WRITE_DOCUMENT_PREFIX) and (
        document.removeprefix(_READ_WRITE_DOCUMENT_PREFIX) in READ_WRITE_OPENAPI_DOCUMENT_NAMES
    )


def bind_read_write_document(document: str, update: OpenApiDocumentUpdate) -> str:
    """Bind a logical catalogue document identifier to the selected update snapshot.

    Args:
        document: Logical or already-bundled schema document identifier.
        update: Participant-selected OpenAPI document update.

    Returns:
        Bundled document identifier for ``update``. Identifiers outside the
        Read/Write namespace are returned unchanged.

    Raises:
        ValueError: If ``document`` is already bound to a different update.
    """
    if is_logical_read_write_document(document):
        return bundled_read_write_document(update, document.removeprefix(_READ_WRITE_DOCUMENT_PREFIX))
    if document.startswith(_READ_WRITE_DOCUMENT_PREFIX) and document not in bundled_openapi_document_paths():
        raise ValueError(f"Unknown Read/Write OpenAPI document: {document}")
    if document.startswith(_READ_WRITE_DOCUMENT_PREFIX) and not document.startswith(
        f"{_READ_WRITE_DOCUMENT_PREFIX}{update.catalogue_id}/"
    ):
        raise ValueError(f"OpenAPI document {document} does not belong to {update.catalogue_id}")
    return document


@cache
def bundled_openapi_document_paths() -> Mapping[str, Path]:
    """Return every allowlisted bundled OpenAPI document identifier and its path.

    Returns:
        Immutable mapping derived from the specification registry.
    """
    return MappingProxyType(
        {
            bundled_read_write_document(update, name): read_write_openapi_document_path(update, name)
            for update in all_openapi_document_updates()
            for name in READ_WRITE_OPENAPI_DOCUMENT_NAMES
        }
    )
