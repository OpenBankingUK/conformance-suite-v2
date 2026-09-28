# Docker deployment guide

The hardened Docker image is the primary, supported way to run the Functional
Conformance Suite. It ships as a digest-pinned, non-root, read-only-rootfs
container with no shell or package manager, and needs no manually supplied
Django secret.

## Pulling the image

Images are published to a private GHCR repository:
`ghcr.io/openbankinguk/conformance-suite-v2`. Authenticate with a GitHub
personal access token that has at least `read:packages` scope and is
authorized for the `OpenBankingUK` organization:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <your-github-username> --password-stdin
docker pull ghcr.io/openbankinguk/conformance-suite-v2:<version>
```

Replace `<version>` with an exact published tag, for example `2.0.0`,
`2.0.0-beta.1`, or `2.0.0-dev.1`. See
[`docs/CICD_STRATEGY.md`](CICD_STRATEGY.md) for the full preview/beta/GA
versioning and promotion model. Never rely on `latest` for anything other than
the current GA release, and never use a mutable channel-named tag — none is
published.

## Quick start (ephemeral)

For a disposable local session with no persisted data:

```bash
docker run --rm -p 127.0.0.1:8443:8443 \
  ghcr.io/openbankinguk/conformance-suite-v2:<version>
```

Open `https://127.0.0.1:8443/`. The container generates a local self-signed
certificate, so the browser displays a certificate warning on first use.
Browser sessions, generated results, execution logs, and the ephemeral
certificate are lost when the container exits.

For an ASPSP registration that still uses the legacy FCS redirect URI, open
`https://0.0.0.0:8443/` and accept its certificate warning before starting the
PSU flow. On supported local hosts this reaches the same loopback-published
container; Docker does not expose a second port or bind the service to the LAN.

## Recommended durable run

For day-to-day use, mount a named volume at `/data` and run under the full
hardened profile:

```bash
docker volume create conformance-suite-data

docker run --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  ghcr.io/openbankinguk/conformance-suite-v2:<version>
```

This is exactly what `make docker` runs locally (see the repository
`Makefile`). What each flag buys you:

| Flag | Purpose |
| --- | --- |
| `-p 127.0.0.1:8443:8443` | Publishes the UI only to localhost; never binds `0.0.0.0` by default. |
| `--read-only` | The root filesystem cannot be written to anywhere except the mounts below. |
| `--cap-drop=ALL` | Drops every Linux capability; the process runs as unprivileged UID/GID `65532`. |
| `--security-opt=no-new-privileges` | Blocks privilege escalation via setuid binaries. |
| `--tmpfs /tmp:size=64m,mode=1777` | A small, size-bounded scratch space for the application's own temp-file use. |
| `-v conformance-suite-data:/data` | Persists the generated Django secret key and local TLS certificate, browser sessions, structured results, and execution logs across restarts. |

### What lives under `/data`

| Path | Contents |
| --- | --- |
| `/data/django-secret-key` | Mode-`0600` generated Django secret key, created on first run and reused afterwards. |
| `/data/tls/` | Generated local certificate and mode-`0600` private key used by the browser UI and PSU callback. |
| `/data/sessions/` | Server-side browser wizard session files. |
| `/data/results/` | Structured JSON results from browser/API and CLI runs inside the container (`CONFORMANCE_DATA_DIR` defaults to `/data`). |
| `/data/logs/` | NDJSON execution logs from browser/API and CLI runs inside the container. |

If `/data` is not mounted (as in the ephemeral quick start), the container
still starts: it falls back to an in-memory secret key and Django's own
`/tmp`-based session default. Only persistence is lost, not functionality.

## Optional certificate mount

Mount a read-only directory at `/certs` to supply TLS/mTLS and FAPI signing
material without ever baking it into the image:

```bash
docker run --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  ghcr.io/openbankinguk/conformance-suite-v2:<version>
```

`/certs` is never copied, cached, or written elsewhere in the container — it
is read directly by the application at request time. Populate it with exactly
the artifacts your test plan or model-bank config references by absolute
path:

| `/certs` artifact | Referenced by | Inline alternative |
| --- | --- | --- |
| CA bundle | `tls.caBundlePath` | `tls.caBundlePem` |
| Transport/mTLS certificate | `tls.clientCertificatePath` | `tls.clientCertificatePem` |
| Transport/mTLS private key | `tls.clientPrivateKeyPath` | `tls.clientPrivateKeyPem` |
| Signing certificate | `fapiSigning.signingCertificatePath` | `fapiSigning.signingCertificatePem` |
| Signing private key | `fapiSigning.signingPrivateKeyPath` | `fapiSigning.signingPrivateKeyPem` |

Every one of the path fields must be an absolute path (for example
`/certs/transport.pem`); relative paths are rejected. Any subset may be
omitted if your test plan does not require it — for example, discovery-only
runs need none of them.

The mount is optional. Each credential may instead be supplied as inline PEM
text using the `*Pem` sibling key shown above, which the browser wizard writes
when you paste a credential or upload a file. A credential must be supplied
exactly once: giving both the path and the inline key for the same credential
is rejected. Inline material is held in memory for the life of the run, is
redacted from logs, results, and safe plan exports, and is written to a
short-lived `0600` temporary file only where OpenSSL cannot accept in-memory
client-certificate material. Mounting `/certs` remains the better option for
unattended or repeated runs, because the material stays outside the browser
session.

## Compose equivalent

`compose.yaml` in the repository root provides the same durable, hardened
profile:

```bash
docker compose up
```

To also mount certificates, layer the optional override:

```bash
CERTS_DIR=/path/to/your/certs docker compose -f compose.yaml -f compose.certs.yaml up
```

`CERTS_DIR` defaults to `./local-config/certs` (the same directory used for
local development certificates) if unset.

## Transport

The container serves HTTPS on port 8443 using a generated self-signed
certificate whose subject alternative names cover `localhost`, `127.0.0.1`,
`::1`, and the legacy FCS callback host `0.0.0.0`. With a named `/data` volume,
the certificate and private key persist across restarts; without one they are
ephemeral. This preserves compatibility with the legacy registered callback
`https://0.0.0.0:8443/conformancesuite/callback` while Docker publishes the
port only on host loopback. UI TLS/LAN exposure beyond localhost remains out
of scope.

## Health check

The image ships an exec-form `HEALTHCHECK` that probes `GET /health/` inside
the container (visible via `docker inspect --format='{{json .State.Health}}'
<container>`). It requires no host networking and works the same whether or
not `/data`/`/certs` are mounted.

## Overriding the command

The default `CMD` starts the ASGI server. Any override (for example the
existing headless CLI, `python3 main.py ...`) still runs through
the same entrypoint, so secret-key generation, `/data` preparation, and safe
environment defaults apply identically:

```bash
docker run --rm \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  -v /path/to/your/test-plan.json:/test-plan.json:ro \
  ghcr.io/openbankinguk/conformance-suite-v2:<version> \
  python3 main.py --test-plan /test-plan.json
```

Structured results and execution logs from this run land under
`/data/results/test-results.json` and `/data/logs/execution-log.ndjson` by
default (see [What lives under `/data`](#what-lives-under-data)).
