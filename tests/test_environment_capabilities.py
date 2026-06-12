"""Unit tests for environment capability metadata and compatibility helpers."""

from __future__ import annotations

import pytest

from conformance.environment_capabilities import (
    EnvironmentDeclaration,
    UnsupportedCombination,
    evaluate_suite_environment_support,
    list_environment_presets,
    list_suite_environment_capabilities,
    make_custom_environment_reference,
    make_preset_environment_reference,
)
from conformance.model_bank_config import SuiteSelection
from conformance.suite_catalog import list_supported_suites


@pytest.mark.unit
def test_suite_capabilities_cover_all_bundled_catalog_rows() -> None:
    """Every bundled suite catalog row should have capability metadata."""
    capabilities = list_suite_environment_capabilities()
    catalog_keys = {metadata.to_suite_selection() for metadata in list_supported_suites()}

    assert len(capabilities) == 23
    assert {(item.standard, item.spec_version, item.api, item.suite) for item in capabilities} == {
        (selection.standard, selection.spec_version, selection.api, selection.suite) for selection in catalog_keys
    }


@pytest.mark.unit
def test_known_preset_supports_current_ais_certification_with_private_key_jwt() -> None:
    """Known Ozone preset should support current AIS certification defaults."""
    selection = SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        api="ais",
        suite="ais-certification-slice",
    )
    environment = make_preset_environment_reference("ozone-obie-preprod")

    assert environment is not None
    evaluation = evaluate_suite_environment_support(
        selection=selection,
        environment=environment,
        psu_mode="manual",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )

    assert evaluation.support == "supported"
    assert evaluation.blockers == ()
    assert evaluation.warnings == ()


@pytest.mark.unit
def test_headless_psu_mode_is_blocked_for_current_bundled_psu_suites() -> None:
    """Headless PSU mode must be blocked for current bundled starter suites."""
    selection = SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0.1",
        profile="fapi1-advanced",
        api="pis",
        suite="psu-auth-starter",
    )
    environment = make_preset_environment_reference("ozone-obie-preprod")

    assert environment is not None
    evaluation = evaluate_suite_environment_support(
        selection=selection,
        environment=environment,
        psu_mode="headless",
    )

    assert evaluation.support == "blocked"
    assert any("Headless PSU" in reason for reason in evaluation.blockers)


@pytest.mark.unit
def test_custom_environment_without_capability_declaration_returns_unknown() -> None:
    """Custom environments without declarations should produce unknown warnings."""
    selection = SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        api="ais",
        suite="ais-certification-baseline",
    )
    environment = make_custom_environment_reference(label="participant-custom-env")

    evaluation = evaluate_suite_environment_support(
        selection=selection,
        environment=environment,
        psu_mode="manual",
        token_endpoint_auth_method="private_key_jwt",  # noqa: S106 - auth-method enum fixture, not a secret
    )

    assert evaluation.support == "unknown"
    assert evaluation.blockers == ()
    assert any("undeclared" in warning for warning in evaluation.warnings)


@pytest.mark.unit
def test_declared_unsupported_combination_blocks_custom_environment() -> None:
    """Explicitly declared unsupported combinations should hard-block launch."""
    selection = SuiteSelection(
        standard="ob-read-write",
        spec_version="v4.0",
        profile="fapi1-advanced",
        api="ais",
        suite="ais-certification-slice",
    )
    environment = make_custom_environment_reference(
        label="restricted-env",
        declaration=EnvironmentDeclaration(
            supported_standards=frozenset({"ob-read-write"}),
            supported_spec_versions=frozenset({"v4.0"}),
            supported_api_families=frozenset({"ais"}),
            supported_suites=frozenset({"ais-certification-slice"}),
            supported_psu_modes=frozenset({"manual"}),
            supported_token_endpoint_auth_methods=frozenset({"private_key_jwt", "tls_client_auth"}),
            mtls_supported=True,
            fapi_signing_supported=True,
            redirect_uri_supported=True,
            resource_base_url_supported=True,
            known_unsupported_combinations=(
                UnsupportedCombination(
                    reason="Client registration excludes tls_client_auth for this suite.",
                    suite="ais-certification-slice",
                    token_endpoint_auth_method="tls_client_auth",  # noqa: S106 - auth-method enum fixture, not a secret
                ),
            ),
        ),
    )

    evaluation = evaluate_suite_environment_support(
        selection=selection,
        environment=environment,
        psu_mode="manual",
        token_endpoint_auth_method="tls_client_auth",  # noqa: S106 - auth-method enum fixture, not a secret
    )

    assert evaluation.support == "blocked"
    assert "excludes tls_client_auth" in " ".join(evaluation.blockers)


@pytest.mark.unit
def test_environment_presets_contains_ozone_obie_preprod() -> None:
    """Preset list should expose the known Ozone model-bank environment."""
    presets = list_environment_presets()

    assert any(item.key == "ozone-obie-preprod" for item in presets)
