import pytest
from django.test import Client


@pytest.mark.integration
def test_health_endpoint_returns_200() -> None:
    client = Client()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
