"""Lightweight terminal styling shared by CLI startup and execution."""

from __future__ import annotations

import os
import sys
from typing import Literal, TextIO

type Tone = Literal["info", "passed", "failed", "warning", "skipped"]

_COLOURS: dict[Tone, str] = {
    "info": "36",
    "passed": "32",
    "failed": "31",
    "warning": "33",
    "skipped": "2",
}


def supports_colour(stream: TextIO) -> bool:
    """Enable ANSI labels only on terminals unless NO_COLOR is present."""
    return "NO_COLOR" not in os.environ and stream.isatty()


def colour_label(text: str, tone: Tone, *, enabled: bool = False) -> str:
    """Style only a label, resetting immediately and preserving plain text."""
    return f"\033[{_COLOURS[tone]}m{text}\033[0m" if enabled else text


def write_notice(label: str, text: str, *, tone: Tone = "info") -> None:
    """Write and flush a labelled CLI notice to the current stderr stream."""
    styled = colour_label(label, tone, enabled=supports_colour(sys.stderr))
    sys.stderr.write(f"{styled} {text}\n")
    sys.stderr.flush()
