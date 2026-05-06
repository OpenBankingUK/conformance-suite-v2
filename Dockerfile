# ─── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.14.4-slim-bookworm AS builder

# Install uv for fast, reproducible dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (maximises layer cache)
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev group)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY . .

# Install the project itself
RUN uv sync --frozen --no-dev

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.14.4-slim-bookworm AS runtime

WORKDIR /app

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash appuser

# Copy virtual environment and application from builder
COPY --from=builder /app /app

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Set ownership so the non-root user can write to the DB and any runtime files
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health/').raise_for_status()" || exit 1

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
