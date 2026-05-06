import logging

import pytest

from main import main


@pytest.mark.unit
def test_main_logs_hello(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        main()
    assert "Hello from conformance-suite-v2!" in caplog.text
