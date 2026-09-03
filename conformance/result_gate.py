"""Operational gate for structured conformance result artifacts."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from conformance.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)

_RESULT_STATUSES = frozenset({"passed", "failed", "warn", "skipped"})
"""Statuses accepted in structured conformance result evidence."""


class StructuredResultGateError(ValueError):
    """Raised when a structured result is malformed or not conformant."""


def validate_structured_conformance_result(raw_result: object) -> JsonObject:
    """Validate a structured result as successful conformance evidence.

    This gate deliberately ignores console transcripts. It requires the
    machine-readable aggregate, summary, step outcomes, and any hierarchical
    scenario/case/step trace to contain zero failures.

    Args:
        raw_result: Decoded result JSON.

    Returns:
        Compact validated counts for workflow summaries.

    Raises:
        StructuredResultGateError: If the result is malformed, inconsistent, or
            contains a failed conformance outcome.
    """
    result = _object(raw_result, location="result")
    if result.get("status") != "passed":
        raise StructuredResultGateError("result.status must be 'passed'")
    summary = _object(result.get("summary"), location="result.summary")
    reported_failed = _non_negative_integer(summary.get("failed"), location="result.summary.failed")
    if reported_failed != 0:
        raise StructuredResultGateError("result.summary.failed must be zero")

    steps = _array(result.get("steps"), location="result.steps")
    if not steps:
        raise StructuredResultGateError("result.steps must contain at least one conformance step")
    step_statuses = _statuses(steps, location="result.steps")
    computed_failed = sum(status == "failed" for status in step_statuses)
    if computed_failed:
        raise StructuredResultGateError(f"result.steps contains {computed_failed} failed conformance step(s)")
    if computed_failed != reported_failed:
        raise StructuredResultGateError("result.summary.failed does not match result.steps")

    case_count = 0
    catalogue = result.get("catalogue")
    if catalogue is not None:
        catalogue_object = _object(catalogue, location="result.catalogue")
        trace_groups = catalogue_object.get("traceGroups")
        is_dcr_result = catalogue_object.get("api") in {"dcr", "dynamic-client-registration"}
        if is_dcr_result and trace_groups is None:
            raise StructuredResultGateError("DCR result.catalogue.traceGroups is required")
        if trace_groups is not None:
            for group_index, raw_group in enumerate(_array(trace_groups, location="result.catalogue.traceGroups")):
                group = _object(raw_group, location=f"result.catalogue.traceGroups[{group_index}]")
                group_status = _status(group, location=f"result.catalogue.traceGroups[{group_index}]")
                if group_status == "failed":
                    raise StructuredResultGateError(
                        f"result.catalogue.traceGroups[{group_index}] is a failed conformance scenario"
                    )
                cases = _array(
                    group.get("testCases"),
                    location=f"result.catalogue.traceGroups[{group_index}].testCases",
                )
                case_count += len(cases)
                _reject_failed_trace_cases(cases, group_index=group_index)
        if is_dcr_result and case_count == 0:
            raise StructuredResultGateError("DCR result must contain traceable conformance cases")

    return {
        "status": "passed",
        "failed": 0,
        "stepCount": len(steps),
        "caseCount": case_count,
    }


def run(argv: Sequence[str] | None = None) -> int:
    """Validate one structured result file for automation.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero for conformant evidence, one for conformance failure, and two for
        malformed or unreadable input.
    """
    parser = argparse.ArgumentParser(description="Require a passing structured conformance result")
    parser.add_argument("result", type=Path, help="Path to structured conformance result JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    try:
        raw_result = json.loads(args.result.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        logger.error("Structured result JSON error: %s", error.msg)
        return 2
    except OSError as error:
        logger.error("Unable to read structured result: %s", error)
        return 2
    try:
        validated = validate_structured_conformance_result(raw_result)
    except StructuredResultGateError as error:
        logger.error("Structured conformance gate failed: %s", error)
        return 1
    logger.info(
        "Structured conformance gate passed: %s steps, %s cases, zero failures",
        validated["stepCount"],
        validated["caseCount"],
    )
    return 0


def _reject_failed_trace_cases(cases: list[object], *, group_index: int) -> None:
    """Reject failed cases or nested steps in one trace group.

    Args:
        cases: Raw trace-case objects.
        group_index: Parent trace-group index used in errors.

    Raises:
        StructuredResultGateError: If a case or nested step failed.
    """
    for case_index, raw_case in enumerate(cases):
        location = f"result.catalogue.traceGroups[{group_index}].testCases[{case_index}]"
        case = _object(raw_case, location=location)
        if _status(case, location=location) == "failed":
            raise StructuredResultGateError(f"{location} is a failed conformance case")
        nested_steps = _array(case.get("steps"), location=f"{location}.steps")
        if "failed" in _statuses(nested_steps, location=f"{location}.steps"):
            raise StructuredResultGateError(f"{location}.steps contains a failed conformance step")


def _object(value: object, *, location: str) -> Mapping[str, object]:
    """Require a JSON object.

    Args:
        value: Decoded value.
        location: Result location used in errors.

    Returns:
        Object mapping.

    Raises:
        StructuredResultGateError: If the value is not an object.
    """
    if not isinstance(value, dict):
        raise StructuredResultGateError(f"{location} must be a JSON object")
    return value


def _array(value: object, *, location: str) -> list[object]:
    """Require a JSON array.

    Args:
        value: Decoded value.
        location: Result location used in errors.

    Returns:
        Array values.

    Raises:
        StructuredResultGateError: If the value is not an array.
    """
    if not isinstance(value, list):
        raise StructuredResultGateError(f"{location} must be a JSON array")
    return value


def _status(value: Mapping[str, object], *, location: str) -> str:
    """Require a known status from one result object.

    Args:
        value: Result, group, case, or step object.
        location: Result location used in errors.

    Returns:
        Validated status.

    Raises:
        StructuredResultGateError: If status is missing or unknown.
    """
    status = value.get("status")
    if not isinstance(status, str) or status not in _RESULT_STATUSES:
        raise StructuredResultGateError(f"{location}.status must be one of: {', '.join(sorted(_RESULT_STATUSES))}")
    return status


def _statuses(values: list[object], *, location: str) -> list[str]:
    """Read statuses from an array of result objects.

    Args:
        values: Raw result entries.
        location: Parent location used in errors.

    Returns:
        Validated statuses in source order.
    """
    return [
        _status(_object(value, location=f"{location}[{index}]"), location=f"{location}[{index}]")
        for index, value in enumerate(values)
    ]


def _non_negative_integer(value: JsonValue | object, *, location: str) -> int:
    """Require a non-negative JSON integer.

    Args:
        value: Decoded value.
        location: Result location used in errors.

    Returns:
        Validated integer.

    Raises:
        StructuredResultGateError: If the value is not a non-negative integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StructuredResultGateError(f"{location} must be a non-negative integer")
    return value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run())
