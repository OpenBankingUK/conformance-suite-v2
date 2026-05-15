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
