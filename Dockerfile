# ─── Build stage ──────────────────────────────────────────────────────────────
# The Alpine DHI builder and distroless runtime share Python 3.14 and musl.
# Install native dependencies from locked musllinux wheels, without a compiler.
#
# Pulling from `dhi.io` requires `docker login dhi.io` using a Docker account
# (see docs/DEVELOPER_GUIDE.md); CI authenticates with a read-only
# organisation-owned credential. Both stages pin multi-architecture index
# digests for reproducibility on amd64 and arm64; base updates require a
# reviewed digest bump and scanner policy reassessment.
FROM dhi.io/python:3.14-alpine-dev@sha256:ee05e528039dac5da30a16e914dfe66a660e6966c115b413ae24f8f17d40009f AS builder

# Install uv for fast, reproducible dependency resolution
COPY --from=docker.io/astral/uv:0.13.8@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (maximises layer cache)
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev group, no project install — this
# is a container-deployed Django app, not a distributable Python package)
RUN uv sync --frozen --no-dev --no-install-project --no-build

# Copy application source (docs/, tests/, scripts/, and other CI/participant-
# only files are excluded via .dockerignore)
COPY . .

# Collect static assets (vendored HTMX) into STATIC_ROOT so WhiteNoise can
# serve pre-compressed copies at runtime. Build-time only; no secrets needed.
RUN .venv/bin/python manage.py collectstatic --noinput

# Pre-create the persistent data root with its subpaths, owned by the
# runtime's non-root UID/GID (65532). The final stage's runtime user cannot
# run `mkdir`/`chown` itself — its image has no shell — so these are created
# here and copied across with the correct ownership already applied. A named
# volume mounted at /data in the runtime container inherits this ownership
# and starts empty on first run (Docker volume initialisation semantics).
RUN mkdir -p /data/results /data/logs /data/sessions && \
    chown -R 65532:65532 /app /data

# ─── Runtime stage ────────────────────────────────────────────────────────────
# Distroless DHI runtime variant: no shell, defaults to non-root UID/GID 65532.
# Pinned to an exact digest for the same reasons as the builder stage above.
FROM dhi.io/python:3.14-alpine@sha256:74d0713d667db05590e77587097a004b0f97a27bab0549d05396384a113b0e03 AS runtime

WORKDIR /app

# Copy the application and venv, and the pre-created /data tree. --chown is
# required even though the builder already chowned these paths: COPY creates
# a *new* destination directory owned by root by default whenever the
# destination does not yet exist in this stage, regardless of the source
# ownership.
COPY --from=builder --chown=65532:65532 /app /app
COPY --from=builder --chown=65532:65532 /data /data

ARG CONFORMANCE_TOOL_VERSION=""
ARG SOURCE_REVISION=""
ENV PATH="/app/.venv/bin:$PATH"
ENV CONFORMANCE_TOOL_VERSION=${CONFORMANCE_TOOL_VERSION}

# OCI labels used by the promotion workflow to cross-check a built candidate
# image against its release metadata before publishing (see scripts/release_metadata.py).
LABEL org.opencontainers.image.title="Open Banking UK Conformance Suite" \
      org.opencontainers.image.description="Open Banking UK Conformance Test Tool for verifying API standards compliance" \
      org.opencontainers.image.source="https://github.com/OpenBankingUK/conformance-suite-v2" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version=${CONFORMANCE_TOOL_VERSION} \
      org.opencontainers.image.revision=${SOURCE_REVISION}

# The base image already defaults to this UID/GID; set explicitly so the
# requirement holds regardless of upstream base image changes.
USER 65532:65532

EXPOSE 8443

VOLUME ["/data"]

# Exec-form healthcheck: the runtime image has no shell, so a shell-form
# `CMD` string (as used by most Dockerfiles) cannot run here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "/app/docker/healthcheck.py"]

# The entrypoint prepares /data, the Django secret key, local TLS material,
# and safe defaults, then execs into CMD (or an operator-supplied override,
# e.g. the existing headless CLI) so signals and exit codes reach it directly.
ENTRYPOINT ["python3", "/app/docker/entrypoint.py"]
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8443", "--ssl-keyfile", "/tmp/conformance-suite-tls/localhost-private-key.pem", "--ssl-certfile", "/tmp/conformance-suite-tls/localhost-certificate.pem", "--log-config", "/app/docker/uvicorn_logging.json"]
