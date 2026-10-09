# Docker deployment guide

[Project README](../README.md) | [Installation guide](INSTALLATION_GUIDE.md) |
[Usage guide](USAGE_GUIDE.md)

**FCS v2 `2.0.0-beta.12` is an evaluation beta, not a certification release.**
Use it to try the new Functional Conformance Suite and provide feedback. Do
not use beta runs or reports as certification evidence, even if the builder
or a result displays certification-related labels.

The primary beta.11 workflow is the local browser UI: run the Docker image,
build a test plan, and paste credentials into the builder. The image runs
non-root and requires no manually supplied Django secret. The persistence,
certificate mount, Compose, and CLI options later in this guide are advanced
reference rather than prerequisites for the beta UI.

## Beta quick start: browser UI

Install and start Docker, then run:

```bash
docker run --pull=always --rm -p 127.0.0.1:8443:8443 openbanking/conformance-suite-v2:2.0.0-beta-latest
```

`2.0.0-beta-latest` tracks the newest published 2.0.0 beta. Docker pulls the
current image from Docker Hub on each launch, even if an older image is cached.
Open `https://127.0.0.1:8443/` on the same computer and accept the browser's
warning for the locally generated self-signed HTTPS certificate. Keep the
`127.0.0.1` Docker port binding so the UI stays local.

Continue with the [browser builder walkthrough](USAGE_GUIDE.md#browser-builder-workflow)
to select your scope, supply credentials, review and launch. Paste/upload needs
no `/certs` mount; absolute paths refer to files inside the container. Supply
each credential by only one method. Your registered PSU redirect URI is
independent of the UI address.

See [run monitoring and results](USAGE_GUIDE.md#monitor-a-run-and-interpret-results)
for keeping evidence and interpreting outcomes. Safe plan exports omit secrets;
exports with secrets must be protected.

Stop with Ctrl+C. With this disposable command, browser sessions, generated
results, logs, and the local certificate are lost when the container exits;
save anything you need first. See [Advanced: persistent local
run](#advanced-persistent-local-run) if you need state across restarts.

## Advanced: image tags and pulling

Published images are available from Docker Hub at
`docker.io/openbanking/conformance-suite-v2`:

```bash
docker pull docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
```

If the repository requires authentication, log in to Docker Hub with
credentials that grant access before pulling.

For reproducible runs, replace `2.0.0-beta-latest` in the commands in this guide
with an exact published tag, for example `2.0.0-beta.12` once published.
See [`CICD_STRATEGY.md`](CICD_STRATEGY.md) for the full preview/beta/GA
versioning and promotion model. `latest` refers only to the current GA release.
For convenient MVP beta evaluation, `2.0.0-beta-latest` tracks the highest
published `2.0.0-beta.N` version, updated automatically after approved promotion
and attestations succeed. It stops updating once the formal `2.0.0` image is
published and remains a superseded beta, never a GA image. Other release series
do not move it. Exact version tags remain preferable for reproducible runs.
Neither beta tag is suitable for certification.

```bash
docker pull docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
docker run --pull=always --rm -p 127.0.0.1:8443:8443 docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
```

Use `--pull=always` when starting a new container, as shown above, or pull
explicitly beforehand: a moving tag alone does not refresh a cached image or an
already running container. The alias is maintained by eligible beta promotions;
existing beta images are not automatically retagged when documentation changes.

The former `beta-latest` tag is no longer maintained. Existing registry tags are
not deleted by this change; switch pull commands to `2.0.0-beta-latest`.

## Advanced: persistent local run

To retain browser sessions, results, and logs across restarts, mount a named
volume at `/data` and run under the full hardened profile:

```bash
docker volume create conformance-suite-data

docker run --pull=always --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
```

This uses the same hardened profile as `make docker`, which builds and runs a
local image instead (see the repository `Makefile`). What each flag buys you:

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

The volume persists files, not active execution or process-local run/feedback
stores. Restarting does not resume a run or restore its run-detail API record.

## Advanced: optional certificate mount

Mount a read-only directory at `/certs` to supply TLS/mTLS and FAPI signing
material without ever baking it into the image:

```bash
docker run --pull=always --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
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

## Advanced: Compose equivalent

`compose.yaml` in the repository root provides the same durable, hardened
profile. Select the beta tag explicitly, since the Compose file defaults to
the GA `latest` tag, and pull the current image on launch:

```bash
CONFORMANCE_SUITE_VERSION=2.0.0-beta-latest docker compose up --pull always
```

To also mount certificates, layer the optional override:

```bash
CONFORMANCE_SUITE_VERSION=2.0.0-beta-latest CERTS_DIR=/path/to/your/certs \
  docker compose -f compose.yaml -f compose.certs.yaml up --pull always
```

`CERTS_DIR` defaults to `./local-config/certs` (the same directory used for
local development certificates) if unset.

## Transport

The container serves HTTPS on port 8443 using a generated self-signed
certificate covering `localhost`, `127.0.0.1`, `::1`, and the legacy FCS
callback host `0.0.0.0`. With a named `/data` volume, the certificate and
private key persist across restarts; without one they are ephemeral.

If your existing ASPSP client is registered for the legacy redirect URI
`https://0.0.0.0:8443/conformancesuite/callback`, enter that exact URI in
the builder and visit `https://0.0.0.0:8443/` to accept its browser
certificate warning before PSU authorisation. On supported local hosts
it reaches the same container; keep the Docker port bound to `127.0.0.1`.
UI TLS/LAN exposure beyond localhost remains out of scope.

## Health check

The image ships an exec-form `HEALTHCHECK` that probes `GET /health/` inside
the container (visible via `docker inspect --format='{{json .State.Health}}'
<container>`). It requires no host networking and works the same whether or
not `/data`/`/certs` are mounted.

Docker repeats the probe every 30 seconds to detect failures after startup.
Successful loopback `GET /health/` requests are omitted from the container's
Uvicorn access log to avoid repetitive terminal output. Failed health requests
and other access logs (including browser requests for `/favicon.ico`) remain
visible; Docker still records the container's health status.

## Advanced: overriding the command

The default `CMD` starts the ASGI server. Any override (for example the
existing headless CLI, `python3 main.py ...`) still runs through
the same entrypoint, so secret-key generation, `/data` preparation, and safe
environment defaults apply identically:

```bash
docker run --pull=always --rm \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  -v /path/to/your/test-plan.json:/test-plan.json:ro \
  docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest \
  python3 main.py --test-plan /test-plan.json
```

For manual PSU authorisation, the CLI starts its own HTTPS callback listener
inside the container on the `redirectUri` port. Publish that port to the host
on loopback, for example `-p 127.0.0.1:8443:8443` for
`https://0.0.0.0:8443/conformancesuite/callback`, so the browser redirect
reaches the run. Plans with `"execution": {"psuAuthorization": {"mode":
"auto-approve"}}` don't need a published port. See
[PSU authorisation and pipeline runs](USAGE_GUIDE.md#psu-authorisation-and-pipeline-runs).

Structured results and execution logs from this run land under
`/data/results/test-results.json` and `/data/logs/execution-log.ndjson` by
default (see [What lives under `/data`](#what-lives-under-data)).
