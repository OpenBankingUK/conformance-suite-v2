import pytest

from conformance.assertions import evaluate_assertion
from conformance.json_types import JsonValue
from conformance.manifest import HttpStatusAssertion, JsonFieldAssertion


@pytest.mark.unit
def test_evaluate_http_status_passes_when_status_matches() -> None:
    result = evaluate_assertion(
        HttpStatusAssertion(type="http_status", expected=200),
        status_code=200,
        body={},
    )

    assert result.passed is True

    assert result.message == "HTTP status was 200"


@pytest.mark.unit
def test_evaluate_http_status_fails_when_status_differs() -> None:
    result = evaluate_assertion(
        HttpStatusAssertion(type="http_status", expected=200),
        status_code=201,
        body={},
    )

    assert result.passed is False
    assert result.message == "Expected HTTP status 200, got 201"


@pytest.mark.unit
def test_evaluate_required_json_field_passes_when_field_is_present() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="issuer", rule="required"),
        status_code=200,
        body={"issuer": None},
    )

    assert result.passed is True
    assert result.message == "JSON field issuer is present"


@pytest.mark.unit
def test_evaluate_required_json_field_fails_when_field_is_missing() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="issuer", rule="required"),
        status_code=200,
        body={},
    )

    assert result.passed is False
    assert result.message == "JSON field issuer is missing"


@pytest.mark.unit
def test_evaluate_https_url_json_field_passes_for_https_url() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="jwks_uri", rule="https_url"),
        status_code=200,
        body={"jwks_uri": "https://modelbank.example.com/jwks"},
    )

    assert result.passed is True
    assert result.message == "JSON field jwks_uri is an HTTPS URL"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("http://modelbank.example.com/jwks", "JSON field jwks_uri must be an HTTPS URL"),
        ("https://client@modelbank.example.com/jwks", "JSON field jwks_uri must not include credentials"),
        ("https://127.0.0.1/jwks", "JSON field jwks_uri must use a DNS hostname, not an IP literal"),
        (42, "JSON field jwks_uri must be a non-empty HTTPS URL string"),
    ],
)
def test_evaluate_https_url_json_field_fails_for_unsafe_values(value: JsonValue, message: str) -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="jwks_uri", rule="https_url"),
        status_code=200,
        body={"jwks_uri": value},
    )

    assert result.passed is False
    assert result.message == message


@pytest.mark.unit
def test_evaluate_array_json_field_passes_for_array() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="keys", rule="array"),
        status_code=200,
        body={"keys": []},
    )

    assert result.passed is True
    assert result.message == "JSON field keys is an array"


@pytest.mark.unit
def test_evaluate_array_json_field_fails_for_non_array() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="keys", rule="array"),
        status_code=200,
        body={"keys": {}},
    )

    assert result.passed is False
    assert result.message == "JSON field keys must be an array"


@pytest.mark.unit
def test_evaluate_json_field_resolves_dot_paths() -> None:
    result = evaluate_assertion(
        JsonFieldAssertion(type="json_field", path="metadata.issuer", rule="required"),
        status_code=200,
        body={"metadata": {"issuer": "https://modelbank.example.com"}},
    )

    assert result.passed is True
