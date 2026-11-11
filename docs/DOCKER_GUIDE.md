# Docker deployment guide

[Project README](../README.md) | [Installation guide](INSTALLATION_GUIDE.md) |
[Usage guide](USAGE_GUIDE.md)

**FCS v2 `2.0.0-beta.17` is an evaluation beta, not a certification release.**
Use it to try the new Functional Conformance Suite and provide feedback. Do
not use beta runs or reports as certification evidence, even if the builder
or a result displays certification-related labels.

The primary beta.17 workflow is the local browser UI: run the Docker image,
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
with an exact published tag, for example `2.0.0-beta.17` once published.
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
certificate covering `localhost`, `127.0.0.1`, `::1`, and the FCS v1 callback
host `0.0.0.0`. With a named `/data` volume, the certificate and
private key persist across restarts; without one they are ephemeral.

For local PSU callbacks, enter the exact redirect URI registered for your
client in the builder. The usual local forms are:

- `https://127.0.0.1:8443/conformancesuite/callback`
- `https://0.0.0.0:8443/conformancesuite/callback` (the FCS v1 callback)

Before PSU authorisation, visit the matching origin (`https://127.0.0.1:8443/`
or `https://0.0.0.0:8443/`) to accept the local certificate warning. The
browser must run on the Docker host, because both addresses refer to the
browser's own machine rather than a remote Docker server; on supported local
hosts they reach the same container. Keep the published port bound to
`127.0.0.1`. This certificate is separate from the TPP's outbound mTLS
certificate and signing key; changing the callback must not disable
verification of ASPSP TLS. UI TLS/LAN exposure beyond localhost remains out of
scope.

### OAuth redirect policy

FCS applies the same redirect policy to OAuth configuration, PSU steps and DCR
redirect values. It enforces what the cited standards require (an absolute
HTTPS URI with no fragment, no `localhost` host, and exact matching against the
registered value) and rejects forms that are ambiguous or not valid unicast
destinations. Acceptance by FCS is not proof of Directory or bank registration.

| URI | FCS policy |
| --- | --- |
| HTTPS with a DNS hostname other than `localhost` or a `.localhost` name | Supported; use a controlled hostname for hosted deployments. |
| HTTPS with a dotted-decimal IPv4 literal, such as `127.0.0.1` or `192.168.1.10` | Supported, including a configured port, callback path and fixed query. The address must be registered, reachable by the browser and named by the callback certificate. |
| HTTPS with a bracketed IPv6 literal, such as `https://[::1]:8443/conformancesuite/callback` | Supported. The CLI listener binds `::1` for a `[::1]` redirect outside a container. In Docker the port must also be published on the host's IPv6 loopback (for example `-p [::1]:8443:8443`), which depends on your Docker platform. The default `DJANGO_ALLOWED_HOSTS` already includes `[::1]`; add it yourself if you override that variable. |
| `https://0.0.0.0:8443/conformancesuite/callback` (also with a trailing slash) | Supported: the FCS v1 callback, accepted only with this exact host, port and path and no query or fragment. |
| HTTPS with `localhost` | Rejected under OB DCR 3.4; case/trailing-dot variants and `.localhost` names are also rejected. |
| `0.0.0.0` with any other port, path or query; other unspecified, multicast or reserved addresses (`0.0.0.0/8`, `240.0.0.0/4`, broadcast); IPv6 link-local, scoped or IPv4-mapped addresses | Rejected: not valid unicast destinations for a browser redirect. |
| Non-canonical IPv4 spellings (`127.1`, `2130706433`, `0x7f000001`, leading zeros, trailing dot) and bracketed IPv4 | Rejected as ambiguous; use dotted-decimal. |
| HTTP, embedded credentials, fragments, backslashes or wildcard hostnames | Rejected. A fixed query is allowed and must match the registration. A `*` in a path or query is a literal character: FCS never expands wildcards. |

Outbound targets such as discovery, token and API endpoints are validated
separately and still require HTTPS DNS hostnames; this redirect policy does not
change them.

The `0.0.0.0` callback is not extended to other ports or paths, because
`0.0.0.0` is the unspecified address rather than a designated loopback
destination (see [Standards basis](#standards-basis)). FCS preserves the
configured URI exactly: it does not rewrite `0.0.0.0` to `127.0.0.1`, or the
reverse, and does not select a random port.

To add or change a redirect for an existing client, work in this order:

1. Add the exact URI to the software statement's redirect list in the
   Directory or with your SSA issuer, then obtain a newly issued/signed SSA.
   Do not edit the payload of an existing signed JWT.
2. Register or update the client at the ASPSP using an allowed subset of the
   SSA's `software_redirect_uris`. Confirm which URIs the bank actually registered.
3. Change the FCS redirect only after registration succeeds. Preserve the exact
   scheme, host, port, path, trailing slash and any query.
4. Ensure the callback listener is reachable and its certificate covers the
   chosen host: an IP SAN for an IP literal, or a DNS SAN for a DNS hostname.
   The bundled Docker certificate names only `127.0.0.1`, `::1`, `0.0.0.0` and
   `localhost` (which is not a permitted redirect host). Any other host needs a
   listener or reverse proxy that you control with a suitable certificate;
   otherwise the browser reports a certificate name mismatch.

No SSA schema change is needed, and FCS does not modify a participant's Directory
entry or bank registration. Directory/ASPSP acceptance remains environment-specific;
if your Directory or ASPSP does not accept the redirect you need, use an approved,
reachable HTTPS DNS callback with suitable certificates and routing instead. The
read/write workflow uses an existing client registration and does not independently
verify its SSA or fetch its registered redirect list.

`0.0.0.0` as the **container bind address** ("listen on all container
interfaces") is a separate, unchanged concern from the registered redirect URI,
which is the destination the browser is sent to. Likewise, `localhost` may still
be used to visit the UI; the DCR restriction applies to the OAuth redirect, not
every local HTTPS URL.

### Standards basis

- [IANA IPv4 Special-Purpose Address Space](https://www.iana.org/assignments/iana-ipv4-special-registry/)
  distinguishes `0.0.0.0/32` ("This host on this network") from `127.0.0.0/8`
  (loopback), referencing RFC 1122 section 3.2.1.3. `0.0.0.0` is an unspecified
  address, not the designated loopback destination, and its local-destination
  behaviour is platform-dependent. FCS therefore supports it only as the FCS v1
  callback shape rather than as a general redirect host. The registry's
  destination/forwarding flags concern traffic between devices, not whether
  loopback works within one machine.
- [RFC 8252 sections 7.3 and 8.3](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.3)
  identify `127.0.0.1` / `::1` as loopback redirect addresses and prefer IP literals
  to `localhost` to avoid name-resolution and interface-selection problems.
  **Scope matters:** this is native-app guidance, with HTTP and flexible-port
  provisions. It supports the address choice, not an exemption from OB HTTPS,
  FAPI exact matching, or permission to reclassify FCS's DCR web client as native.
- [RFC 6749 section 3.1.2](https://www.rfc-editor.org/rfc/rfc6749#section-3.1.2)
  requires an absolute redirect URI without a fragment; a query is allowed and,
  under exact matching, must match the registration.
- [RFC 9525 section 6.4](https://www.rfc-editor.org/rfc/rfc9525.html#section-6.4)
  matches an IP-address reference identity against an `iPAddress` subject
  alternative name, which is why the callback certificate must name any IP literal.
- [OpenID Connect Registration section 2](https://openid.net/specs/openid-connect-registration-1_0.html#ClientMetadata)
  requires exact redirect matching. Its `application_type` rules distinguish web
  and native clients; the HTTPS/`localhost` restriction there specifically concerns
  web clients using the implicit grant type.
- [OB DCR 3.4, `redirect_uris`](https://openbankinguk.github.io/dcr-docs-pub/v3.4/dynamic-client-registration.html#data-dictionary)
  independently requires HTTPS, forbids host `localhost`, and requires redirects
  to match or be a subset of a master set defined by the SSA. It does not explicitly
  prohibit all IP literals, nor guarantee that any given Directory/ASPSP accepts them.
- [FAPI 1 Baseline section 5.2.2, items 8-10 and 20](https://openid.net/specs/openid-financial-api-part-1-1_0.html#authorization-server)
  requires pre-registration, exact matching and HTTPS. These remain unchanged.

FCS enforces what these sources require (HTTPS, no fragment, exact matching and,
for OB DCR, no `localhost`) and does not otherwise restrict IP literals, which OB
DCR neither mandates nor lists. The narrow `0.0.0.0` acceptance is a
compatibility allowance for the FCS v1 callback; it does not depend on a claim
that browsers block `0.0.0.0` OAuth redirects. Acceptance by a Directory or
ASPSP remains environment-specific, and FCS assumes no test-tool exemption from
the specifications above.

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
`https://127.0.0.1:8443/conformancesuite/callback` or the FCS v1
`https://0.0.0.0:8443/conformancesuite/callback`, so the browser redirect
reaches the run. Plans with `"execution": {"psuAuthorization": {"mode":
"auto-approve"}}` don't need a published port. See
[PSU authorisation and pipeline runs](USAGE_GUIDE.md#psu-authorisation-and-pipeline-runs).

Structured results and execution logs from this run land under
`/data/results/test-results.json` and `/data/logs/execution-log.ndjson` by
default (see [What lives under `/data`](#what-lives-under-data)).
