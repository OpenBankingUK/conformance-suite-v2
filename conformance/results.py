"""Structured result models for conformance smoke-check output."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from conformance.json_types import JsonObject, JsonValue

CheckStatus = Literal["passed", "failed", "skipped"]
"""Outcome values currently emitted by smoke-check steps and summaries.

PRD ultimately requires PASS, FAIL, WARN, SKIPPED. ``warn`` is reserved
for a future feature (per-test deprecation signals) and is not yet
emitted by the engine; ``skipped`` is emitted when a v1 step cannot run
because a prerequisite step produced no response.
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
    """

    name: str
    status: CheckStatus
    message: str
    url: str | None = None
    status_code: int | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)

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
                "skipped": sum(1 for step in self.steps if step.status == "skipped"),
            },
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
    status: CheckStatus = "passed" if all(step.status == "passed" for step in steps) else "failed"
    return SmokeCheckResult(
        environment=environment,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        steps=tuple(steps),
    )
