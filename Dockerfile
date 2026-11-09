# Temporary Expat and zlib fixes for the pinned DHI indexes. apk verifies the
# signed Alpine v3.24/main index and packages; only these exact package
# upgrades are accepted, and their runtime files and APK records are copied.
FROM dhi.io/python:3.14-alpine3.24@sha256:3d7fbfaca07a97d63e0d9efa00a4abc412f0044710efcae292ae7fa211fe1359 AS runtime-base

FROM dhi.io/python:3.14-alpine3.24-dev@sha256:a856bc8f2464347152d6bdcbf43fe995c79551e9fc2482ee2f00c5ec2642e406 AS package-patch

COPY --from=runtime-base /lib/apk/db/installed /patch/lib/apk/db/installed
COPY docker/patch_base_packages.py /patch_base_packages.py
RUN cp /lib/apk/db/installed /before-installed && \
    apk add --no-cache \
        --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/main \
        'expat=2.10.0-r0' 'libexpat=2.10.0-r0' \
        'zlib=1.4-r0' 'zlib-dev=1.4-r0' && \
    python3 /patch_base_packages.py && \
    mkdir -p /patch/usr/lib /patch/usr/bin && \
    cp -a /usr/lib/libexpat.so.1* /patch/usr/lib/ && \
    cp -a /usr/lib/libz.so.1* /patch/usr/lib/ && \
    cp -a /usr/bin/xmlwf /patch/usr/bin/

# ─── Build stage ──────────────────────────────────────────────────────────────
# Docker Hardened Image (DHI), Alpine 3.24, "-dev" variant: has a shell, apk,
# and pip so it can build the venv, but is otherwise the same underlying
# Python 3.14/musl environment as the distroless runtime stage below.
# Uvicorn's C-extension dependencies (httptools, uvloop, and watchfiles)
# provide prebuilt musllinux wheels, so no compiler toolchain is required.
#
# Pulling from `dhi.io` requires `docker login dhi.io` using a Docker account
# (see docs/DEVELOPER_GUIDE.md); CI authenticates with a read-only
# organisation-owned credential. Both stages pin multi-architecture index
# digests for reproducibility on amd64 and arm64; base updates require a
# reviewed digest bump and scanner policy reassessment.
FROM dhi.io/python:3.14-alpine3.24-dev@sha256:a856bc8f2464347152d6bdcbf43fe995c79551e9fc2482ee2f00c5ec2642e406 AS builder

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
# Distroless DHI runtime variant: no shell, defaults to non-root UID/GID 65532.
# Pinned to an exact digest for the same reasons as the builder stage above.
FROM dhi.io/python:3.14-alpine3.24@sha256:3d7fbfaca07a97d63e0d9efa00a4abc412f0044710efcae292ae7fa211fe1359 AS runtime

# The runtime base already has files from the vulnerable packages. Remove the
# old shared libraries before the overlay COPY, which otherwise only merges files.
USER 0:0
RUN ["python3", "-c", "from pathlib import Path; [p.unlink() for pattern in ('libexpat.so.1*', 'libz.so.1*') for p in Path('/usr/lib').glob(pattern)]; Path('/usr/bin/xmlwf').unlink(missing_ok=True)"]
COPY --from=package-patch /patch/ /
USER 65532:65532

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
