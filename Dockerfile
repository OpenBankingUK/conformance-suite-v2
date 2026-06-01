# ─── Build stage ──────────────────────────────────────────────────────────────
# Alpine base (musl libc) chosen over Debian slim to eliminate the bulk of
# upstream OS-package CVEs that Snyk flags on the Debian image. Pinned to
# Alpine 3.22 + the stable Python 3.14 line (matches `requires-python` in
# pyproject.toml — no release-candidate interpreters in regulated builds).
FROM python:3.14-alpine3.22 AS builder

# Build toolchain for C/Rust extensions pulled in by uvicorn[standard]
# (httptools, uvloop, watchfiles). musl wheels exist for most of these on
# recent releases, but installing build deps here is the belt-and-braces
# guarantee — everything stays in the discarded builder layer.
RUN apk add --no-cache \
        build-base \
        libffi-dev \
        cargo \
        rust

# Install uv for fast, reproducible dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (maximises layer cache)
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev group, no project install — this
# is a container-deployed Django app, not a distributable Python package)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY . .

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.14-alpine3.22 AS runtime

WORKDIR /app

# Create non-root user. Alpine ships BusyBox addgroup/adduser rather than
# groupadd/useradd; flags differ from the shadow-utils equivalents used on
# Debian. `-D` disables the password, `-S` would create a system user (we
# want a regular UID 1000 so file ownership is predictable on bind mounts).
RUN addgroup -g 1000 appuser && \
    adduser -u 1000 -G appuser -s /bin/sh -D appuser

# Copy virtual environment and application from builder (with correct ownership)
COPY --from=builder --chown=appuser:appuser /app /app

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health/', headers={'Host': 'healthcheck.local'}).raise_for_status()" || exit 1

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
