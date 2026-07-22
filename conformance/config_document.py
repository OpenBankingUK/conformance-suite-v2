"""Parse participant config documents with optional embedded test plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError, ModelBankConfig, parse_model_bank_config
from conformance.participant_test_plan import (
    ParticipantTestPlan,
    ParticipantTestPlanParseError,
    parse_participant_test_plan,
    run_plan_v2_from_participant_test_plan,
)
from conformance.run_plan_v2 import RunPlanV2


@dataclass(frozen=True)
class ParticipantConfigDocument:
    """Validated participant config document.

    Attributes:
        config: Strictly validated participant runtime config.
        test_plan: Optional embedded participant test-plan intent from the
            top-level ``testPlan`` key.
    """

    config: ModelBankConfig
    test_plan: ParticipantTestPlan | None = None


class ConfigDocumentError(ConfigError):
    """Raised when an enhanced participant config document is invalid."""


def load_participant_config_document(config_path: Path) -> ParticipantConfigDocument:
    """Load a participant config document from disk.

    Args:
        config_path: Path to the JSON config document.

    Returns:
        Parsed config document containing the strict config and optional
        embedded participant test plan.

    Raises:
        ConfigDocumentError: If the file cannot be read, parsed, or validated.
    """
    resolved_config_path = config_path.resolve()
    try:
        raw_config = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigDocumentError(f"Invalid JSON config: {error.msg}") from error
    except OSError as error:
        raise ConfigDocumentError(f"Unable to read config file: {error}") from error

    if not isinstance(raw_config, dict):
        raise ConfigDocumentError("Config root must be a JSON object")

    return parse_participant_config_document(
        raw_config,
        base_dir=resolved_config_path.parent,
        output_base_dir=Path.cwd(),
    )


def parse_participant_config_document(
    raw_document: dict[str, JsonValue],
    *,
    base_dir: Path,
    output_base_dir: Path | None = None,
) -> ParticipantConfigDocument:
    """Parse an enhanced participant config document.

    Accepts the existing participant config shape with an optional top-level
    ``testPlan`` object.  The embedded plan is stripped before strict config
    validation so unsupported config fields remain rejected by
    :func:`conformance.model_bank_config.parse_model_bank_config`.

    Args:
        raw_document: Parsed JSON object supplied by a participant.
        base_dir: Directory used to resolve participant-controlled file paths.
        output_base_dir: Directory used to resolve result output paths.

    Returns:
        Parsed config document with a strict ``ModelBankConfig`` and optional
        embedded participant test plan.

    Raises:
        ConfigDocumentError: If the document is a bare retired plan, the embedded
            plan is invalid, strict config validation fails, or config
            ``testTarget`` coordinates conflict with the embedded plan.
    """
    if _looks_like_bare_run_plan_v2(raw_document):
        raise ConfigDocumentError(
            "Config JSON appears to be a retired bare RunPlanV2 document. Rebuild/export a participant "
            "config JSON with a top-level 'testPlan' section."
        )
    if "runPlan" in raw_document:
        raise ConfigDocumentError(
            "The top-level 'runPlan' field is no longer supported. Rebuild/export the config JSON "
            "and use top-level 'testPlan'."
        )

    raw_config = dict(raw_document)
    raw_test_plan = raw_config.pop("testPlan", None)
    test_plan = _parse_embedded_test_plan(raw_test_plan)
    _validate_dcr_section_matches_target(raw_config=raw_config, test_plan=test_plan)

    try:
        config = parse_model_bank_config(raw_config, base_dir=base_dir, output_base_dir=output_base_dir)
    except ConfigError as error:
        raise ConfigDocumentError(str(error)) from error

    if test_plan is not None:
        _validate_target_matches_embedded_plan(config=config, plan=test_plan)

    return ParticipantConfigDocument(config=config, test_plan=test_plan)


def _validate_dcr_section_matches_target(
    *,
    raw_config: dict[str, JsonValue],
    test_plan: ParticipantTestPlan | None,
) -> None:
    """Reject DCR runtime sections on non-DCR target plans.

    Args:
        raw_config: Top-level config document after removing any embedded
            ``testPlan`` section.
        test_plan: Parsed embedded participant test plan, or ``None`` when the
            config omits ``testPlan``.

    Raises:
        ConfigDocumentError: If the config contains a top-level ``dcr`` section
            while its target specification is not Dynamic Client Registration.
    """
    if "dcr" not in raw_config:
        return

    target_specification = _raw_config_target_specification(raw_config)
    if target_specification is None and test_plan is not None:
        target_specification = test_plan.target.specification
    if target_specification is None or target_specification == "dynamic-client-registration":
        return

    raise ConfigDocumentError(
        "The top-level 'dcr' section is only valid for dynamic-client-registration test plans; "
        f"this config targets {target_specification!r}. Remove the 'dcr' section or rebuild/export "
        "the config for a Dynamic Client Registration test plan."
    )


def _raw_config_target_specification(raw_config: dict[str, JsonValue]) -> str | None:
    """Return the raw ``testTarget.specification`` value when present.

    Args:
        raw_config: Top-level config document after removing any embedded
            ``testPlan`` section.

    Returns:
        The raw target specification string, or ``None`` when it is absent or
        not a string.
    """
    raw_target = raw_config.get("testTarget")
    if not isinstance(raw_target, dict):
        return None
    specification = raw_target.get("specification")
    return specification if isinstance(specification, str) else None


def resolve_config_document_execution_plan(
    document: ParticipantConfigDocument,
) -> RunPlanV2 | None:
    """Resolve the internal executable plan for a participant config document.

    Args:
        document: Parsed participant config document.

    Returns:
        Internal catalogue-planner intent adapted from the embedded
        ``testPlan``, or ``None`` when no plan is available from the document.
    """
    if document.test_plan is None:
        return None
    return run_plan_v2_from_participant_test_plan(document.test_plan)


def _parse_embedded_test_plan(raw_test_plan: JsonValue | None) -> ParticipantTestPlan | None:
    """Parse an optional embedded participant test-plan object.

    Args:
        raw_test_plan: Raw top-level ``testPlan`` value, or ``None`` if absent.

    Returns:
        Parsed participant test plan, or ``None`` when the config omits
        ``testPlan``.

    Raises:
        ConfigDocumentError: If ``testPlan`` is present but cannot be parsed.
    """
    if raw_test_plan is None:
        return None
    try:
        return parse_participant_test_plan(raw_test_plan)
    except ParticipantTestPlanParseError as error:
        raise ConfigDocumentError(f"testPlan is invalid: {error}") from error


def _validate_target_matches_embedded_plan(*, config: ModelBankConfig, plan: ParticipantTestPlan) -> None:
    """Validate config target coordinates against an embedded test plan.

    Args:
        config: Strict participant config parsed from the same document.
        plan: Embedded participant test plan parsed from ``testPlan``.

    Raises:
        ConfigDocumentError: If ``config.testTarget`` is present and conflicts
            with the embedded plan target coordinates or resource groups.
    """
    target = config.test_target
    if target is None:
        return

    mismatches: list[str] = []
    comparisons = (
        ("standard", target.standard, plan.target.standard),
        ("specification", target.specification, plan.target.specification),
        ("securityProfile", target.security_profile, plan.target.security_profile),
        ("specificationVersion", target.specification_version, plan.target.specification_version),
        ("resourceGroups", target.resource_groups, plan.resource_groups),
    )
    for field_name, config_value, plan_value in comparisons:
        if config_value != plan_value:
            mismatches.append(f"{field_name} config={config_value!r} testPlan={plan_value!r}")

    if mismatches:
        raise ConfigDocumentError("config.testTarget does not match embedded testPlan target: " + "; ".join(mismatches))


def _looks_like_bare_run_plan_v2(raw_document: dict[str, JsonValue]) -> bool:
    """Return whether a JSON object appears to be a bare RunPlanV2 document.

    Args:
        raw_document: Top-level parsed JSON object.

    Returns:
        ``True`` when the object has the RunPlanV2 discriminator and target
        shape at the config root.
    """
    return raw_document.get("schemaVersion") == "2" and isinstance(raw_document.get("target"), dict)
