# ─── Build stage ──────────────────────────────────────────────────────────────
# Docker Hardened Image (DHI), Debian 13, "-dev" variant: has a shell, apt,
# and pip so it can build the venv, but is otherwise the same underlying
# Python 3.14 install as the distroless runtime stage below (glibc, so
# uvicorn's C-extension deps — httptools, uvloop, watchfiles — install from
# prebuilt manylinux wheels; no compiler toolchain is required).
#
# Pulling from `dhi.io` requires `docker login dhi.io` using a Docker account
# (see docs/DEVELOPER_GUIDE.md); CI authenticates with a read-only
# organisation-owned credential. Pinned to an exact digest for
# reproducibility; base updates require a reviewed digest bump and scanner
# policy reassessment.
FROM dhi.io/python:3.14-debian13-dev@sha256:42cd56dede69350b250398097287cbf1020d0ead0ad8cb4e179bd3bac1a98634 AS builder

# Install uv for fast, reproducible dependency resolution
COPY --from=docker.io/astral/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (maximises layer cache)
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev group, no project install — this
# is a container-deployed Django app, not a distributable Python package)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source (docs/, tests/, scripts/, and other CI/participant-
# only files are excluded via .dockerignore)
COPY . .

# Pre-create the persistent data root with its subpaths, owned by the
# runtime's non-root UID/GID (65532). The final stage's runtime user cannot
# run `mkdir`/`chown` itself — its image has no shell — so these are created
# here and copied across with the correct ownership already applied. A named
# volume mounted at /data in the runtime container inherits this ownership
# and starts empty on first run (Docker volume initialisation semantics).
RUN mkdir -p /data/results /data/logs /data/sessions && \
    chown -R 65532:65532 /app /data

# ─── Runtime stage ────────────────────────────────────────────────────────────
# Distroless DHI runtime variant: no shell, no package manager, defaults to
# non-root UID/GID 65532. Pinned to an exact digest for the same reasons as
# the builder stage above.
FROM dhi.io/python:3.14-debian13@sha256:e1a5bd571d9585d7eb80c8278b54b69a0e0bf5a9bb2b1424b9e4576374df6659 AS runtime

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
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8443", "--ssl-keyfile", "/tmp/conformance-suite-tls/localhost-private-key.pem", "--ssl-certfile", "/tmp/conformance-suite-tls/localhost-certificate.pem"]
