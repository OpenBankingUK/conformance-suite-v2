from types import MappingProxyType

import pytest

from conformance.results import StepResult


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
def test_step_result_mandatory_defaults_to_false() -> None:
    """``StepResult.mandatory`` defaults to ``False`` when not supplied."""
    result = StepResult(name="x", status="passed", message="ok")
    assert result.mandatory is False


@pytest.mark.unit
def test_step_result_mandatory_not_in_json_output() -> None:
    """``mandatory`` is intentionally not serialised on individual step entries.

    The per-step JSON shape is kept stable; mandatory status is surfaced only
    via the aggregate ``certificationEligibility`` block.
    """
    result = StepResult(name="x", status="passed", message="ok", mandatory=True)
    assert "mandatory" not in result.to_json_object()


@pytest.mark.unit
def test_eligibility_block_eligible_when_all_mandatory_pass() -> None:
    """Eligible when at least one mandatory step ran and all passed."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="passed", message="ok", mandatory=True),
        StepResult(name="opt", status="failed", message="boom", mandatory=False),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert block["mandatoryTotal"] == 1
    assert block["mandatoryPassed"] == 1
    assert block["mandatoryFailed"] == 0
    assert "reason" not in block


@pytest.mark.unit
def test_eligibility_block_warn_on_mandatory_is_non_blocking() -> None:
    """A WARN on a mandatory step does not block eligibility (PRD)."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="passed", message="ok", mandatory=True),
        StepResult(name="m2", status="warn", message="deprecated", mandatory=True),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is True
    assert block["mandatoryWarn"] == 1


@pytest.mark.unit
def test_eligibility_block_failed_mandatory_blocks_with_reason() -> None:
    """A failed mandatory step blocks eligibility and emits a reason."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="failed", message="boom", mandatory=True),
        StepResult(name="m2", status="passed", message="ok", mandatory=True),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryFailed"] == 1
    assert "1 mandatory step(s) failed" in str(block["reason"])


@pytest.mark.unit
def test_eligibility_block_skipped_mandatory_blocks_with_reason() -> None:
    """A skipped mandatory step blocks eligibility and emits a reason."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [
        StepResult(name="m1", status="skipped", message="prereq failed", mandatory=True),
    ]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert "skipped" in str(block["reason"])


@pytest.mark.unit
def test_eligibility_block_no_mandatory_means_not_eligible() -> None:
    """A manifest with zero mandatory steps cannot be certified (PRD)."""
    from datetime import UTC, datetime

    from conformance.results import build_smoke_check_result

    started = datetime.now(UTC)
    steps = [StepResult(name="opt", status="passed", message="ok", mandatory=False)]
    block = build_smoke_check_result("env", steps, started_at=started).to_json_object()["certificationEligibility"]
    assert isinstance(block, dict)
    assert block["eligible"] is False
    assert block["mandatoryTotal"] == 0
    assert "No mandatory steps" in str(block["reason"])
