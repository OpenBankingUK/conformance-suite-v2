"""Path-or-paste credential fields for the browser test-plan wizard.

Every credential in the wizard can be supplied three ways: as an absolute file
path (the original behaviour, backed by the optional read-only ``/certs``
mount), as PEM text pasted into a textarea, or as an uploaded file. Pasted and
uploaded material is carried inline in the draft configuration; see
:mod:`conformance.credentials` for how it is consumed at runtime.

Stored inline material is **never** re-rendered into the page. On revisit the
field shows a "Configured" badge with a non-secret descriptor (certificate
subject and expiry, or software statement ``software_id``/``kid``) plus Keep,
Replace, and Clear controls. There is no reveal control and no endpoint that
returns stored credential material.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from django import forms

from conformance.credentials import (
    MAX_INLINE_CREDENTIAL_BYTES,
    CredentialError,
    CredentialKind,
    CredentialMaterial,
    credential_from_inline,
    credential_from_path,
    describe_credential,
    validate_inline_material,
)
from conformance.json_types import JsonObject

CREDENTIAL_SOURCE_CHOICES: tuple[tuple[str, str], ...] = (
    ("path", "Absolute file path"),
    ("inline", "Paste or upload"),
)
"""How a participant is supplying one credential."""

CREDENTIAL_ACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("keep", "Keep the configured value"),
    ("replace", "Replace"),
    ("clear", "Clear"),
)
"""What to do with a credential that already holds stored inline material."""


@dataclass(frozen=True)
class CredentialFieldSpec:
    """One wizard credential that accepts a path, pasted text, or an upload.

    Attributes:
        name: Field-name stem. The existing path input is ``{name}_path``, and
            the paste, upload, source, and action inputs are ``{name}_pem``,
            ``{name}_file``, ``{name}_source``, and ``{name}_action``.
        label: Participant-facing credential name used in messages.
        kind: Artefact the credential carries, used to validate pasted text and
            to build the non-secret descriptor.
    """

    name: str
    label: str
    kind: CredentialKind

    @property
    def path_field(self) -> str:
        """Form field name carrying the absolute path.

        Returns:
            Field name for the path input.
        """
        return f"{self.name}_path"

    @property
    def pem_field(self) -> str:
        """Form field name carrying pasted material.

        Returns:
            Field name for the paste textarea.
        """
        return f"{self.name}_pem"

    @property
    def file_field(self) -> str:
        """Form field name carrying an uploaded file.

        Returns:
            Field name for the upload input.
        """
        return f"{self.name}_file"

    @property
    def source_field(self) -> str:
        """Form field name carrying the selected supply method.

        Returns:
            Field name for the source selector.
        """
        return f"{self.name}_source"

    @property
    def action_field(self) -> str:
        """Form field name carrying the keep/replace/clear action.

        Returns:
            Field name for the stored-value action selector.
        """
        return f"{self.name}_action"


SECURITY_CREDENTIAL_SPECS: tuple[CredentialFieldSpec, ...] = (
    CredentialFieldSpec("signing_certificate", "Signing certificate", "certificate"),
    CredentialFieldSpec("signing_private_key", "Signing private key", "private_key"),
    CredentialFieldSpec("tls_ca_bundle", "CA bundle", "ca_bundle"),
    CredentialFieldSpec("tls_client_certificate", "mTLS client certificate", "certificate"),
    CredentialFieldSpec("tls_client_private_key", "mTLS client private key", "private_key"),
    CredentialFieldSpec(
        "dcr_software_statement_assertion",
        "Software statement assertion",
        "assertion",
    ),
    CredentialFieldSpec("dcr_signing_certificate", "DCR signing certificate", "certificate"),
)
"""Credentials configurable on the wizard security step."""

SECURITY_CREDENTIAL_SPECS_BY_NAME: Mapping[str, CredentialFieldSpec] = {
    spec.name: spec for spec in SECURITY_CREDENTIAL_SPECS
}
"""Security credential specs keyed by field-name stem."""


def add_credential_fields(fields: MutableMapping[str, forms.Field], specs: Mapping[str, CredentialFieldSpec]) -> None:
    """Add the paste, upload, source, and action inputs for each credential.

    The path input is declared on the form itself, so only the additional
    inputs are added here.

    Args:
        fields: Mutable form field mapping to extend.
        specs: Credential specs to add fields for, keyed by name.
    """
    for spec in specs.values():
        fields[spec.pem_field] = forms.CharField(
            label=f"{spec.label} (paste)",
            required=False,
            widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "false", "autocomplete": "off"}),
        )
        fields[spec.file_field] = forms.FileField(label=f"{spec.label} (upload)", required=False)
        fields[spec.source_field] = forms.ChoiceField(
            label=f"{spec.label} source",
            required=False,
            choices=CREDENTIAL_SOURCE_CHOICES,
        )
        fields[spec.action_field] = forms.ChoiceField(
            label=f"{spec.label} action",
            required=False,
            choices=CREDENTIAL_ACTION_CHOICES,
        )


def resolve_credential(
    form: forms.Form,
    cleaned_data: Mapping[str, object],
    spec: CredentialFieldSpec,
    *,
    stored: CredentialMaterial | None,
) -> CredentialMaterial | None:
    """Resolve one credential from submitted path, paste, upload, or stored value.

    The credential is resolved from whichever input was actually filled in, so
    pasted material is never silently discarded because a source selector was
    left alone. Supplying the same credential through more than one input is an
    error rather than a silent precedence rule, because the participant's
    intent is genuinely ambiguous.

    Validation failures are attached to the form rather than raised, so every
    credential reports its own error in one pass.

    Args:
        form: Bound form to attach field errors to.
        cleaned_data: Cleaned form values.
        spec: Credential being resolved.
        stored: Credential already held in the draft, when present.

    Returns:
        The resolved credential, or ``None`` when it is unconfigured or cleared.
    """
    action = str(cleaned_data.get(spec.action_field) or "").strip()
    if action == "clear":
        return None

    raw_path = _optional_text(cleaned_data.get(spec.path_field))
    pasted = _optional_text(cleaned_data.get(spec.pem_field))
    uploaded = _uploaded_text(form, spec)
    supplied = [value for value in (raw_path, pasted, uploaded) if value is not None]
    if len(supplied) > 1:
        form.add_error(
            spec.path_field,
            f"Supply the {spec.label} using only one of a file path, pasted text, or an upload.",
        )
        return None
    if not supplied:
        # An untouched credential keeps whatever the draft already holds,
        # unless the participant explicitly asked to replace it.
        return None if action == "replace" else stored

    if raw_path is not None:
        path = Path(raw_path)
        if not path.is_absolute():
            form.add_error(spec.path_field, "Enter an absolute file path.")
            return None
        return credential_from_path(path)

    text = pasted if pasted is not None else uploaded
    error_field = spec.pem_field if pasted is not None else spec.file_field
    try:
        return credential_from_inline(validate_inline_material(str(text), kind=spec.kind))
    except CredentialError as error:
        form.add_error(error_field, str(error))
        return None


def _uploaded_text(form: forms.Form, spec: CredentialFieldSpec) -> str | None:
    """Read an uploaded credential file as text without exposing its contents.

    Args:
        form: Bound form carrying uploaded files.
        spec: Credential being resolved.

    Returns:
        Decoded file text, or ``None`` when no file was uploaded or it could
        not be read as UTF-8 text.
    """
    upload = form.cleaned_data.get(spec.file_field) if hasattr(form, "cleaned_data") else None
    if upload is None:
        return None
    if upload.size is not None and upload.size > MAX_INLINE_CREDENTIAL_BYTES:
        form.add_error(
            spec.file_field,
            f"Upload a credential file no larger than {MAX_INLINE_CREDENTIAL_BYTES} bytes.",
        )
        return None
    try:
        return bytes(upload.read()).decode("utf-8")
    except OSError, UnicodeDecodeError:
        form.add_error(spec.file_field, "Upload a UTF-8 encoded PEM or JWS file.")
        return None


def _optional_text(value: object) -> str | None:
    """Return a submitted value as non-empty text.

    Args:
        value: Raw cleaned form value.

    Returns:
        The stripped text, or ``None`` when blank or not a string.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def set_credential_keys(
    target: JsonObject,
    material: CredentialMaterial | None,
    *,
    path_key: str,
    inline_key: str,
) -> None:
    """Write one credential into a config object under the correct key.

    Args:
        target: Config object to update in place.
        material: Resolved credential, or ``None`` to write nothing.
        path_key: Key used when the credential is a path reference.
        inline_key: Key used when the credential carries inline material.
    """
    target.pop(path_key, None)
    target.pop(inline_key, None)
    if material is None:
        return
    if material.inline is not None:
        target[inline_key] = material.inline
    else:
        target[path_key] = str(material.path)


@dataclass(frozen=True)
class CredentialState:
    """Non-secret presentation state for one credential field.

    Attributes:
        name: Field-name stem the state belongs to.
        label: Participant-facing credential name.
        configured: Whether the draft already holds this credential.
        inline: Whether the stored credential is inline material.
        descriptor: Non-secret summary shown beside the "Configured" badge.
        path: Stored path reference, safe to re-render, when present.
    """

    name: str
    label: str
    configured: bool
    inline: bool
    descriptor: str | None
    path: str | None


def credential_state(spec: CredentialFieldSpec, material: CredentialMaterial | None) -> CredentialState:
    """Build the non-secret presentation state for one credential.

    Args:
        spec: Credential being presented.
        material: Stored credential, when configured.

    Returns:
        Presentation state containing no credential material.
    """
    return CredentialState(
        name=spec.name,
        label=spec.label,
        configured=material is not None,
        inline=material is not None and material.is_inline,
        descriptor=describe_credential(material, kind=spec.kind),
        path=str(material.path) if material is not None and material.path is not None else None,
    )
