# Installation guide

[Project README](../README.md) | [Usage guide](USAGE_GUIDE.md) |
[Developer guide](DEVELOPER_GUIDE.md)

**FCS v2 `2.0.0-beta.11` is for evaluation only, not certification.** Docker,
locally built images and source runs have the same beta restriction. Continue
using FCS v1 for certification.

## Choose a setup

| Setup | Requirements | Use when |
| --- | --- | --- |
| Published Docker image (recommended) | Docker installed and running; access to Docker Hub | You want to evaluate the browser UI without installing Python. |
| Run from source | Git, Python 3.14.4+, uv, GNU Make and OpenSSL | You want to inspect the source or develop locally. |
| Build a local image | Source checkout, Docker, access to `dhi.io` base images | You want to evaluate packaging changes; see the developer guide. |

An ASPSP sandbox, client credentials and business test data are needed to
execute the selected tests, not to start the UI. See
[Before you run](USAGE_GUIDE.md#before-you-run).

## Published Docker image

Install Docker using its platform instructions, start its daemon/Desktop, then:

```bash
docker run --pull=always --rm -p 127.0.0.1:8443:8443 openbanking/conformance-suite-v2:2.0.0-beta-latest
```

Open `https://127.0.0.1:8443/` on the same computer. A main menu with
**Create new test plan with builder** indicates the application is ready.
The locally generated self-signed certificate causes a browser warning; check
that you are connecting to your own local container. This is not an instruction
to ignore certificate errors when connecting to an ASPSP.

Keep the `127.0.0.1` port binding: this beta workflow is local-only. The container
runs non-root and generates its own Django secret and local TLS material; you
do not need to set a secret, mount credentials or run migrations to start.

Stop with Ctrl+C. This disposable command loses browser sessions, local TLS
material, results and logs when the container exits. Download anything needed
before stopping. Relaunch the command to start again.

`--pull=always` refreshes the image when launching a new container; it does not
update an active container. Use an exact published beta tag for reproducibility.
The moving `2.0.0-beta-latest` tag is never a certification/GA tag.
For full tag semantics, see [image tags](DOCKER_GUIDE.md#advanced-image-tags-and-pulling).

## Persistence and advanced deployment

Use the [Docker deployment guide](DOCKER_GUIDE.md) when you need:

- [Persistent sessions, results and logs](DOCKER_GUIDE.md#advanced-persistent-local-run).
- [Read-only certificate mounts](DOCKER_GUIDE.md#advanced-optional-certificate-mount).
- [Compose](DOCKER_GUIDE.md#advanced-compose-equivalent).
- [Local TLS and legacy callbacks](DOCKER_GUIDE.md#transport).
- [Container health checks](DOCKER_GUIDE.md#health-check).
- [Running the CLI in Docker](DOCKER_GUIDE.md#advanced-overriding-the-command).

Persistence retains files, not live execution or process-local run/feedback
stores. It does not resume interrupted tests after restart.

## Alternative: run from source

Install Git, Python 3.14.4 or later, [uv](https://docs.astral.sh/uv/), GNU Make
and OpenSSL. `.python-version` pins 3.14.4; `pyproject.toml` requires at least
that version. Ensure OpenSSL supports the `req -addext` option used by `make dev`.
On Windows, use a Linux environment such as WSL for these Make/shell commands.

```bash
git clone https://github.com/OpenBankingUK/conformance-suite-v2.git
cd conformance-suite-v2
uv sync --frozen --no-install-project
make dev
```

These commands use the repository's default branch; to reproduce a particular
beta, check out its corresponding release ref before syncing dependencies.

`make dev` creates a self-signed certificate under `local-config/certs/` on
first use and runs the auto-reloading HTTPS server. Open
`https://127.0.0.1:8443/`, check the local certificate warning, and follow the
[Usage guide](USAGE_GUIDE.md). There is no separate build step or database
migration for the default file-backed builder sessions. Stop with Ctrl+C.

Unlike Docker's loopback-only published port, `make dev` listens on all network
interfaces. Use it on a trusted local development machine; do not expose it to
an untrusted network.

`make serve` runs without reload or Django debug mode, but uses **plain HTTP**:
open `http://127.0.0.1:8443/`, not HTTPS. It is a local behaviour check, not a
replacement for the container's hardened TLS deployment.

To change code or build your own image with `make docker`, follow the
[Developer guide](DEVELOPER_GUIDE.md). Pulling the build's Docker Hardened Image
base requires `docker login dhi.io` with an appropriate Docker account; this is
separate from pulling the published participant image.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Docker cannot connect to its daemon | Start Docker Desktop/daemon before running the command. |
| Pull denied or tag missing | Check registry access and the published tag. If access requires authentication, log in with credentials authorised for that registry. |
| Port 8443 is already in use | Stop the conflicting local server/container or use another loopback host port, e.g. `-p 127.0.0.1:9443:8443`, and open `https://127.0.0.1:9443/`. A changed UI port does not change your registered PSU redirect URI. |
| Browser cannot connect | Wait for server startup and check terminal errors. Use HTTPS for Docker/`make dev`, HTTP for `make serve`. |
| Certificate warning | Verify the address belongs to your local instance. Never disable ASPSP TLS verification to resolve a local UI warning. |
| Source startup fails | Confirm the Python minimum, run `uv sync --frozen --no-install-project`, and check Make/OpenSSL availability and the actual error. |
| Sessions/results disappear | The quick start is disposable. Use a named volume before runs whose files you need to retain. |

After the main menu loads, installation is complete. Configuration, callback
problems, failed checks and result interpretation belong in the
[Usage guide](USAGE_GUIDE.md#troubleshooting).
