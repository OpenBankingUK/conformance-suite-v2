from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from conformance.json_types import JsonObject

CheckStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class StepResult:
    name: str
    status: CheckStatus
    message: str
    url: str | None = None
    status_code: int | None = None
    details: JsonObject = field(default_factory=dict)

    def to_json_object(self) -> JsonObject:
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
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class SmokeCheckResult:
    environment: str
    status: CheckStatus
    started_at: datetime
    finished_at: datetime
    steps: tuple[StepResult, ...]

    def to_json_object(self) -> JsonObject:
        return {
            "environment": self.environment,
            "status": self.status,
            "startedAt": self.started_at.isoformat(),
            "finishedAt": self.finished_at.isoformat(),
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for step in self.steps if step.status == "passed"),
                "failed": sum(1 for step in self.steps if step.status == "failed"),
            },
            "steps": [step.to_json_object() for step in self.steps],
        }


def build_smoke_check_result(environment: str, steps: list[StepResult], *, started_at: datetime) -> SmokeCheckResult:
    finished_at = datetime.now(UTC)
    status: CheckStatus = "passed" if all(step.status == "passed" for step in steps) else "failed"
    return SmokeCheckResult(
        environment=environment,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        steps=tuple(steps),
    )
