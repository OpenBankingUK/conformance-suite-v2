"""Builder wizard selection of Read/Write OpenAPI document updates."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore

from conformance.api.builder_draft_store import SessionBuilderDraftStore
from conformance.api.builder_wizard import (
    CatalogueBoundaryForm,
    openapi_document_update_options,
)

pytestmark = pytest.mark.unit


def _boundary_data(*, version: str, update: str | None) -> dict[str, object]:
    data: dict[str, object] = {"scheme": "open-banking-uk", "specification": "read-write", "version": version}
    if update is not None:
        data["openapi_document_update"] = update
    return data


def test_update_options_are_scoped_per_version_and_mark_latest() -> None:
    options = [option for option in openapi_document_update_options() if option.version == "4.0.0"]

    assert [option.value for option in options] == ["Baseline", "Release-2", "Update-3", "Update-4", "Update-5"]
    assert [option.label for option in options][:2] == ["Baseline", "Release 2"]
    assert [option.value for option in options if option.latest] == ["Update-5"]
    assert not [option for option in openapi_document_update_options() if option.version == "3.4"]


def test_boundary_form_defaults_blank_update_to_latest() -> None:
    form = CatalogueBoundaryForm(data=_boundary_data(version="4.0.1", update=None))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["openapi_document_update"] == "Update-1"


def test_boundary_form_accepts_historical_update() -> None:
    form = CatalogueBoundaryForm(data=_boundary_data(version="3.1.11", update="Release-2"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["openapi_document_update"] == "Release-2"


def test_boundary_form_rejects_update_from_another_version() -> None:
    form = CatalogueBoundaryForm(data=_boundary_data(version="4.0.1", update="Release-5"))

    assert not form.is_valid()
    assert "openapi_document_update" in form.errors


def test_draft_persists_selected_update_and_resets_on_version_change() -> None:
    store = SessionBuilderDraftStore(SessionStore())
    draft = store.create().with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="read-write",
        version="4.0.0",
        openapi_document_update="Release-2",
    )
    store.save(draft)

    loaded = store.get(draft.draft_id)
    assert loaded is not None
    assert loaded.openapi_document_update == "Release-2"
    assert loaded.with_config(config={}).openapi_document_update == "Release-2"

    switched = loaded.with_catalogue_boundary(scheme="open-banking-uk", specification="read-write", version="4.0.1")
    assert switched.openapi_document_update == "Update-1"
    dcr = loaded.with_catalogue_boundary(
        scheme="open-banking-uk",
        specification="dynamic-client-registration",
        version="3.4",
    )
    assert dcr.openapi_document_update is None


def test_draft_rejects_unpublished_update() -> None:
    draft = SessionBuilderDraftStore(SessionStore()).create()

    with pytest.raises(ValueError, match="openApiDocumentUpdate"):
        draft.with_catalogue_boundary(
            scheme="open-banking-uk",
            specification="read-write",
            version="4.0.1",
            openapi_document_update="Update-5",
        )
