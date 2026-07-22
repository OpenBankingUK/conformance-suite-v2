"""Phase-10 validation tests for participant-facing docs and examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from conformance.config_document import parse_participant_config_document
from conformance.json_types import JsonObject

REPO_ROOT = Path(__file__).resolve().parents[1]
PARTICIPANT_EXAMPLE_CONFIGS = (
    "config/model-bank-rw-ais-example.json",
    "config/model-bank-dcr-example.json",
    "config/model-bank-pis-domestic-payment-starter-example.json",
    "config/model-bank-pis-fcs-legacy-benchmark-example.json",
    "config/model-bank-ais-certification-baseline-example.json",
    "config/model-bank-ais-certification-baseline-v4.0.1-example.json",
)
"""Participant-facing target/test-plan example configs guarded by phase-10 tests."""

CREDENTIAL_PATH_KEYS = frozenset(
    {
        "clientCertificatePath",
        "clientPrivateKeyPath",
        "caBundlePath",
        "signingCertificatePath",
        "signingPrivateKeyPath",
        "transportCertificatePath",
        "transportPrivateKeyPath",
        "ssaPath",
        "signatureTrustAnchorPath",
    }
)
"""Config keys whose values must be exact absolute credential file paths."""

RETIRED_PARTICIPANT_CONFIG_KEYS = frozenset(
    {
        "testSuite",
        "runPlan",
        "certificatePathRoot",
        "credentialPathRoot",
        "environment",
    }
)
"""Retired keys that committed participant examples must not advertise."""


def _load_json(path: Path) -> JsonObject:
    """Load a JSON object from disk.

    Args:
        path: Path to the JSON file.

    Returns:
        Parsed JSON object.
    """
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def _walk_json_objects(value: object) -> list[JsonObject]:
    """Return all JSON objects contained inside a decoded JSON value.

    Args:
        value: Decoded JSON value to inspect recursively.

    Returns:
        JSON objects found at the root or nested inside lists/dictionaries.
    """
    objects: list[JsonObject] = []
    if isinstance(value, dict):
        objects.append(cast(JsonObject, value))
        for child in value.values():
            objects.extend(_walk_json_objects(child))
    elif isinstance(value, list):
        for child in value:
            objects.extend(_walk_json_objects(child))
    return objects


@pytest.mark.unit
@pytest.mark.parametrize(
    "relative_path",
    PARTICIPANT_EXAMPLE_CONFIGS,
)
def test_participant_example_configs_use_target_contract(relative_path: str) -> None:
    """Participant example configs use `testTarget` and avoid legacy `testSuite`."""
    config_doc = _load_json(REPO_ROOT / relative_path)

    assert "testTarget" in config_doc
    assert "testSuite" not in config_doc


@pytest.mark.unit
@pytest.mark.parametrize("relative_path", PARTICIPANT_EXAMPLE_CONFIGS)
def test_participant_example_configs_parse_as_single_config_document(relative_path: str) -> None:
    """Committed participant examples use the shared config/test-plan parser."""
    config_doc = _load_json(REPO_ROOT / relative_path)

    document = parse_participant_config_document(config_doc, base_dir=REPO_ROOT / "config", output_base_dir=REPO_ROOT)

    assert document.config.test_target is not None


@pytest.mark.unit
@pytest.mark.parametrize("relative_path", PARTICIPANT_EXAMPLE_CONFIGS)
def test_participant_example_configs_avoid_retired_config_keys(relative_path: str) -> None:
    """Participant examples do not advertise retired run-plan/root/environment keys."""
    config_doc = _load_json(REPO_ROOT / relative_path)

    for json_object in _walk_json_objects(config_doc):
        assert RETIRED_PARTICIPANT_CONFIG_KEYS.isdisjoint(json_object)


@pytest.mark.unit
@pytest.mark.parametrize("relative_path", PARTICIPANT_EXAMPLE_CONFIGS)
def test_participant_example_credential_paths_are_absolute(relative_path: str) -> None:
    """Credential path placeholders in participant examples are exact absolute paths."""
    config_doc = _load_json(REPO_ROOT / relative_path)

    for json_object in _walk_json_objects(config_doc):
        for key, value in json_object.items():
            if key in CREDENTIAL_PATH_KEYS:
                assert isinstance(value, str)
                assert Path(value).is_absolute()


@pytest.mark.unit
def test_smoke_example_config_avoids_legacy_suite_inputs() -> None:
    """Smoke-check example avoids participant legacy suite/manifest inputs."""
    config_doc = _load_json(REPO_ROOT / "config" / "model-bank-example.json")

    assert "testSuite" not in config_doc
    assert "manifest" not in config_doc


@pytest.mark.unit
def test_readme_documents_target_test_plan_participant_contract() -> None:
    """README advertises target/test-plan inputs and rejects retired legacy inputs."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`testTarget`" in readme
    assert "top-level `testPlan`" in readme
    assert "--run-plan` are no longer supported" in readme
    assert "single participant config JSON document" in readme
    assert "does not include `environment`, `certificatePathRoot`, or `credentialPathRoot`" in readme
    assert "OpenID Provider discovery URL server-side" in readme
    assert "Credential material remains path-only" in readme
    assert "--manifest`, `--deselect`, and `--run-plan` are no longer supported" in readme
    assert "legacy `runPlan`, `manifest`, and `deselectStepIds` request fields are rejected" in readme
    assert "`--manifest` remains an explicit override" not in readme
    assert "inline `manifest` in `POST /api/runs/` wins" not in readme
    assert "`deselectStepIds` is accepted with inline manifests only" not in readme
