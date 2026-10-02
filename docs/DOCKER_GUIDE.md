# Docker deployment and image tags

## Local hardened runtime

Authenticate with `docker login dhi.io` before building the Docker Hardened
Image bases, then run `make docker` or `docker compose up --build`.
The image runs as non-root UID/GID `65532` without a shell, serves HTTPS on
port `8443`, and generates its Django secret and self-signed TLS identity.
The local profile publishes only `127.0.0.1:8443`; open
`https://127.0.0.1:8443/` and accept the local certificate warning.

Compose and `make docker` use a read-only root filesystem, dropped
capabilities, no-new-privileges, a private writable `/tmp`, and a named
`/data` volume. The volume persists the secret, TLS identity, and browser
sessions. `/data/results` and `/data/logs` are writable locations for
explicitly configured artifact output paths; existing participant-selected
paths are not rewritten.

For a read-only certificate mount, use:

```bash
CERTS_DIR=/path/to/your/certs docker compose -f compose.yaml -f compose.certs.yaml up --build
```

Container paths, not host paths, must be used in participant configuration.
An operator-supplied command replaces the default server command, so CLI
execution remains available. The runtime has no shell; use exec-form commands.

## Published image tags

Published images are available from Docker Hub at
`docker.io/openbanking/conformance-suite-v2`:

```bash
docker pull docker.io/openbanking/conformance-suite-v2:<version>
```

Replace `<version>` with an exact published preview, beta, or GA version.
Exact version tags are immutable and remain preferable for reproducible runs.
See [`CICD_STRATEGY.md`](CICD_STRATEGY.md) for the full versioning and
Environment-approved promotion model.

`latest` refers only to the current GA release. For convenient MVP beta
evaluation, `2.0.0-beta-latest` tracks the highest published `2.0.0-beta.N`
version, updated automatically after approved publication and
provenance/SBOM attestations succeed. It stops updating once the formal
`2.0.0` image is published and remains a superseded beta, never a GA image.
Other release series do not move it. Beta images are for evaluation, not
certification.

```bash
docker pull docker.io/openbanking/conformance-suite-v2:2.0.0-beta-latest
```

Pull explicitly before starting a new container: a moving tag does not refresh
a cached image or an already running container. The alias is seeded from the
`2.0.0-beta.4` manifest; future eligible beta promotions maintain it. Existing
beta images are not automatically retagged when this feature is deployed.

The former `beta-latest` tag is no longer maintained. Existing registry tags are
not deleted by this change.
