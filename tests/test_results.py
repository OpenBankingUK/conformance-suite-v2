from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from conformance.results import StepResult, build_smoke_check_result


@pytest.mark.unit
def test_step_result_details_are_detached_from_input_mapping() -> None:
    details = {"keyCount": 1}

    result = StepResult(name="jwks", status="passed", message="Fetched JWKS document", details=details)
    details["keyCount"] = 999

    assert result.to_json_object()["details"] == {"keyCount": 1}


@pytest.mark.unit
def test_step_result_serialized_details_are_detached_from_result() -> None:
    result = StepResult(name="jwks", status="passed", message="Fetched JWKS document", details={"keyCount": 1})

    serialized_result = result.to_json_object()
    serialized_details = serialized_result["details"]
    assert isinstance(serialized_details, dict)
    serialized_details["keyCount"] = 999

    assert result.to_json_object()["details"] == {"keyCount": 1}
    assert isinstance(result.details, MappingProxyType)


@pytest.mark.unit
def test_smoke_check_result_summary_counts_skipped_steps() -> None:
    started_at = datetime.now(UTC)
    steps = [
        StepResult(name="primary", status="failed", message="boom"),
        StepResult(name="primary.followUp", status="skipped", message="Skipped because primary step 'primary' failed"),
        StepResult(name="other", status="passed", message="ok"),
    ]

    result = build_smoke_check_result("ozone-model-bank", steps, started_at=started_at)

    assert result.status == "failed"
    assert result.to_json_object()["summary"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "skipped": 1,
    }


@pytest.mark.unit
def test_smoke_check_result_skipped_alone_does_not_fail_aggregate() -> None:
    started_at = datetime.now(UTC)
    steps = [
        StepResult(name="discovery", status="passed", message="ok"),
        # Synthetic scenario: a skipped step without an upstream failure does
        # not by itself force the aggregate to ``failed``. The aggregate is
        # ``failed`` iff at least one step failed.
        StepResult(name="optional", status="skipped", message="skipped by configuration"),
    ]

    result = build_smoke_check_result("ozone-model-bank", steps, started_at=started_at)

    assert result.status == "passed"
    assert result.to_json_object()["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 0,
        "skipped": 1,
    }
