# Docker deployment guide

**FCS v2 `2.0.0-beta.1` is an evaluation beta, not a certification release.**
Use it to try the new Functional Conformance Suite and provide feedback. Do
not use beta runs or reports as certification evidence, even if the builder
or a result displays certification-related labels.

The primary beta.1 workflow is the local browser UI: run the Docker image,
build a test plan, and paste credentials into the builder. The image runs
non-root and requires no manually supplied Django secret. The persistence,
certificate mount, Compose, and CLI options later in this guide are advanced
reference rather than prerequisites for the beta UI.

## Beta.1 quick start: browser UI

Install and start Docker, then run:

```bash
docker run --rm -p 127.0.0.1:8443:8443 openbanking/conformance-suite-v2:2.0.0-beta.1
```

Docker pulls the image from Docker Hub if it is not already available locally.
Open `https://127.0.0.1:8443/` on the same computer and accept the browser's
warning for the locally generated self-signed HTTPS certificate. Keep the
`127.0.0.1` Docker port binding so the UI stays local.

1. Select **Create new test plan with builder** on the main menu. Choose the
   scheme, specification, and version, then provide your OpenID discovery URL
   and the OAuth/FAPI and resource-server values relevant to your environment.
2. On **OAuth and security configuration**, select **Paste or upload** under
   **Supply this credential as** for each required credential. Paste your PEM
   certificate or private key into **Paste PEM text** (and supply the other
   credentials requested for your chosen plan). Leave **Absolute file path**
   blank: a path on your computer is not a path inside the container. No
   `/certs` mount is needed for pasted credentials. Supply each credential by
   only one method.
3. Select implemented endpoints and optional capabilities, fill in the
   requested business data, and review the generated plan.
   The builder indicates required fields and any launch blockers. Choose
   **Launch run** when ready; a PSU authorisation handoff may still be needed
   during execution. For tests requiring PSU authorisation, enter the exact
   callback URI registered for your test client with the ASPSP in the
   builder's **Redirect URI** field; it is independent of the UI address.
4. Inspect the run details and open the **JSON** masked report if you want to
   keep it. **Export safe JSON** at plan review removes secret values and
   requires you to re-enter them after import. **Export with secrets** includes
   sensitive values: avoid it unless necessary and protect any copy you make.
   Neither export nor the result is certification evidence in beta.1.

Stop with Ctrl+C. With this disposable command, browser sessions, generated
results, logs, and the local certificate are lost when the container exits;
save anything you need first. See [Advanced: persistent local
run](#advanced-persistent-local-run) if you need state across restarts.

## Advanced: image tags and pulling

Published images are available from Docker Hub at
`docker.io/openbanking/conformance-suite-v2`:

```bash
docker pull docker.io/openbanking/conformance-suite-v2:<version>
```

If the repository requires authentication, log in to Docker Hub with
credentials that grant access before pulling.

Replace `<version>` with an exact published tag, for example `2.0.0-beta.1`.
See [`CICD_STRATEGY.md`](CICD_STRATEGY.md) for the full preview/beta/GA
versioning and promotion model. Never rely on `latest` for anything other than
the current GA release, and never use a mutable channel-named tag — none is
published.

## Advanced: persistent local run

To retain browser sessions, results, and logs across restarts, mount a named
volume at `/data` and run under the full hardened profile:

```bash
docker volume create conformance-suite-data

docker run --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  docker.io/openbanking/conformance-suite-v2:<version>
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

## Advanced: optional certificate mount

Mount a read-only directory at `/certs` to supply TLS/mTLS and FAPI signing
material without ever baking it into the image:

```bash
docker run --rm -p 127.0.0.1:8443:8443 \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  docker.io/openbanking/conformance-suite-v2:<version>
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

## Advanced: overriding the command

The default `CMD` starts the ASGI server. Any override (for example the
existing headless CLI, `python3 main.py ...`) still runs through
the same entrypoint, so secret-key generation, `/data` preparation, and safe
environment defaults apply identically:

```bash
docker run --rm \
  -v conformance-suite-data:/data \
  -v /path/to/your/certs:/certs:ro \
  -v /path/to/your/test-plan.json:/test-plan.json:ro \
  docker.io/openbanking/conformance-suite-v2:<version> \
  python3 main.py --test-plan /test-plan.json
```

Structured results and execution logs from this run land under
`/data/results/test-results.json` and `/data/logs/execution-log.ndjson` by
default (see [What lives under `/data`](#what-lives-under-data)).
