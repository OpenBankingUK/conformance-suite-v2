"""Structured result models for conformance smoke-check output."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from conformance.json_types import JsonObject, JsonValue

CheckStatus = Literal["passed", "failed", "warn", "skipped"]
"""Outcome values emitted by smoke-check steps and summaries.

Matches the four-state PRD outcome model: PASS, FAIL, WARN, SKIPPED.
``warn`` is emitted when an otherwise-passing v1 step declares a
``warning`` message in the manifest (signalling a deprecation or risk
that does not block certification). ``skipped`` is emitted when a v1
step cannot run because a prerequisite step produced no response.
"""


@dataclass(frozen=True)
class StepResult:
    """Result for a single observable conformance step.

    Attributes:
        name: Stable step identifier for consumers of the result JSON.
        status: Pass/fail outcome for this step.
        message: Human-readable summary of the step outcome.
        url: Optional endpoint URL involved in the step.
        status_code: Optional HTTP status code returned by the endpoint.
        details: Optional structured data safe to include in the result file.
        mandatory: Whether this step was declared mandatory in the manifest.
            Used by the aggregate ``certificationEligibility`` block; not
            serialised on the individual step entry to keep the per-step
            shape stable.
    """

    name: str
    status: CheckStatus
    message: str
    url: str | None = None
    status_code: int | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    mandatory: bool = False

    def __post_init__(self) -> None:
        """Freeze nested details so result objects stay immutable after creation."""
        object.__setattr__(self, "details", MappingProxyType(deepcopy(dict(self.details))))

    def to_json_object(self) -> JsonObject:
        """Convert the step result into the public JSON report shape.

        Returns:
            JSON object suitable for serialisation into the result file.
        """
        result: JsonObject = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.url is not None:
            result["url"] = self.url
        if self.status_code is not None:
            result["statusCode"] = self.status_code
        if self.details:
            result["details"] = deepcopy(dict(self.details))
        return result


@dataclass(frozen=True)
class SmokeCheckResult:
    """Complete result for a model-bank smoke-check execution.

    Attributes:
        environment: Environment name copied from the input config.
        status: Aggregate pass/fail outcome across all steps.
        started_at: UTC timestamp when execution started.
        finished_at: UTC timestamp when execution finished.
        steps: Ordered step results that explain the aggregate outcome.
    """

    environment: str
    status: CheckStatus
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepResult, ...]

    def to_json_object(self) -> JsonObject:
        """Convert the smoke-check result into the public JSON report shape.

        Returns:
            JSON object suitable for serialisation into the result file.
        """
        return {
            "environment": self.environment,
            "status": self.status,
            "startedAt": self.started_at.isoformat(),
            "finishedAt": self.finished_at.isoformat(),
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for step in self.steps if step.status == "passed"),
                "failed": sum(1 for step in self.steps if step.status == "failed"),
                "warn": sum(1 for step in self.steps if step.status == "warn"),
                "skipped": sum(1 for step in self.steps if step.status == "skipped"),
            },
            "certificationEligibility": _build_eligibility(self.steps),
            "steps": [step.to_json_object() for step in self.steps],
        }


def build_smoke_check_result(environment: str, steps: list[StepResult], *, started_at: datetime) -> SmokeCheckResult:
    """Build an aggregate smoke-check result from collected step outcomes.

    Args:
        environment: Environment name copied from the input config.
        steps: Ordered mutable list of step outcomes collected by the runner.
        started_at: UTC timestamp captured before execution began.

    Returns:
        Immutable smoke-check result with finished timestamp and aggregate status.
    """
    finished_at = datetime.now(UTC)
    # WARN is a non-blocking signal (PRD: "does not block certification"), so
    # a step with status=="warn" must not flip the aggregate run to "failed".
    # Only FAILED and SKIPPED (which always implies an earlier failure) fail
    # the run.
    status: CheckStatus = "passed" if all(step.status in {"passed", "warn"} for step in steps) else "failed"
    return SmokeCheckResult(
        environment=environment,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        steps=tuple(steps),
    )


def _build_eligibility(steps: tuple[StepResult, ...]) -> JsonObject:
    """Build the ``certificationEligibility`` block for the result file.

    Implements the PRD's Certification Eligibility Assessment for Phase 1: a
    self-service check that a run is suitable for submission to OBL for
    formal certification. The criteria are driven by which steps were
    declared ``mandatory`` in the manifest — *not* hardcoded — so OBL
    Standards can adjust mandatory coverage by editing configuration.

    Eligibility rules:
        * A run is eligible only when at least one mandatory step ran *and*
          every mandatory step finished as ``passed`` or ``warn``.
        * ``warn`` is non-blocking (PRD: warnings *"do not block
          certification"*).
        * ``failed`` and ``skipped`` on a mandatory step block eligibility.
          ``skipped`` always implies an earlier failure, so it is treated as
          blocking by definition.
        * A run with no mandatory steps cannot certify — the PRD
          requires *"all mandatory tests were included in the run"*, so
          zero mandatory steps is treated as "not a certification
          candidate". This applies equally to manifest runs that declare
          no mandatory steps and to non-manifest smoke checks (which have
          no mandatory concept at all).

    The block intentionally does *not* yet check "FCS version is an approved
    release". That criterion is the CertificationValidator's job (OBL-side,
    not participant-side) and the approved-release list is not yet wired
    through to the engine.

    Args:
        steps: Ordered step results from the smoke-check run.

    Returns:
        JSON object containing the boolean ``eligible`` flag, per-status
        mandatory counts, and a ``reason`` string when not eligible (omitted
        when eligible).
    """
    mandatory_steps = [step for step in steps if step.mandatory]
    mandatory_passed = sum(1 for step in mandatory_steps if step.status == "passed")
    mandatory_failed = sum(1 for step in mandatory_steps if step.status == "failed")
    mandatory_warn = sum(1 for step in mandatory_steps if step.status == "warn")
    mandatory_skipped = sum(1 for step in mandatory_steps if step.status == "skipped")

    counts: JsonObject = {
        "mandatoryTotal": len(mandatory_steps),
        "mandatoryPassed": mandatory_passed,
        "mandatoryFailed": mandatory_failed,
        "mandatoryWarn": mandatory_warn,
        "mandatorySkipped": mandatory_skipped,
    }

    reason: str | None
    if not mandatory_steps:
        reason = "No mandatory steps declared"
    elif mandatory_failed:
        reason = f"{mandatory_failed} mandatory step(s) failed"
    elif mandatory_skipped:
        reason = f"{mandatory_skipped} mandatory step(s) skipped due to earlier failures"
    else:
        reason = None

    block: JsonObject = {"eligible": reason is None, **counts}
    if reason is not None:
        block["reason"] = reason
    return block
