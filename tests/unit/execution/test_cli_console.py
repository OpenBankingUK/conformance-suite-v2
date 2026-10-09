"""Terminal-only CLI colour and plain-text summary coverage."""

import io
import sys
from pathlib import Path

import pytest

from conformance.cli_console import Tone, colour_label, supports_colour, write_notice
from conformance.cli_summary import render_run_summary

pytestmark = pytest.mark.unit


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_colour_respects_stream_and_no_color_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert supports_colour(_Terminal())
    assert not supports_colour(io.StringIO())
    monkeypatch.setenv("NO_COLOR", "")
    assert not supports_colour(_Terminal())


@pytest.mark.parametrize(
    ("tone", "code"), [("info", "36"), ("passed", "32"), ("failed", "31"), ("warning", "33"), ("skipped", "2")]
)
def test_labels_reset_immediately(tone: Tone, code: str) -> None:
    assert colour_label("label", tone, enabled=True) == f"\033[{code}mlabel\033[0m"
    assert colour_label("label", tone) == "label"


def test_notice_colours_only_stderr_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    stderr = _Terminal()
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setattr("sys.stdout", stdout)

    write_notice("[PSU]", "https://example.com/callback")

    assert stderr.getvalue() == "\033[36m[PSU]\033[0m https://example.com/callback\n"
    assert stdout.getvalue() == ""


def test_summary_colour_only_changes_status_labels() -> None:
    plain = render_run_summary(
        {"status": "passed"}, run_label="plan", result_path=Path("result.json"), execution_log_path=Path("log.ndjson")
    )
    coloured = render_run_summary(
        {"status": "passed"},
        colour=True,
        run_label="plan",
        result_path=Path("result.json"),
        execution_log_path=Path("log.ndjson"),
    )
    assert "\033" not in plain
    assert "Conformance run \033[32mPASSED\033[0m: plan" in coloured
    assert coloured.replace("\033[32m", "").replace("\033[0m", "") == plain


def test_stdout_colour_is_independent_of_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout", _Terminal())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    assert supports_colour(sys.stdout)
    assert not supports_colour(sys.stderr)
