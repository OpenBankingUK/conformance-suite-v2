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

## OAuth redirect policy

For local PSU callbacks, enter the exact redirect URI registered for your
client in the builder. Both `https://127.0.0.1:8443/conformancesuite/callback`
and the FCS v1 callback `https://0.0.0.0:8443/conformancesuite/callback` are
supported. Before PSU authorisation, visit the matching origin to accept the
local certificate warning. The browser must run on the Docker host; these
addresses refer to the browser's own machine, not a remote Docker server.
Keep the published port bound to `127.0.0.1`.

The generated callback certificate covers `localhost`, `127.0.0.1`, `::1` and
`0.0.0.0`. It is separate from the TPP's outbound mTLS certificate and signing
key; changing a callback must not disable verification of ASPSP TLS.

FCS applies the same redirect policy to OAuth configuration, PSU steps and DCR
redirect values. Acceptance by FCS is not proof of Directory or bank registration.

| URI | FCS policy |
| --- | --- |
| HTTPS with a DNS hostname other than `localhost` or a `.localhost` name | Supported; use a controlled hostname for hosted deployments. |
| HTTPS with a dotted-decimal IPv4 literal, such as `127.0.0.1` or `192.168.1.10` | Supported, including a configured port, callback path and fixed query. The address must be registered, reachable by the browser and named by the callback certificate. |
| HTTPS with a bracketed IPv6 literal, such as `https://[::1]:8443/conformancesuite/callback` | Supported. In Docker, publish the port on the host's IPv6 loopback (for example `-p [::1]:8443:8443`), which depends on your Docker platform. The default `DJANGO_ALLOWED_HOSTS` includes `[::1]`; add it yourself if you override that variable. |
| `https://0.0.0.0:8443/conformancesuite/callback` (also with a trailing slash) | Supported only with this host, port and path and no query or fragment. |
| HTTPS with `localhost` | Rejected under OB DCR 3.4, including case/trailing-dot variants and `.localhost` names. |
| Other unspecified, multicast or reserved addresses (`0.0.0.0/8`, `240.0.0.0/4`, broadcast); IPv6 link-local, scoped or IPv4-mapped addresses | Rejected: not valid unicast destinations for a browser redirect. |
| Non-canonical IPv4 spellings (`127.1`, `2130706433`, `0x7f000001`, leading zeros, trailing dot) and bracketed IPv4 | Rejected as ambiguous; use dotted-decimal. |
| HTTP, embedded credentials, fragments, backslashes or wildcard hostnames | Rejected. A fixed query is allowed and must match the registration. A `*` in a path or query is a literal character: FCS never expands wildcards. |

Outbound targets such as discovery, token and API endpoints are validated
separately and still require HTTPS DNS hostnames. FCS preserves the configured
redirect exactly: it does not rewrite `0.0.0.0` to `127.0.0.1`, or the reverse,
and does not select a random port. The unspecified `0.0.0.0` address is retained
only for compatibility with the FCS v1 registered callback.

To change an existing client's redirect:

1. Add the exact URI to the software statement's redirect list in the Directory
   or with your SSA issuer, then obtain a newly issued/signed SSA. Do not edit an
   existing signed JWT.
2. Register or update the client at the ASPSP using an allowed subset of the
   SSA's `software_redirect_uris`. Confirm which URIs the bank registered.
3. Change the FCS redirect only after registration succeeds. Preserve the exact
   scheme, host, port, path, trailing slash and any query.
4. Ensure the callback listener is reachable and its certificate covers the
   chosen host: an IP SAN for an IP literal, or a DNS SAN for a DNS hostname.
   Hosts not named by the bundled certificate need a listener or reverse proxy
   that you control with a suitable certificate.

No SSA schema change is needed, and FCS does not modify a participant's
Directory entry or bank registration. Directory/ASPSP acceptance remains
environment-specific. The read/write workflow uses an existing registration
and does not independently verify its SSA or fetch its registered redirect list.

`0.0.0.0` as the container bind address ("listen on all container interfaces")
is separate from a redirect URI, which is the browser's destination. Likewise,
`localhost` may still be used to visit the UI; the restriction applies to OAuth
redirects, not every local HTTPS URL.

### Standards basis

- [RFC 6749 section 3.1.2](https://www.rfc-editor.org/rfc/rfc6749#section-3.1.2)
  requires an absolute redirect URI without a fragment; a fixed query is allowed.
- [OB DCR 3.4, `redirect_uris`](https://openbankinguk.github.io/dcr-docs-pub/v3.4/dynamic-client-registration.html#data-dictionary)
  requires HTTPS, forbids host `localhost`, and requires redirects to match or
  be a subset of a master set defined by the SSA. It does not prohibit all IP
  literals or guarantee any given Directory/ASPSP accepts them.
- [FAPI 1 Baseline section 5.2.2, items 8-10 and 20](https://openid.net/specs/openid-financial-api-part-1-1_0.html#authorization-server)
  requires pre-registration, exact matching and HTTPS.
- [RFC 9525 section 6.4](https://www.rfc-editor.org/rfc/rfc9525.html#section-6.4)
  matches an IP-address identity against an `iPAddress` subject alternative name.
- [IANA IPv4 Special-Purpose Address Space](https://www.iana.org/assignments/iana-ipv4-special-registry/)
  distinguishes unspecified `0.0.0.0` from loopback `127.0.0.0/8`.
- [RFC 8252 sections 7.3 and 8.3](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.3)
  prefer loopback IP literals to `localhost` for native apps. This supports the
  address choice, not an exemption from OB HTTPS or FAPI exact matching, nor
  reclassification of the DCR web client as native.

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
