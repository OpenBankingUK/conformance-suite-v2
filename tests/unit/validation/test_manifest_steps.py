"""Parsing of v1 step structure, optional step metadata, and certification coverage."""

from typing import NamedTuple, cast

import pytest

from conformance.json_types import JsonValue
from conformance.manifest import (
    CertificationCoverage,
    ManifestError,
    ManifestStep,
    PsuAuthorizationStep,
    StepPhase,
    parse_manifest,
)
from tests.support.manifest_documents import v1_manifest, v1_single_step_manifest

pytestmark = pytest.mark.unit


def test_parse_v1_manifest_accepts_minimal_multi_step() -> None:
    raw_manifest = v1_manifest()
    manifest = parse_manifest(raw_manifest)

    assert manifest.schema_version == "v1"
    assert manifest.name == "Ozone OpenID discovery and JWKS (v1)"
    assert len(manifest.steps) == 2
    assert manifest.steps[0].id == "openid-discovery"
    assert (
        cast("ManifestStep", manifest.steps[0]).request.url
        == "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration"
    )
    assert manifest.steps[1].id == "jwks-fetch"
    assert cast("ManifestStep", manifest.steps[1]).request.url == "${steps.openid-discovery.response.body.jwks_uri}"


def test_parse_v1_manifest_rejects_duplicate_step_ids() -> None:
    raw_manifest = v1_manifest()
    steps = cast("list[dict[str, JsonValue]]", raw_manifest["steps"])
    steps[1]["id"] = "openid-discovery"

    with pytest.raises(ManifestError, match=r"steps\[1\]\.id 'openid-discovery' is a duplicate"):
        parse_manifest(raw_manifest)


def test_parse_v1_manifest_rejects_unknown_keys_at_root() -> None:
    raw_manifest = v1_manifest()
    raw_manifest["tests"] = []

    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(raw_manifest)


@pytest.mark.parametrize("bad_value", ["", "full", "none", "PARTIAL", 1, True, None])
def test_parse_v1_manifest_rejects_invalid_coverage_value(bad_value: JsonValue) -> None:
    """certificationCoverage must be exactly partial or complete — any other value is rejected."""
    raw_manifest = v1_manifest()
    raw_manifest["certificationCoverage"] = bad_value

    with pytest.raises(ManifestError, match="certificationCoverage must be one of: partial, complete"):
        parse_manifest(raw_manifest)


@pytest.mark.parametrize("bad_warning", ["", "   ", 42, None, []])
def test_parse_v1_step_warning_rejects_non_string_or_empty(bad_warning: JsonValue) -> None:
    """A ``warning`` field that is not a non-empty string fails parse."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-warn",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "warning": bad_warning,
            }
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.warning must be a non-empty string"):
        parse_manifest(raw_manifest)


@pytest.mark.parametrize("bad_value", [1, 0, "true", "false", None, []])
def test_parse_v1_step_mandatory_rejects_non_boolean(bad_value: JsonValue) -> None:
    """``mandatory`` must be a JSON boolean; truthy coercion is rejected."""
    raw_manifest: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-mandatory",
        "steps": [
            {
                "id": "first",
                "name": "First step",
                "request": {"method": "GET", "url": "https://example.com/a"},
                "assertions": [{"type": "http_status", "expected": 200}],
                "mandatory": bad_value,
            }
        ],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.mandatory must be a JSON boolean"):
        parse_manifest(raw_manifest)


def test_parse_v1_step_rejects_unknown_kind() -> None:
    """Unknown ``kind`` values fail at parse time."""
    raw: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-kind",
        "steps": [{"kind": "telepathy", "id": "first", "name": "first"}],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.kind must be one of"):
        parse_manifest(raw)


def test_parse_v1_step_rejects_non_string_kind() -> None:
    """``kind`` must be a string when present."""
    raw: dict[str, JsonValue] = {
        "schemaVersion": "v1",
        "name": "bad-kind-type",
        "steps": [{"kind": 7, "id": "first", "name": "first"}],
    }
    with pytest.raises(ManifestError, match=r"steps\[0\]\.kind must be a string"):
        parse_manifest(raw)


GET_STEP_REQUEST: dict[str, JsonValue] = {"method": "GET", "url": "https://example.com/a"}
"""Request document used by the step-metadata matrices, which never vary the request."""


COVERAGE_ACCEPTANCES = (
    pytest.param(None, "partial", id="absent-defaults-to-partial"),
    pytest.param("partial", "partial", id="explicit-partial"),
    pytest.param("complete", "complete", id="complete"),
)
"""``certificationCoverage`` values a v1 manifest accepts, with the parsed result."""


@pytest.mark.parametrize(("declared", "expected"), COVERAGE_ACCEPTANCES)
def test_parse_v1_manifest_accepts_certification_coverage(
    declared: str | None,
    expected: CertificationCoverage,
) -> None:
    """Certification coverage defaults to partial and round-trips when declared.

    Defaulting to ``partial`` is the safe direction: a manifest never claims
    complete certification coverage unless it says so explicitly.

    Args:
        declared: Raw ``certificationCoverage`` value, or ``None`` to omit it.
        expected: Coverage value the parsed manifest must carry.
    """
    document = v1_manifest()
    if declared is None:
        assert "certificationCoverage" not in document
    else:
        document["certificationCoverage"] = declared

    manifest = parse_manifest(document)

    coverage: CertificationCoverage = manifest.certification_coverage
    assert coverage == expected


class StepAcceptance(NamedTuple):
    """Optional step metadata and the parsed ``ManifestStep`` fields it must yield.

    Attributes:
        step_fields: Raw step fields merged into a valid one-step manifest.
        expected_warning: Parsed ``warning`` value.
        expected_mandatory: Parsed ``mandatory`` flag.
        expected_group: Parsed execution group.
        expected_phase: Parsed scheduling phase.
    """

    step_fields: dict[str, JsonValue]
    expected_warning: str | None = None
    expected_mandatory: bool = False
    expected_group: str = "default"
    expected_phase: StepPhase = "execution"


STEP_ACCEPTANCES = (
    pytest.param(StepAcceptance(step_fields={}), id="omitted-metadata-defaults"),
    pytest.param(
        StepAcceptance(
            step_fields={"warning": "Use endpoint /b instead (deprecated in v4.1)"},
            expected_warning="Use endpoint /b instead (deprecated in v4.1)",
        ),
        id="warning",
    ),
    pytest.param(
        StepAcceptance(step_fields={"mandatory": True}, expected_mandatory=True),
        id="mandatory-true",
    ),
    pytest.param(
        StepAcceptance(
            step_fields={"group": "bank_a", "phase": "setup"},
            expected_group="bank_a",
            expected_phase="setup",
        ),
        id="explicit-group-and-setup-phase",
    ),
)
"""Optional step metadata the v1 parser accepts, with the parsed step it yields."""


@pytest.mark.parametrize("case", STEP_ACCEPTANCES)
def test_parse_v1_step_accepts_optional_metadata(case: StepAcceptance) -> None:
    """Optional step metadata parses into the documented defaults or declared values.

    Args:
        case: Step metadata under test and the parsed fields it must yield.
    """
    manifest = parse_manifest(v1_single_step_manifest(request=GET_STEP_REQUEST, **case.step_fields))

    step = cast("ManifestStep", manifest.steps[0])
    assert step.warning == case.expected_warning
    assert step.mandatory is case.expected_mandatory
    assert step.group == case.expected_group
    assert step.phase == case.expected_phase


class StepRejection(NamedTuple):
    """Malformed step metadata and the parse error it must raise.

    Attributes:
        step_fields: Raw step fields merged into an otherwise valid one-step manifest.
        message: Regular expression the ``ManifestError`` message must match.
    """

    step_fields: dict[str, JsonValue]
    message: str


STEP_REJECTIONS = (
    pytest.param(
        StepRejection(
            step_fields={"phase": "parallel"},
            message=r"steps\[0\]\.phase must be one of: setup, execution",
        ),
        id="invalid-phase",
    ),
    pytest.param(
        StepRejection(
            step_fields={"group": "bad.group"},
            message=r"steps\[0\]\.group 'bad\.group' contains invalid characters",
        ),
        id="invalid-group",
    ),
    pytest.param(
        StepRejection(step_fields={"extra": "bad"}, message=r"Unknown steps\[0\] field"),
        id="unknown-step-field",
    ),
    pytest.param(
        StepRejection(
            step_fields={"clientId": "should-not-be-here"},
            message=r"Unknown steps\[0\] field\(s\): clientId",
        ),
        id="psu-only-field-on-http-step",
    ),
)
"""Step metadata the v1 parser rejects, with the message each must produce."""


@pytest.mark.parametrize("case", STEP_REJECTIONS)
def test_parse_v1_step_rejects_invalid_metadata(case: StepRejection) -> None:
    """Malformed step metadata fails parse with a field-specific message.

    Args:
        case: Step metadata under test and the message it must produce.
    """
    with pytest.raises(ManifestError, match=case.message):
        parse_manifest(v1_single_step_manifest(request=GET_STEP_REQUEST, **case.step_fields))


@pytest.mark.parametrize("kind", [pytest.param(None, id="omitted"), pytest.param("http", id="explicit-http")])
def test_parse_v1_step_kind_selects_an_http_step(kind: str | None) -> None:
    """Omitting ``kind`` is equivalent to declaring ``http``.

    Args:
        kind: Raw ``kind`` value, or ``None`` to omit the field.
    """
    step_fields: dict[str, JsonValue] = {} if kind is None else {"kind": kind}

    manifest = parse_manifest(v1_single_step_manifest(request=GET_STEP_REQUEST, **step_fields))

    assert not isinstance(manifest.steps[0], PsuAuthorizationStep)
