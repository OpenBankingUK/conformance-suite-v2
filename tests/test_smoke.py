import pytest
from django.core.management import call_command


@pytest.mark.integration
def test_django_system_checks_pass() -> None:
    """Validate Django configuration: middleware, installed apps, URL routing, etc."""
    call_command("check", "--fail-level", "WARNING")
