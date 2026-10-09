"""Human-readable console summary for CLI conformance runs.

The structured result JSON remains the single authoritative artefact (and
``python -m conformance.result_gate`` the automation gate). This module only
renders a concise stdout digest from that JSON so a participant or pipeline
log reader can see the overall outcome and where to start diagnosing
failures without opening the file.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from conformance.cli_console import colour_label
from conformance.json_types import JsonObject, JsonValue

MAX_LISTED_STEPS = 20
"""Maximum failed steps listed individually before the summary truncates."""

_MAX_MESSAGE_LENGTH = 240
"""Per-step message length shown on the console; the result file keeps the full text."""


def render_run_summary(
    result: JsonObject,
    *,
    run_label: str,
    result_path: Path,
    execution_log_path: Path,
    colour: bool = False,
) -> str:
    """Render the end-of-run console summary.

    Args:
        result: Structured result JSON object exactly as written to disk.
        run_label: Short description of what ran (for example the plan path).
        result_path: Path the result JSON was written to.
        execution_log_path: Path the NDJSON execution log was written to.
        colour: Whether the CLI's stdout supports ANSI status labels.

    Returns:
        Multi-line plain-text summary ending with a newline.
    """
    status = str(result.get("status", "unknown")).upper()
    styled_status = colour_label(status, "passed" if status == "PASSED" else "failed", enabled=colour)
    lines = [f"Conformance run {styled_status}: {run_label}"]
    catalogue = _object(result.get("catalogue"))
    if catalogue:
        lines.append(
            "  Catalogue: "
            + " ".join(str(catalogue[key]) for key in ("standard", "version", "api") if key in catalogue)
        )
    summary = _object(result.get("summary"))
    lines.append(
        "  Steps: "
        + ", ".join(f"{summary.get(key, 0)} {key}" for key in ("total", "passed", "failed", "warn", "skipped"))
    )
    psu = _object(_object(result.get("execution")).get("psuAuthorization"))
    if psu:
        lines.append(f"  PSU authorisation: {_describe_psu(psu)}")
    lines.extend(_eligibility_lines(_object(result.get("certificationEligibility"))))

    steps = [step for step in _list(result.get("steps")) if isinstance(step, dict)]
    failed = [step for step in steps if step.get("status") == "failed"]
    if failed:
        lines.append("")
        lines.append(colour_label(f"Failed steps ({len(failed)}):", "failed", enabled=colour))
        lines.extend(_step_line(step) for step in failed[:MAX_LISTED_STEPS])
        if len(failed) > MAX_LISTED_STEPS:
            lines.append(f"  ... and {len(failed) - MAX_LISTED_STEPS} more (see result file)")
    skipped = sum(1 for step in steps if step.get("status") == "skipped")
    if skipped:
        lines.append(f"Skipped steps: {skipped} (dependent on earlier failures or not selected; see result file)")

    lines.append("")
    lines.append(f"Result file:   {result_path}")
    lines.append(f"Execution log: {execution_log_path}")
    if failed:
        lines.append("Diagnose: open the result file and inspect each failed step's details.request/response evidence.")
    lines.append(f"Pipeline gate: python -m conformance.result_gate {result_path}")
    return "\n".join(lines) + "\n"


def _describe_psu(psu: JsonObject) -> str:
    """Describe the PSU authorisation mode and configured names."""
    description = str(psu.get("mode", "manual"))
    extras = []
    header_names = _names(psu.get("headerNames"))
    parameter_names = _names(psu.get("parameterNames"))
    if header_names:
        extras.append(f"headers: {', '.join(header_names)}")
    if parameter_names:
        extras.append(f"parameters: {', '.join(parameter_names)}")
    return f"{description} ({'; '.join(extras)})" if extras else description


def _eligibility_lines(eligibility: JsonObject) -> list[str]:
    """Render certification-eligibility status and its blocking reasons."""
    if not eligibility:
        return []
    if eligibility.get("eligible") is True:
        return ["  Certification eligible: yes"]
    reasons = _names(eligibility.get("reasons"))
    lines = ["  Certification eligible: no"]
    lines.extend(f"    - {reason}" for reason in reasons)
    return lines


def _step_line(step: JsonObject) -> str:
    """Render one failed step as a single diagnostic line."""
    name = str(step.get("name", "<unnamed>"))
    status_code = step.get("statusCode")
    http = f" [HTTP {status_code}]" if isinstance(status_code, int) else ""
    message = " ".join(str(step.get("message", "")).split())
    if len(message) > _MAX_MESSAGE_LENGTH:
        message = message[: _MAX_MESSAGE_LENGTH - 3] + "..."
    return f"  x {name}{http}: {message}"


def _object(value: JsonValue | None) -> JsonObject:
    """Return ``value`` when it is a JSON object, otherwise an empty object."""
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue | None) -> Sequence[JsonValue]:
    """Return ``value`` when it is a JSON array, otherwise an empty list."""
    return value if isinstance(value, list) else []


def _names(value: JsonValue | None) -> list[str]:
    """Return the string members of a JSON array."""
    return [item for item in _list(value) if isinstance(item, str)]
