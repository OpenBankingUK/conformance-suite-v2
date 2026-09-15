"""Tests for the coordinator-owned migrated catalogue release registry."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conformance.configuration_contracts import (
    compile_participant_plan,
    load_participant_plan,
    load_requirements_catalogue,
    load_suite_release,
    load_test_definition_catalogue,
    verify_suite_release_artifacts,
)
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.unit

_REGISTRY_PATH = (
    REPO_ROOT / "conformance" / "configuration_contracts" / "bundles" / "open-banking-mvp" / "suite-release.json"
)
_CATALOGUE_ROOT = REPO_ROOT / "conformance" / "configuration_contracts" / "catalogues"
_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "configuration_contracts"
_CATALOGUE_CASES = (
    ("pis", "v3_1_11", "obl.pis-v311.requirements", "obl.pis-v311.tests"),
    ("pis", "v4_0_1", "obl.pis-v401.requirements", "obl.pis-v401.tests"),
    ("ais", "v3_1_11", "obl.ais-v311.requirements", "obl.ais-v311.tests"),
    ("ais", "v4_0_1", "obl.ais-v401.requirements", "obl.ais-v401.tests"),
    ("cbpii", "v3_1_11", "obl.cbpii-v311.requirements", "obl.cbpii-v311.tests"),
    ("cbpii", "v4_0_1", "obl.cbpii-v401.requirements", "obl.cbpii-v401.tests"),
    ("vrp", "v3_1_11", "obl.vrp-v311.requirements", "obl.vrp-v311.tests"),
    ("vrp", "v4_0_1", "obl.vrp-v401.requirements", "obl.vrp-v401.tests"),
    ("dcr", "v3_4", "obl.dcr-v34.requirements", "obl.dcr-v34.tests"),
)
_TECHNICAL_SOURCE_IDS = {
    "ob-rw-v311.payment-initiation-openapi",
    "ob-rw-v401.payment-initiation-openapi",
    "ob-rw-v311.account-info-openapi",
    "ob-rw-v401.account-info-openapi",
    "ob-rw-v311.confirmation-funds-openapi",
    "ob-rw-v401.confirmation-funds-openapi",
    "ob-rw-v311.vrp-openapi",
    "ob-rw-v401.vrp-openapi",
    "ob-dcr-v34.openapi",
}
_SCHEMA_IDS = {
    "requirements-catalogue-v1",
    "test-definition-catalogue-v1",
    "participant-plan-v1",
    "resolved-plan-v1",
    "execution-manifest-v1",
}


def test_registry_binds_the_exact_migrated_matrix_and_current_bytes() -> None:
    suite_release = load_suite_release(_REGISTRY_PATH)
    artifacts_by_kind = {
        kind: {str(artifact.id) for artifact in suite_release.artifacts if artifact.kind == kind}
        for kind in ("requirements-catalogue", "test-definition-catalogue", "technical-source", "json-schema")
    }

    assert suite_release.id == "obl.open-banking-mvp.catalogue-release"
    assert artifacts_by_kind["requirements-catalogue"] == {case[2] for case in _CATALOGUE_CASES}
    assert artifacts_by_kind["test-definition-catalogue"] == {case[3] for case in _CATALOGUE_CASES}
    assert artifacts_by_kind["technical-source"] == _TECHNICAL_SOURCE_IDS
    assert artifacts_by_kind["json-schema"] == _SCHEMA_IDS
    assert len(suite_release.artifacts) == 32

    artifact_bytes = {
        (str(artifact.kind), str(artifact.id)): (REPO_ROOT / artifact.uri).read_bytes()
        for artifact in suite_release.artifacts
    }
    assert verify_suite_release_artifacts(suite_release, artifact_bytes) == ()


@pytest.mark.parametrize(("family", "version", "requirements_id", "tests_id"), _CATALOGUE_CASES)
def test_every_registered_catalogue_compiles_against_the_release(
    family: str,
    version: str,
    requirements_id: str,
    tests_id: str,
) -> None:
    suite_release = load_suite_release(_REGISTRY_PATH)
    catalogue_root = _CATALOGUE_ROOT / family / version
    requirements = load_requirements_catalogue(catalogue_root / "requirements.json")
    test_definitions = load_test_definition_catalogue(catalogue_root / "test-definitions.json")
    participant_plan = load_participant_plan(_FIXTURE_ROOT / family / version / "participant-plan.json")
    participant_plan = replace(participant_plan, suite_release_id=suite_release.id)

    assert requirements.id == requirements_id
    assert test_definitions.id == tests_id
    resolved_plan = compile_participant_plan(
        suite_release,
        requirements,
        test_definitions,
        participant_plan,
    )
    assert resolved_plan.selection_valid is True
    assert resolved_plan.provenance.suite_release_id == suite_release.id
    assert resolved_plan.provenance.requirements_catalogue_id == requirements.id
    assert resolved_plan.provenance.test_definition_catalogue_id == test_definitions.id
    assert resolved_plan.test_instances
