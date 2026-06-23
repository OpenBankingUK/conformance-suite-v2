"""Unit tests for participant ``testData`` configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from conformance.json_types import JsonValue
from conformance.model_bank_config import ConfigError, parse_model_bank_config


def _base_config_with_test_data(test_data: JsonValue | None = None) -> dict[str, JsonValue]:
    """Build a minimal participant config with optional ``testData``.

    Args:
        test_data: Optional raw ``testData`` value to include.

    Returns:
        Raw participant config dictionary suitable for parser tests.
    """
    config: dict[str, JsonValue] = {
        "environment": "sandbox",
        "discoveryUrl": "https://example.com/.well-known/openid-configuration",
    }
    if test_data is not None:
        config["testData"] = test_data
    return config


@pytest.mark.unit
def test_parse_model_bank_config_returns_none_when_test_data_is_absent() -> None:
    """Parser leaves ModelBankConfig.test_data unset when testData is absent."""
    config = parse_model_bank_config(_base_config_with_test_data(), base_dir=Path.cwd(), output_base_dir=Path.cwd())

    assert config.test_data is None


@pytest.mark.unit
def test_parse_model_bank_config_parses_valid_test_data_values() -> None:
    """Parser accepts a valid testData.values mapping."""
    config = parse_model_bank_config(
        _base_config_with_test_data({"values": {"creditorName": "Test Merchant"}}),
        base_dir=Path.cwd(),
        output_base_dir=Path.cwd(),
    )

    assert config.test_data is not None
    assert dict(config.test_data.values) == {"creditorName": "Test Merchant"}


@pytest.mark.unit
def test_parse_model_bank_config_rejects_invalid_test_data_key_name() -> None:
    """Parser rejects invalid key names inside testData.values."""
    with pytest.raises(ConfigError, match="invalid"):
        parse_model_bank_config(
            _base_config_with_test_data({"values": {"1bad": "value"}}),
            base_dir=Path.cwd(),
            output_base_dir=Path.cwd(),
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_string_test_data_value() -> None:
    """Parser rejects non-string values inside testData.values."""
    with pytest.raises(ConfigError, match="string value"):
        parse_model_bank_config(
            _base_config_with_test_data({"values": {"creditorName": 7}}),
            base_dir=Path.cwd(),
            output_base_dir=Path.cwd(),
        )


@pytest.mark.unit
def test_parse_model_bank_config_rejects_non_object_test_data() -> None:
    """Parser rejects a non-object testData section."""
    with pytest.raises(ConfigError, match="testData must be a JSON object"):
        parse_model_bank_config(
            _base_config_with_test_data("bad"),
            base_dir=Path.cwd(),
            output_base_dir=Path.cwd(),
        )
