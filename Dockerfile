# ─── CPython build ────────────────────────────────────────────────────────────
# Temporary derivative until DHI supplies the fixed CPython maintenance release.
# Retain DHI's Debian library backports; do not inherit its Python VEX.
# Pulling from `dhi.io` requires `docker login dhi.io` using a Docker account
# (see docs/DEVELOPER_GUIDE.md); CI authenticates with a read-only
# organisation-owned credential. Pinned to an exact digest for
# reproducibility; base updates require a reviewed digest bump and scanner
# policy reassessment.
FROM dhi.io/python:3.14-debian13-dev@sha256:0fc1a06a500fd1a1a884d7bb53c8da242527878e39eefcf106ed8050e94693ad AS python-build

USER 0
# Build tools stay in this stage. Runtime libraries and headers come from the
# matching pinned DHI dev image, not from an unrelated distribution upgrade.
RUN apt-get update && apt-get install -y --no-install-recommends --no-upgrade \
    gcc=4:14.2.0-1+dhi0 gcc-14=14.2.0-19+dhi5 binutils=2.44-3+dhi9 \
    make=4.4.1-2+dhi1 xz-utils=5.8.1-1+deb13u2+dhi0 \
    libsqlite3-dev=3.46.1-7+deb13u2+dhi1 libssl-dev=3.5.7-1~deb13u3+dhi0 \
    libffi-dev=3.4.8-2+dhi2 libbz2-dev=1.0.8-6+dhi2 \
    liblzma-dev=5.8.1-1+deb13u2+dhi0 libreadline-dev=8.2-6+dhi2 \
    libgdbm-dev=1.24-2+dhi2 libgdbm-compat-dev=1.24-2+dhi2 uuid-dev=2.41.5-0+deb13u1+dhi3 \
    libzstd-dev=1.5.7+dfsg-1+dhi2 \
    zlib1g-dev libexpat1-dev
ENV SOURCE_DATE_EPOCH=1792540800
WORKDIR /src
ADD --checksum=sha256:e02d9ac21cebda6e46435369744a92c65cc7cd8669697694122cf97ffacd1e53 \
    https://www.python.org/ftp/python/3.14.9/Python-3.14.9.tar.xz /src/python.tar.xz
RUN tar -xJf python.tar.xz --strip-components=1 && \
    ./configure --prefix=/opt/python --without-ensurepip --without-static-libpython --with-system-expat \
      --enable-option-checking=fatal --enable-loadable-sqlite-extensions \
      CFLAGS="-O2 -g0 -fstack-protector-strong -fstack-clash-protection -fno-strict-overflow -ftrivial-auto-var-init=zero" \
      CPPFLAGS="-D_FORTIFY_SOURCE=3" \
      LDFLAGS="-Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack" && \
    make -j 4 && \
    ./python -c "import ssl, sqlite3, ctypes, bz2, lzma, zlib, pyexpat, compression.zstd, dbm.gnu, dbm.ndbm" && \
    ./python -m test -j 4 test_tempfile test_tarfile test_ssl test_sqlite3 test_venv && \
    make install DESTDIR=/replacement && \
    rm -rf /replacement/opt/python/lib/python3.14/ensurepip \
      /replacement/opt/python/lib/python3.14/test \
      /replacement/opt/python/lib/libpython3.14.a \
      /replacement/opt/python/include \
      /replacement/opt/python/share
COPY docker/replace_python.py /replacement/replace_python.py

# ─── Application build ────────────────────────────────────────────────────────
FROM python-build AS builder
COPY --from=python-build /replacement/opt/python /opt/python
ENV PATH="/opt/python/bin:$PATH" \
    UV_PYTHON="/opt/python/bin/python3.14" \
    UV_PYTHON_DOWNLOADS=never

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
FROM dhi.io/python:3.14-debian13@sha256:36c38f4be040b095f67fbb0982d18875518f1b58d23b9ad3cc8bc903a7f7524e AS runtime

# The original interpreter performs this build-only replacement, then is
# removed along with its stdlib, shared library, pip and bundled wheels.
USER 0
RUN --mount=from=python-build,source=/replacement,target=/replacement \
    ["/usr/bin/python3", "-B", "/replacement/replace_python.py", "/", "/replacement"]
ENV PYTHON_VERSION="3.14.9"

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
ENV PATH="/app/.venv/bin:/opt/python/bin:$PATH"
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
