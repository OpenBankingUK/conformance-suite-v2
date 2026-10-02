# Docker image tags

Published images are available from Docker Hub at
`docker.io/openbanking/conformance-suite-v2`:

```bash
docker pull docker.io/openbanking/conformance-suite-v2:<version>
```

Replace `<version>` with an exact published preview, beta, or GA version.
Exact version tags are immutable and remain preferable for reproducible runs.
See [`CICD_STRATEGY.md`](CICD_STRATEGY.md) for the full versioning and
Environment-approved promotion model.

`latest` refers only to the current GA release. For convenient beta evaluation,
`beta-latest` tracks the highest published beta version across release branches,
updated automatically after approved publication and provenance/SBOM
attestations succeed. Beta images are for evaluation, not certification.

```bash
docker pull docker.io/openbanking/conformance-suite-v2:beta-latest
```

Pull explicitly before starting a new container: a moving tag does not refresh
a cached image or an already running container. The alias is first created by
an eligible future beta promotion; existing beta images are not automatically
retagged when this feature is deployed. Older beta backfills, preview releases,
and GA releases leave `beta-latest` unchanged.
