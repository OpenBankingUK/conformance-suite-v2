"""Unit tests for the container's Uvicorn access logging configuration."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import pytest
from uvicorn import Config
from uvicorn.config import LOGGING_CONFIG

from docker.access_logging import SuccessfulHealthCheckFilter

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
LOG_CONFIG = ROOT / "docker" / "uvicorn_logging.json"


@pytest.mark.parametrize(
    ("client", "method", "path", "status", "visible"),
    [
        ("127.0.0.1:51452", "GET", "/health/", 200, False),
        ("[::1]:51452", "GET", "/health/", 200, False),
        ("127.0.0.1:51452", "GET", "/health/", 503, True),
        ("127.0.0.1:51452", "GET", "/health/", 404, True),
        ("127.0.0.1:51452", "HEAD", "/health/", 200, True),
        ("127.0.0.1:51452", "GET", "/health/?verbose=1", 200, True),
        ("172.17.0.1:51452", "GET", "/health/", 200, True),
        ("127.0.0.1:51452", "GET", "/favicon.ico", 404, True),
    ],
)
def test_successful_health_filter(
    client: str,
    method: str,
    path: str,
    status: int,
    visible: bool,
) -> None:
    """Hide only successful loopback health access records."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (client, method, path, "1.1", status),
        None,
    )

    assert SuccessfulHealthCheckFilter().filter(record) is visible


def test_unstructured_records_are_preserved() -> None:
    """Non-access messages must never disappear because they have no request fields."""
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "message", (), None)

    assert SuccessfulHealthCheckFilter().filter(record)


def test_default_docker_server_uses_filter_without_disabling_other_logs(capsys: pytest.CaptureFixture[str]) -> None:
    """Docker's Uvicorn config filters only healthy probes, retaining other access and error records."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"--log-config", "/app/docker/uvicorn_logging.json"' in dockerfile
    try:
        Config("config.asgi:application", log_config=str(LOG_CONFIG))
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1234", "GET", "/health/", "1.1", 200)
        access_logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1234", "GET", "/health/", "1.1", 503)
        access_logger.info('%s - "%s %s HTTP/%s" %d', "172.17.0.1:4567", "GET", "/favicon.ico", "1.1", 404)
        logging.getLogger("uvicorn.error").error("server failure")

        captured = capsys.readouterr()
        assert '"GET /health/ HTTP/1.1" 200' not in captured.out
        assert '"GET /health/ HTTP/1.1" 503' in captured.out
        assert '"GET /favicon.ico HTTP/1.1" 404' in captured.out
        assert "server failure" in captured.err
    finally:
        logging.config.dictConfig(LOGGING_CONFIG)
