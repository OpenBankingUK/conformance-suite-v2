"""Uvicorn access-log filtering for successful local container health probes."""

from __future__ import annotations

import logging


class SuccessfulHealthCheckFilter(logging.Filter):
    """Keep access logs except successful loopback health requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Preserve all records other than successful local GET /health/ responses."""
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True

        client_addr, method, path, _, status_code = args
        return not (
            isinstance(client_addr, str)
            and (client_addr.startswith("127.0.0.1:") or client_addr.startswith("[::1]:"))
            and method == "GET"
            and path == "/health/"
            and status_code == 200
        )
