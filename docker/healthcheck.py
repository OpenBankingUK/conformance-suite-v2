"""Exec-form container healthcheck (no shell available in the runtime image).

Requests the application's health endpoint over loopback using the reserved
``healthcheck.local`` ``Host`` header (see ``config.settings.HEALTHCHECK_HOST``)
so the probe never depends on the operator's ``DJANGO_ALLOWED_HOSTS`` value.
"""

from __future__ import annotations

import ssl
import sys

import httpx

HEALTHCHECK_URL = "https://localhost:8443/health/"
HEALTHCHECK_HOST_HEADER = "healthcheck.local"
TLS_CERTIFICATE_PATH = "/tmp/conformance-suite-tls/localhost-certificate.pem"  # noqa: S108 - private container tmpfs path.
TIMEOUT_SECONDS = 5.0


def main() -> int:
    """Probe the health endpoint and report success or failure.

    Returns:
        ``0`` if the health endpoint responds with a successful status code,
        ``1`` otherwise (connection failure, timeout, or error status).
    """
    try:
        tls_context = ssl.create_default_context(cafile=TLS_CERTIFICATE_PATH)
        response = httpx.get(
            HEALTHCHECK_URL,
            headers={"Host": HEALTHCHECK_HOST_HEADER},
            timeout=TIMEOUT_SECONDS,
            verify=tls_context,
        )
        response.raise_for_status()
    except (OSError, httpx.HTTPError) as error:
        sys.stderr.write(f"healthcheck failed: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
