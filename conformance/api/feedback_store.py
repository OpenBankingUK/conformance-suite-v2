"""Bounded session-owned local report storage; reports expire after 30 minutes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic

from conformance.feedback import FeedbackReport

REPORT_TTL_SECONDS = 30 * 60
MAX_REPORTS = 20
MAX_SESSION_REPORTS = 3
MAX_STORED_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _Entry:
    owner: str
    created: float
    report: FeedbackReport


class FeedbackStore:
    """Thread-safe ephemeral storage, never persisted in cookies or drafts."""

    def __init__(self) -> None:
        """Initialise empty local storage."""
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    def put(self, owner: str, report: FeedbackReport) -> None:
        """Store a report, evicting expired and oldest entries within limits."""
        with self._lock:
            self._expire()
            owned = [key for key, entry in self._entries.items() if entry.owner == owner]
            while len(owned) >= MAX_SESSION_REPORTS:
                del self._entries[owned.pop(0)]
            self._entries[report.report_id] = _Entry(owner, monotonic(), report)
            while len(self._entries) > MAX_REPORTS or self._size() > MAX_STORED_BYTES:
                del self._entries[next(iter(self._entries))]

    def get(self, owner: str, report_id: str) -> FeedbackReport | None:
        """Return an unexpired report only to the session that prepared it."""
        with self._lock:
            self._expire()
            entry = self._entries.get(report_id)
            return entry.report if entry is not None and entry.owner == owner else None

    def reset(self) -> None:
        """Discard all prepared reports, as on process restart."""
        with self._lock:
            self._entries.clear()

    def _expire(self) -> None:
        now = monotonic()
        for key in list(self._entries):
            if now - self._entries[key].created >= REPORT_TTL_SECONDS:
                del self._entries[key]

    def _size(self) -> int:
        return sum(len(entry.report.bundle) for entry in self._entries.values())


feedback_store = FeedbackStore()
