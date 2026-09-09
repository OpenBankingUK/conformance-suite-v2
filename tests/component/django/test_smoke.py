import pytest
from django.core.management import call_command

pytestmark = pytest.mark.component


def test_django_system_checks_pass() -> None:
    """Validate Django configuration: middleware, installed apps, URL routing, etc."""
    call_command("check", "--fail-level", "WARNING")
