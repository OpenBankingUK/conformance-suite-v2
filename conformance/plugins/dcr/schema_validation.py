"""DCR registration response schema validation per specification version.

Open Banking UK DCR specification versions 3.2, 3.3, and 3.4 each define a
set of required fields that must appear in the POST /register 201 response.
This module validates that the ASPSP's response contains all required fields
for the declared specification version.

Required fields per version (cumulative):

- **3.2**: ``client_id``, ``redirect_uris``, ``token_endpoint_auth_method``
- **3.3**: adds ``grant_types``, ``response_types``
- **3.4**: adds ``software_statement`` echo

Fields present in previous versions remain required in later versions.
"""

from __future__ import annotations

from typing import Final

from conformance.json_types import JsonObject

# ---------------------------------------------------------------------------
# Required-field sets per version
# ---------------------------------------------------------------------------

_DCR_3_2_REQUIRED: frozenset[str] = frozenset(
    {
        "client_id",
        "redirect_uris",
        "token_endpoint_auth_method",
    }
)
"""Required response fields for DCR specification version 3.2."""

_DCR_3_3_REQUIRED: frozenset[str] = _DCR_3_2_REQUIRED | frozenset(
    {
        "grant_types",
        "response_types",
    }
)
"""Required response fields for DCR specification version 3.3 (superset of 3.2)."""

_DCR_3_4_REQUIRED: frozenset[str] = _DCR_3_3_REQUIRED | frozenset(
    {
        "software_statement",
    }
)
"""Required response fields for DCR specification version 3.4 (superset of 3.3)."""

REQUIRED_FIELDS_BY_VERSION: Final[dict[str, frozenset[str]]] = {
    "3.2": _DCR_3_2_REQUIRED,
    "3.3": _DCR_3_3_REQUIRED,
    "3.4": _DCR_3_4_REQUIRED,
}
"""Mapping from DCR specification version string to its required response fields.

Keys are the version strings used in :class:`~conformance.target_config.TestTargetConfig`
(``"3.2"``, ``"3.3"``, ``"3.4"``).  Values are frozensets of required field
names that must be present in the POST /register 201 JSON response body.
"""

SUPPORTED_VERSIONS: Final[tuple[str, ...]] = ("3.2", "3.3", "3.4")
"""Tuple of DCR specification version strings supported by this validator."""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DcrSchemaValidationError(ValueError):
    """Raised when a DCR registration response fails schema validation.

    Wraps :class:`ValueError` so callers can catch either the specific error
    or the generic base class.

    Attributes:
        missing_fields: Sorted list of field names that were absent from
            the registration response.
        version: DCR specification version that was being validated.
    """

    missing_fields: list[str]
    version: str

    def __init__(self, *, missing_fields: list[str], version: str) -> None:
        """Initialise with the list of missing fields and the specification version.

        Args:
            missing_fields: Sorted list of field names absent from the
                registration response.
            version: DCR specification version under which validation failed.
        """
        super().__init__(
            f"DCR {version} registration response is missing required field(s): {', '.join(missing_fields)}"
        )
        self.missing_fields = missing_fields
        self.version = version


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_dcr_registration_response(body: JsonObject, version: str) -> None:
    """Validate that a registration response contains all version-required fields.

    Checks each field in the required set for the given version against the
    response body keys.  Only field presence is checked — field values are not
    validated here (value-level assertions are handled by the scenario runner).

    Args:
        body: Parsed JSON body from the POST /register response.
        version: DCR specification version string (e.g. ``"3.3"``).

    Raises:
        DcrSchemaValidationError: If one or more required fields are absent from
            ``body``.
        ValueError: If ``version`` is not one of the supported version strings.
    """
    required = _required_fields_for_version(version)
    missing = sorted(field for field in required if field not in body)
    if missing:
        raise DcrSchemaValidationError(missing_fields=missing, version=version)


def _required_fields_for_version(version: str) -> frozenset[str]:
    """Look up the required field set for a DCR specification version.

    Args:
        version: DCR specification version string.

    Returns:
        Frozenset of required field names.

    Raises:
        ValueError: If ``version`` is not in :data:`REQUIRED_FIELDS_BY_VERSION`.
    """
    required = REQUIRED_FIELDS_BY_VERSION.get(version)
    if required is None:
        raise ValueError(
            f"Unsupported DCR specification version {version!r}; expected one of {sorted(SUPPORTED_VERSIONS)}"
        )
    return required
