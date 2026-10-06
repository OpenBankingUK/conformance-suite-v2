# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Every builder page now has an **Import plan** button beside **Main menu**. It imports a test plan JSON file or pasted JSON into the current draft, so switching from building to importing no longer needs a trip through the main menu. If the draft already has values, a **Replace current plan?** confirmation is shown first. **Cancel** returns to the page you came from.
- The review page's plan JSON box is now clearly marked as editable and has a **Load from file…** button. Pasted, typed or loaded JSON is applied automatically after a short pause, using the same rules as import. The summary, generated tests and step bar refresh without replacing the text box or moving the caret.
  - A status line shows **Updating…**, **Applied** or an error.
  - Incomplete or invalid JSON leaves the last applied plan untouched.
  - Older responses never overwrite newer edits.
  - Launch, export and step navigation still validate the current text.
- The review page's plan JSON box is larger and has JSON syntax highlighting. It is still a plain text box underneath, so typing, undo, paste and live apply behave as before.

### Changed

- Builder dropdowns on the specification and connection & security pages are
  restyled, with a consistent chevron, hover and focus states. Browsers that
  support customisable selects (`appearance: base-select`) also get a styled
  option list. The controls stay native, so keyboard and screen-reader
  behaviour is unchanged.
- The Read/Write builder now asks for scope before connection and security:
  specification → scope → connection & security → business data → review. The
  discovery URL moved onto the connection and security page, with an inline
  **Fetch and fill** action that fills empty OAuth fields from discovery
  metadata, summarises what was filled or kept, tags filled fields **From
  discovery**, and offers **Replace with discovery values** when typed values
  differ. DCR's discovery button is now **Preview discovery**. Each field
  shows **Required to run** (with the reason), **Optional**, or **Depends on
  scope** based on the selected endpoints, and the step shows needing attention
  (!) when a value the selected tests need is missing.
- Test plan validation now reports a missing OAuth client, OAuth endpoint, FAPI
  signing, discovery or resource-server value that the selected tests need to
  run, naming the `securityEnvironment` key and the reason. This applies to
  builder, import, REST and CLI plans. The mTLS client certificate and key are
  required only when the token endpoint auth method is `tls_client_auth`.

- Builder endpoint labels now use a codified, source-linked Read/Write
  Mandatory/Conditional/Optional matrix for 4.0.1, 4.0.0 and 3.1.11 rather than
  catalogue "Baseline" coverage. Mandatory covered endpoints and selected
  resource-POST dependencies are locked and restored server-side. Bulk deselection
  retains mandatory endpoints and required features. Specification conditions
  and conflicting source text remain explicit.
- The builder shows a step bar at the top of every step and the review page.
  Choosing a supported specification is the only gate: after that, new and
  imported plans can move freely between any steps, and the step bar marks each
  step complete (✓), needing attention (!), or not started from the saved data.
  Step targets come from a fixed allow-list, and typed URLs for later steps
  return to the specification step until one is chosen.
- Leaving a builder page by **Back**, **Continue**, or the step bar always saves
  what was entered. Missing values are left empty; a badly formatted value is
  kept as typed and flagged on its page and at review instead of blocking the
  page. Pasted or uploaded credential material that fails validation is never
  kept; a message says it was not saved and why.
- Builder step bar polish: steps keep the same position on every page (one
  shared page width and header), pills are smaller with a fixed height in every
  state, a divider sets **Review** apart, and **Main menu** is a compact
  secondary button. Stale "Next you will…" banners are removed, the draft id is
  shown on review only, Business data shows requirement badges beside labels and
  a styled empty state when no scope is selected, and review shows each blocker
  once (an empty scope no longer also shows the raw `resourceGroups` error).
- Locked builder steps on a new plan show a tooltip on hover and keyboard focus
  explaining that a specification must be selected first.
- A builder step that has been saved, or loaded from an imported plan, now shows
  needing attention (!) in the step bar when it still has issues, even if it is
  empty. For example, leaving Scope with no resource group, or Business data
  with required fields empty. Steps that have never been opened still show as
  not started.
- Review is the single validation gate: it lists each step's issues with a
  **Fix** button for that step, and launch stays blocked until they are
  resolved. Business data is empty until scope is selected and follows the
  saved scope; an empty or incomplete scope is reported at review.
- Changing the specification now lists the scope and data it would affect and
  asks for confirmation before saving; reselecting the same specification
  needs none.
- Importing a plan without a supported specification opens the specification
  step with the import warnings and later steps locked; the rest of the plan is
  loaded once a specification is chosen. An unusable import, or one without a
  specification, offers **Start a new plan instead**.
- Ticking a resource group on the builder scope step now selects all of its
  endpoints and optional features by default; ticking a single endpoint selects
  its optional features. **Select all endpoints and features** does the same for
  every selected group, and the deselect action only removes conditional and
  optional endpoints and features.

### Fixed

- The builder's OpenAPI document update dropdown now lists only the updates for
  the selected specification version in browsers that support the customisable
  select picker; previously hidden options for other versions were still shown.
- Builder business data no longer shows every resource group's fields when the
  selected scope cannot be resolved; it shows no fields and links to the scope
  step.
- Malformed advanced JSON on the builder business data step is now reported
  against its own field rather than rejecting the whole page.
- Builder business data labels for Confirmation of Funds debtor-account and VRP
  fields keep their **Required** badge when the page is saved incomplete; the
  badge reflects the selected scope's specification requirements rather than
  whether the builder blocks leaving the page.
- The run screen's **New plan** button is now a **Main menu** link, so
  participants choose between creating and importing a plan.
- The builder's specification step now preselects the latest OpenAPI document
  update for the chosen version instead of the earliest. A saved or imported
  update is still kept.

## [2.0.0-beta.7] - 2026-10-05

### Fixed

- Automatic and manual preview, beta, and GA promotions share a caller-level
  concurrency lock through publication and Git tag/GitHub Release finalization,
  preventing overlapping release boundaries without nested reusable-workflow locks.

### Added

- Selectable Open Banking Read/Write OpenAPI ("swagger") document updates.
  Plans choose one with `specification.openApiDocumentUpdate`. Response-schema
  checks then validate against that update's pinned upstream snapshot from
  `OpenBankingUK/read-write-api-specs`. Every historical update is bundled
  using upstream terminology:
  - `3.1.11`: `Baseline`, `Release-2` to `Release-5`
  - `4.0.0`: `Baseline`, `Release-2`, `Update-3` to `Update-5`
  - `4.0.1`: `Baseline`, `Update-1`
- The builder wizard has an **OpenAPI document update** selector under
  Version. It preselects the latest update.
- Result JSON `catalogue` now records `specificationVersion`,
  `endpointVersion` and `openApiDocumentUpdate` (update, label, catalogue ID,
  upstream tag and commit). The run page shows the selected update.
- Independent Read/Write `4.0.1` catalogues, copied from `4.0.0`, so the two
  versions' coverage can diverge.
- Automatic and manual image promotion run summaries now show the exact release
  version, proposed Docker tags and source commit before approval, including
  conditional `2.0.0-beta-latest` eligibility.
- Every approved beta and GA image publication now ends with a Git tag
  (`vX.Y.Z-beta.N` or `vX.Y.Z`) at the published commit and an immutable
  GitHub Release (a prerelease for betas). The release notes contain the
  version's `CHANGELOG.md` section, the `docker pull` command and image digest,
  and GitHub-generated pull-request notes since the previous release on the
  same channel. Re-running the finalize step is safe and never republishes the
  image.
- CI now fails pull requests to `main` or `release/**` that change
  `[project].version` to a GA version without a `CHANGELOG.md` section, and
  warns when a changed beta version has none. Pull requests that leave the
  version unchanged are not checked.
- Compare links for each released version at the end of `CHANGELOG.md`.

### Changed

- `.github/workflows/_finalize-ga-release.yml` is replaced by
  `.github/workflows/_finalize-release.yml`, which handles both beta and GA
  publications. The manual **Promote beta image** recovery workflow now also
  creates the tag and GitHub prerelease.
- **Breaking:** Read/Write plans must declare `specification.openApiDocumentUpdate`.
  Existing plans keep their previous schema behaviour by selecting the latest
  update: `3.1.11` → `Release-5`, `4.0.0` → `Update-5`. For `4.0.1`, use
  `Update-1`; the previously bundled 4.0.1 documents match `Baseline`.
  **Import test plan** still loads plans without it, selecting the latest
  update and showing an import warning; REST and CLI runs reject them.
- **Breaking:** `4.0.1` plans now validate against the `4.0.1` OpenAPI
  documents. Previously they used the shared `v4.0` (`4.0.0`) snapshot.
- **Breaking:** Read/Write specification version `4.0` was removed. Use `4.0.0`.
- **Breaking:** Bundled schema document IDs are now
  `ob-read-write/<catalogue-id>/<document>`, for example
  `ob-read-write/v4.0.0-Update-5/account-info-openapi`. The old
  `ob-read-write-v3.1.11-*`, `ob-read-write-v4.0-*` and
  `ob-read-write-v4.0.1-*` IDs no longer resolve.
- **Breaking:** Internal v1 plan specs require `catalogue.specificationVersion`
  and now always filter cases by specification version.

## [2.0.0-beta.6] - 2026-10-05

### Changed

- Participant Docker instructions now recommend `2.0.0-beta-latest` across
  browser, persistent, certificate-mounted, CLI, and Compose examples, with
  pull-on-launch options to avoid stale cached images. Exact-version guidance
  for reproducible runs and beta non-certification warnings remain in place.

## [2.0.0-beta.5] - 2026-10-03

### Added

- Lenient browser test-plan import. **Import test plan** now accepts an uploaded
  `.json` file as well as pasted JSON, and loads as much of an incomplete or
  partly invalid plan as possible into an editable builder draft instead of
  rejecting it. The review page lists import warnings for missing, invalid,
  unrecognised, and skipped fields. Fix them in the builder steps or in the review
  page's **Plan JSON** editor, now the single, unmasked and editable view of
  the plan (with a secrets notice). Launch, export, and the Edit-step buttons
  use the editor's contents directly, with no separate save. Launch still
  requires the plan to pass normal validation. The "Masked test plan summary" box and the "Edit raw JSON
  as new draft" form are replaced by this editor.

### Changed

- Renamed the temporary MVP beta pointer from `beta-latest` to
  `2.0.0-beta-latest`, selecting only the highest published 2.0.0 beta and
  freezing updates once formal `2.0.0` is published. The old registry tag is
  no longer maintained and is not deleted automatically.

## [2.0.0-beta.4] - 2026-10-02

### Added

- **Give beta feedback** across the browser UI, preparing a local, credential-
  and certificate-masked diagnostic ZIP, structured email text and an optional
  email-client link for `standardsteam@openbanking.org.uk`. Run logs/results and
  launch-time plans, or saved builder draft evidence, are captured when available.
  Nothing is sent automatically; secured-network and no-mail-client workflows
  use downloads and manual copying.
- Automatic Docker Hub `beta-latest` pointer updates after approved beta
  publication and provenance/SBOM attestations, with manifest-digest
  verification. Docker Hub's highest published beta across release branches
  determines eligibility; older backfills and GA releases leave it unchanged.

## [2.0.0-beta.3] - 2026-10-01

### Changed

- The run page now updates its status, steps, log and result panels in place
  while a run is active and stops polling once it finishes, instead of
  reloading the whole page every 2 seconds.
- Builder wizard steps now navigate and submit without full page reloads, and
  validation errors re-render in place. The scope step refreshes endpoints and
  features through HTMX, replacing its hand-written fetch code.

### Added

- **Check discovery URL** on the builder discovery step, which fetches and
  shows OpenID discovery metadata inline without saving it.
- Vendored, pinned HTMX 2.0.11 and its `head-support` extension, served from
  the application with no CDN.
- WhiteNoise static file serving, with `collectstatic` run during the Docker
  image build.

## [2.0.0-beta.2] - 2026-09-30

### Changed

- Suppressed successful local `/health/` access-log entries in the Docker
  container while retaining recurring health checks and all other request logs.
- Added a consistent beta notice to every browser UI page to clarify that the
  conformance suite is an MVP beta and features and behaviour may change.
- Removed the standalone runtime-input page from the browser plan builder.
  Security and business configuration now continue directly to the generated
  plan review; canonical plan, REST, and CLI runtime-input support is unchanged.
- Synced the trusted release pipeline with `main`: candidate image scanning
  now gates on Snyk and Docker Scout VEX assessments, and approved promotion
  publishes to Docker Hub instead of GHCR.
- Clarified in the README that certification submissions continue to come from
  the legacy FCS v1 while this release is an evaluation beta, and documented
  how to run the browser UI from a source checkout as an alternative to the
  published Docker image.

### Fixed

- Business-default values edited in the plan builder after importing a test plan
  are no longer discarded. The collapsed "advanced JSON" textarea is pre-filled
  from the imported plan and is resubmitted by the browser even when never
  opened, and it previously overrode the friendly fields — so a participant who
  corrected, for example, a CBPII debtor account could unknowingly certify
  against the original imported value and record a false pass. Friendly fields
  the participant actually changed now take precedence, overlaid on the JSON so
  keys the friendly fields cannot express (such as `secondaryIdentification`)
  are still preserved.
- Docker Scout VEX fetch and candidate scan steps now authenticate via
  `DOCKER_SCOUT_HUB_USER`/`DOCKER_SCOUT_HUB_PASSWORD` using the existing
  organisation credentials; the `dhi.io` registry login alone does not
  authenticate the Scout CLI.
- Candidate-image `dhi.io` login now uses the existing read-only repository
  `DOCKER_ORG_ACCESS_TOKEN` instead of the never-provisioned
  `DOCKER_DHI_PULL_TOKEN`; promotion receives the write-capable token as a
  release Environment secret.
- The `Docker Build` job now detects the hardened Docker contract and, when
  present, logs in to `dhi.io` and runs `scripts/docker_smoke_test.sh` plus
  Compose validation instead of the legacy plain-HTTP health probe, which
  cannot reach the HTTPS-only hardened image.
- Docker Scout credentials are now scoped to the pinned Scout CLI steps only;
  the runtime-base lookup runs repository Python without Docker credentials.
  Documented the read-only Dependabot secrets needed for hardened Dependabot
  PRs to pass `dhi.io` authentication.
- CI now logs out of `dhi.io` immediately after each image build and
  re-authenticates only around the pinned Docker Scout steps, so
  branch-controlled scripts never run while the organisation token is stored
  in the runner's Docker config.
- Docker Hub tag discovery now fails closed on 404 responses, which can indicate
  a private or inaccessible repository rather than a repository that has not
  been created.
- Image promotion no longer fails when the source pull request was merged by
  an administrator without a recorded approval; a merged pull request is still
  mandatory, and an unapproved (bypassed) merge is recorded as a workflow
  warning and job-summary audit entry. The Environment reviewer gate remains
  blocking.
- Image promotion now pushes the candidate OCI archives to Docker Hub by
  digest with `skopeo copy --preserve-digests` instead of `docker load`/`docker
  push`, which re-serialised manifests and published different digests from the
  scanned ones, and no longer leaves `promote-*` staging tags behind.
- Image attestations now use `actions/attest` instead of the deprecated
  `actions/attest-build-provenance`/`actions/attest-sbom` wrappers, use the
  documented `docker.io` subject name, and grant `artifact-metadata: write` so
  the provenance attestation can create its Artifact Metadata storage record.

### Security

- Migrated the pinned Docker Hardened Image builder and distroless runtime
  from Debian 13 to Alpine 3.24, eliminating the unfixed OpenSSL
  CVE-2026-84782 finding. The Alpine runtime ships without system pip, so the
  Debian-specific pip-removal layer is no longer needed.
- Vulnerability scans now continue without VEX suppressions only when Docker
  Scout explicitly reports that the assessed DHI publishes no OpenVEX
  attestations; authentication, signature and all other fetch failures remain
  blocking.
- Docker-signed OpenVEX is verified against Docker's DHI signing key, now
  checked into the repository and pinned by SHA-256 rather than downloaded,
  without a Rekor transparency-log lookup, which DHI VEX attestations do not
  have; previously every hardened-image scan failed at VEX verification.
- A required `Vulnerability Scan` status check now builds and scans the image
  for both platforms on **every pull request** (Docker Scout, Snyk Container
  with application packages, and pip-audit of `uv.lock`). It fails on any
  fixable vulnerability of any severity and on unfixed critical/high findings,
  applies Docker-signed OpenVEX only to DHI base-image packages, and lists
  every finding in the job summary, annotations and code scanning.
- Vulnerability exceptions now live only in
  `security/vulnerability-exceptions.toml`, with owner, distinct approver and
  a maximum 90-day expiry; expired, stale or now-fixable exceptions fail the
  gate. The `.snyk` policy file has been removed (its two entries are covered
  by Docker's signed VEX).
- Promotion re-scans the exact candidate archives with current data before
  the Environment approval gate, and a daily `security-rescan.yml` re-scans
  `main`, `release/*` and published Docker Hub images, tracking one issue per
  vulnerability.
- Dependabot now updates `uv.lock`, Dockerfile base-image digests and GitHub
  Actions.
- Upgraded Django to 6.0.8 (fixes vulnerabilities in 6.0.5) and refreshed
  cryptography 50.0.1, sqlparse 0.6.0 and anyio 4.15.1.
- Candidate image scans and trusted promotion now require the runtime base to
  match the exact DHI digest covered by the signed VEX assessment.
- Bumped vulnerable Python dependencies so the candidate container image carries
  no known HIGH or CRITICAL advisories:
  - `anyio` 4.13.0 → 4.15.1 (CVE-2026-63374, CRITICAL) — transitive via `httpx`
    and `uvicorn[standard]`'s `watchfiles`.
  - `cryptography` 48.0.0 → 50.0.1 (three HIGH advisories) — direct dependency;
    the `pyproject.toml` floor is raised to `>=50.0.0` so the fixed version
    cannot be resolved away. Used for FAPI signing, JWS/JWKS, and mTLS
    certificate handling; no API breakage across the major bump.
  - `sqlparse` 0.5.5 → 0.6.0 (three HIGH advisories) — transitive via `django`.

## [2.0.0-beta.1] - 2026-09-28

First public beta of the MVP: the Open Banking UK Read/Write v3.1.11 and v4.0.x
catalogue-backed conformance engine (AIS, PIS, CBPII, VRP) and DCR 3.4 support,
the canonical JSON-first test-plan schema and guided browser builder, the
hardened non-root Docker distribution, and the trusted candidate-image build
and preview/beta/GA promotion pipeline, as detailed below.

### Added


- Browser wizard and configuration support for supplying PEM credentials as
  pasted text or an uploaded file, in addition to an absolute file path. Every
  credential (FAPI signing certificate and private key, TLS CA bundle, mTLS
  client certificate and private key, DCR software statement assertion, and DCR
  signing certificate) now accepts an inline sibling key in canonical plans and
  model-bank config (`signingCertificatePem`, `signingPrivateKeyPem`,
  `caBundlePem`, `clientCertificatePem`, `clientPrivateKeyPem`, `certificatePem`,
  `privateKeyPem`, and `softwareStatementAssertion`). A credential must be
  supplied exactly once, as either a path or inline material. Inline material is
  held in memory, is redacted from logs, results, and safe plan exports, and is
  never re-rendered into the wizard once stored. The read-only `/certs` container
  mount is therefore now optional for participants who paste credentials.

- First-class Open Banking UK Read/Write v3.1.11 support for AIS, PIS, CBPII,
  and VRP, with dedicated v3.1 catalogue boundaries, pinned v3.1.11 OpenAPI
  schemas, explicit request-signing metadata, and a machine-checkable strict
  parity contract against legacy FCS v1.10.0, including distinct execution of
  every AIS query variant without v2-only capability omissions.
- Catalogue-backed shared plan-document model for Open Banking conformance runs, including catalogue keys, v2 scheme/specification/version boundaries, security-profile applicability, implemented endpoints, runtime input requirements, assertion override tracking, compiled execution graphs, and traceability metadata.
- Legacy FCS-derived bundled catalogues for AIS/accounts-transactions, PIS/payments, CBPII, and VRP under `conformance/catalogues/`, with registry coverage through `conformance.catalogue_registry`.
- CLI, REST API, and browser builder support for canonical schemaVersion `1.0` test-plan execution.
- Browser implemented-endpoint selection grouped by resource area, inline required/optional capability selectors, endpoint/capability-derived runtime prompts, launch integration, and a read-only generated-plan preview with collapsed low-level audit details.
- Multi-step browser test-plan builder flow with session-backed drafts, grouped execution config, JSON-first plan import, generated review summaries, safe/secret export actions, and launch from the reviewed plan.
- Canonical JSON-first test-plan schema `1.0` with `specification`, single `securityEnvironment`, `resourceGroups`, `businessTestData`, `metadata`, and certification/development `executionMode`.
- Narrow Open Banking specification registry and family-discriminated canonical plan scope for Read/Write and Dynamic Client Registration 3.4, including mandatory locked registration POST and optional direct management endpoints.
- Executable Open Banking DCR 3.4 catalogue metadata for the pinned 10-scenario, 34-case, 79-step parity inventory, including endpoint gates, dependencies, state flow, locked assertions, runtime requirements, and legacy/normative traceability.
- Typed DCR 3.4 runtime primitives for strict discovery/JWKS validation, compact PS256 registration JOSE, certificate subject-DN derivation, mTLS, four executable token authentication methods, scenario-local state, dependency skips, cleanup, and masked shared result/log evidence.
- First-class DCR 3.4 CLI, local REST, browser import/builder/review/launch, persisted run-detail, structured scenario/case/step result, certification, and opt-in Ozone verification-gate integration.
- Shared pre-run validation evidence and secret-safe test-plan snapshots embedded in JSON run results for canonical plan launches.
- Structured v2 config sections for resource-server headers, OAuth defaults, client credentials, Open Banking signature metadata, AIS/PIS/CBPII business defaults, and conditional properties.
- Result JSON catalogue traceability, runtime input snapshots with sensitive values omitted, certification/non-certification reasons, and per-step catalogue evidence.
- Run-detail catalogue evidence summary for selected endpoint/capability counts, generated test-case counts, catalogue version, and non-certifying reasons.
- Hand-maintained legacy mapping documentation in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.
- Regression coverage for bundled catalogue registration, legacy FCS provenance, restored guided builder capability selection, safe v2 plan-document export, API/CLI capability parity, run-detail catalogue evidence, compiled-plan execution traceability, and result JSON omission of legacy suite metadata.
- `scripts/release_metadata.py` and its CI-facing `scripts/validate_release.py` CLI, providing strict raw-version parsing, preview/beta/GA channel classification, branch/tag compatibility checks, version-increase and duplicate-publish guards, and a promotion-manifest builder for the Docker release process.
- `.github/dependabot.yml`, scheduling weekly update PRs for the pinned Docker base image digests, Python (`uv`) dependencies, and GitHub Actions, each gated by the full check suite, hardened runtime smoke test, and vulnerability scan.
- `scripts/build_promotion_manifest.py`, a CI-facing CLI that validates branch/channel compatibility and writes the candidate promotion manifest (source SHA, raw version/channel, per-platform image digests, OCI labels) as JSON.
- `scripts/docker_smoke_test.sh`, the canonical hardened-profile smoke test run against every built candidate image: non-root UID/GID 65532, no shell, immutable root filesystem, `/health/` and `/` endpoints, secret-key persistence across restart, writable `/data/sessions`/`/data/results`/`/data/logs`, a readable-but-non-writable `/certs` mount, and a working CLI command override.
- `.github/workflows/ci.yml` gained a `plan` job that classifies every run by target branch and fork origin, a `candidate-image` job matrix building native `linux/amd64` and `linux/arm64` images for `main`, `release/**`, and `preview/**` branches, running the full hardened smoke test and a zero-exception Snyk container scan, generating an SPDX SBOM, and uploading the built image plus its metadata as a workflow artifact without publishing anywhere; a `candidate-manifest` job that assembles and uploads the checksummed promotion manifest from those artifacts; and a `reject-fork-candidate` job that fails fork-originated pull requests targeting a candidate-bearing branch with an actionable message.
- `scripts/validate_promotion.py`, a CI-facing CLI that revalidates a candidate promotion manifest against the exact source SHA, branch, tag (GA), platform set, and previously published version tags before publication, printing the manifest's expected image tags for the promotion workflow to consume.
- Dedicated preview/beta/GA promotion workflows: `.github/workflows/_promote-image.yml` (a shared, `workflow_call`-only implementation) locates the successful candidate-image CI run for a given commit, downloads its already-scanned platform image artifacts and promotion manifest, verifies their checksums, cross-checks the exact commit's own `pyproject.toml` version against the manifest, requires the commit belong to a merged pull request with at least one recorded approval, refuses to republish an already-published version tag, republishes the exact already-scanned platform digests as a multi-arch manifest list via `docker buildx imagetools create` (never rebuilding), attaches build-provenance and SBOM attestations, and removes its temporary per-run staging tags; `.github/workflows/promote-preview.yml`, `promote-beta.yml`, and `promote-ga.yml` trigger it via `workflow_dispatch` behind the `preview-release`, `beta-release`, and `ga-release` GitHub Environments respectively, each validating its own branch pattern first. GA promotion additionally opens (but never auto-merges) a pull request merging `main` back into `develop`. Docker Hub is never configured as a target.

### Changed

- Dockerfile rewritten as a hardened, digest-pinned multi-stage build on Docker Hardened Images (`dhi.io/python`, Debian 13), running as non-root UID/GID 65532 with no shell or package manager in the runtime stage. `docker/entrypoint.py` prepares the `/data` volume, generates and persists a mode-0600 Django secret key, and supplies safe host/session-path defaults before exec'ing into the application; `docker/healthcheck.py` provides an exec-form container health check. `make docker` now runs the image under the full hardened profile (read-only root filesystem, all capabilities dropped, `no-new-privileges`, tmpfs `/tmp`, named `/data` volume) without requiring manually supplied secrets.
- `docs/CICD_STRATEGY.md` corrected to match the implemented candidate/promotion pipeline: the version-tag format is `X.Y.Z-dev.N` (preview) / `X.Y.Z-beta.N` (beta) / `X.Y.Z` (GA) rather than PEP 440 `.devN`/`bN`/`rcN` suffixes, there is no release-candidate (RC) channel, GA publishes only the exact version plus `latest` (no `X.Y`/`X` aliases), the workflow-file references now name the actual `ci.yml` jobs and `_promote-image.yml`/`promote-preview.yml`/`promote-beta.yml`/`promote-ga.yml` files instead of the never-implemented `image-candidate.yml`/`image-promote.yml`, the hotfix process is rewritten to describe shipping an expedited `release/X.Y.Z` patch cut from `main` through the normal beta/GA gate (there is no separate automated hotfix-tag publish path), and the required-secrets table now lists `DOCKER_ORG_USERNAME`/`DOCKER_ORG_ACCESS_TOKEN`. `docs/settings/ACTIONS_GENERAL.md` now allowlists `anchore/sbom-action` and documents that "Allow Actions to create and approve PRs" must be enabled solely so `promote-ga.yml` can open its `main`-to-`develop` merge-back pull request. `.github/PULL_REQUEST_TEMPLATE.md` and `docs/settings/GENERAL.md` no longer reference the RC channel.
- `docs/DOCKER_GUIDE.md`, `compose.yaml`, and `compose.certs.yaml` document and provide the canonical hardened `docker run`/Compose launch commands, GHCR pull instructions, the `/data` persistence layout, and the optional read-only `/certs` mount contract; `README.md` and `docs/DEVELOPER_GUIDE.md` now point participants at the Docker image as the primary supported way to run the suite.

- CI/CD strategy now documents the stable-`main`, `develop`, feature-preview,
  release-branch, and digest-promotion model for participant-facing Docker
  preview, beta, and GA images, including the human approval points and
  source-controlled image tag metadata.
- `.github/workflows/ci.yml`'s `docker-build` job now authenticates to `dhi.io` before building (Docker Hardened Images require registry login even to pull), no longer sets a hardcoded `DJANGO_SECRET_KEY`/`DJANGO_ALLOWED_HOSTS` for its smoke test since the entrypoint now generates and persists these itself, and additionally validates both `compose.yaml` and the `compose.certs.yaml` override.
- Pull request CI now invokes the canonical `make check` gate with a full
  tracked-file secret scan while Docker image build, startup, and `/health/`
  validation run independently in parallel. The duplicated lint/test command
  definitions and CI-only coverage/test-result artifacts have been removed.
- Local `make secrets` and `make check` skip only versioned OpenAPI reference snapshots, cutting repeated entropy-scanning overhead while staged-file and CI secret scans continue to inspect those files.
- The supported test suite is now offline-only and split into exactly two pytest categories, `unit` and `component`. Every collected test must declare exactly one of them; `tests/conftest.py` fails collection with the offending node ids otherwise. `make test` runs `-m "unit or component"` with aggregate coverage, and `make unit`/`make component` give focused iteration without coverage. CI runs the same selection through `make check`.
- Offline execution is now enforced rather than assumed: `tests/conftest.py` installs a session-wide socket guard that raises `ExternalNetworkAccessError` for any connection to a non-loopback address, naming the address that was attempted. Loopback TLS/mTLS transport fixtures are unaffected.
- Quality tooling is narrowed to Ruff (lint + format), mypy strict, and pytest with coverage. Ruff's rule selection is focused on defect-finding families — `E4`/`E7`/`E9`, `F`, `I`, `S`, `B`, `A`, `DTZ`, `T20`, and docstring *presence* (`D100`–`D104`) — and drops the preference-heavy `N`, `UP`, `C4`, `PT`, `SIM`, `PTH`, and broad `D` style rules. `.github/copilot-instructions.md` is rewritten as a material-risk-only review rubric that no longer duplicates mechanically enforced findings.
- Offline test lifecycle costs are removed. API tests that launch a run now own and deterministically join their background worker instead of every API test paying a fixed one-second best-effort settle in teardown, and the singleton run/auth-session resets are scoped to the tests that actually reach those singletons. DCR protocol and orchestration behaviour now runs in process through `httpx.MockTransport` at the product's own client boundary; the loopback mTLS listener is retained only for transport, TLS, mTLS, certificate, and real client-configuration behaviour, and its immutable certificate and JOSE material is generated once per session.
- Test modules are now organised by category and behavioural domain under `tests/unit/` (`api`, `catalogue`, `execution`, `security`, `validation`) and `tests/component/` (`api`, `django`, `execution`, `protocol`), with shared fakes, fixtures, and builders in `tests/support/`. No test file sits directly in `tests/`, each module declares its category once through a module-level `pytestmark`, modules that mixed both categories were split, and fixtures moved from the root conftest to the narrowest package that consumes them. The root conftest now owns category enforcement only. Behaviour is unchanged: the same 1,250 tests run, with new node ids.
- Oversized test modules are decomposed by subject and repetitive manifest-parser cases are expressed as typed parameter matrices with descriptive case ids. The 5,662-line executor module is split into eleven modules covering HTTP dispatch, step dependencies, token-endpoint auth, detached JWS, response signatures, evidence masking, run orchestration, compiled payment/account plans, and PSU manual/headless flows; the 3,185-line manifest module is split into seven modules by document area; and the run-endpoint, builder-UI, DCR-execution, catalogue, builder-wizard, and model-bank-config modules are split along their responsibility boundaries. Signing material, PSU step builders, and manifest documents move to `tests/support/`. No production behaviour changed and no guarantee was dropped: `docs/TESTING_STRATEGY.md` maps every retired test name to the retained module or matrix case id. The suite runs 1,247 tests — three retired manifest cases assert defaults that a single retained case now asserts together.
- Browser test-plan security profiles are now derived from the selected specification version; Read/Write 4.0.x resolves to FAPI 1 Advanced and DCR 3.4 remains profile-neutral.
- Browser navigation now removes the redundant home-page health action and returns participants to the home page from finished run pages.
- Participant-facing execution now compiles endpoint selections into catalogue plans and reuses the hardened HTTP, masking, signing, PSU authorisation, logging, and result-evidence execution path.
- CBPII catalogue coverage now executes the distinct legacy invalid-account and expirationDateTime variants from the 3.1.11, 4.0.0, and 4.0.1 FCS manifests instead of grouping them into aggregated cases.
- CBPII consent expiry and invalid-consent requests now replay the legacy next-day UTC macros and literal `42` identifier instead of fixed or generated substitutes.
- PIS, AIS, and VRP catalogue coverage now has explicit parity guards for all legacy v3.1 and v4.0 FCS manifest scripts, with AIS expanded across the remaining accounts-and-transactions resource families.
- Public documentation now describes canonical JSON-first test plans, grouped config plus endpoint/capability execution, and the guided builder workflow instead of checked-in examples, config-selected suites, public manifest authoring, `planSpec`, or generated-test selection.
- Browser import/export now accepts and emits schemaVersion `1.0` JSON-first test plans only.
- Browser wizard sessions now default to server-side file storage so local builder drafts work without running SQLite migrations first.
- Browser config prompts now derive exact runtime values such as resource base URL, consented AIS account id, transaction filters, and CBPII debtor account fields from structured config defaults instead of duplicating them as manual endpoint prompts.
- Browser discovery now treats JWKS as automatic security metadata rather than a participant-facing follow-up choice, removes participant-configurable HTTP and PSU authorisation timeouts, and drives response-signature validation from catalogue coverage.
- Environment labels are removed from new builder plans, runtime config, execution logs, and result JSON because they are metadata-only and not part of FCS conformance behaviour.
- AIS accounts-and-transactions catalogue execution now creates separate legacy basic and detail permission consents/tokens, so full AIS scope exercises both PSU-authorised permission profiles instead of one broad all-permissions consent.
- PIS catalogue coverage now exposes the legacy missing-signature-claim, scheduled-payment datetime format, and v3.1 no-`x-fapi-financial-id` consent behaviours as distinct generated tests.
- VRP catalogue coverage now exposes the legacy v3.1 pre/post-3.1.11 consent and payment body variants as distinct generated tests, with v4 VRP and cVRP retaining separate executable provenance.

### Fixed

- Docker browser runs now terminate local HTTPS and accept the legacy
  `0.0.0.0` host, allowing PSU authorization redirects registered as
  `https://0.0.0.0:8443/conformancesuite/callback` to reach the callback while
  the host port remains published only on `127.0.0.1`. The generated local
  certificate is persisted in the `/data` volume and no private key is baked
  into the image. Browser/API result and execution-log artifacts are also
  written under the writable `/data` volume rather than the read-only `/app`
  filesystem.

- PIS v4 strict parity now executes all 29 legacy FCS v1.10.0 rows as
  independent cases with exact `asserts`/`asserts_one_of` error-code checks,
  response schemas and signature flags, including separate domestic consent
  and scheduled-payment flows, fixed identifiers and UTC-midnight date macros,
  and the original invalid standing-order request bodies.
- PSU authorisation popups now target 900x900 pixels and shrink, center, and remain within the current screen's usable area.
- cVRP is no longer exposed through the bundled Open Banking catalogue registry, Open Banking UK Read/Write v2 builder, or aggregate compiler boundary.
- VRP nested funds-confirmation operations now stay grouped under their parent domestic VRP consent resource group instead of appearing as a separate funds-confirmation resource group.
- AIS resource runs no longer fail status-only negative cases on non-JSON error bodies, correctly resolve JSON assertion paths through arrays, and generate invalid account identifiers for legacy account-scoped negative cases.
- AIS legacy negative cases now preserve one-of status expectations such as HTTP 400 or 403, and the legacy FCS Product playback typo `/product` is canonicalised to `/products`.
- AIS basic-permission checks now assert detail-only account, beneficiary, and transaction fields are absent, matching the previous FCS permission-filtering assertions.
- PIS endpoint selections in the browser plan builder now show Payment Initiation business inputs and validate only the selected product-family defaults they need, with JSON fallbacks accepted for grouped account, amount, and standing-order frequency values.
- PIS catalogue execution now sends spec-shaped payment-initiation JSON bodies, applies detached JWS signing to payment write requests, and inserts PSU authorisation steps before authorised consent/payment follow-ups.
- PIS v4 payment write requests now use the Open Banking v3.1.4+ detached-JWS profile, and v4 response-signature validation no longer rejects valid encoded-payload signatures for missing `b64=false`.
- PIS v4 domestic consent status assertions now use `Data.Status` with v4 status codes, and downstream PIS payment calls now use per-consent PSU-authorised payment tokens instead of the initial client-credentials token.
- PIS payment consent creation now generates fresh instruction identifiers for each run, and PIS consent/payment status reads use the client-credentials payments token while authorised submissions use the matching PSU token.
- PIS v3.1.11 strict parity now executes both legacy domestic-consent rows as
  distinct requests, preserves their different PSU-authorisation behaviour,
  restores fixed end-to-end identifiers and consent-value reuse, and renders
  scheduled-payment `nextDayDate` variants at next-day UTC midnight.
- PIS standing-order legacy schema-check cases now compile with bundled Payment Initiation OpenAPI metadata instead of failing at run launch.
- VRP catalogue execution now sends legacy-shaped domestic VRP/cVRP JSON bodies to versioned Open Banking PISP resource paths, applies detached JWS signing to write requests, generates fresh payment identifiers, and inserts consent-specific PSU authorisation before authorised payment/funds-confirmation calls.
- VRP Read/Write v4.0, v4.0.0, and v4.0.1 plans now honour the selected specification version and no longer execute legacy v3.1 pre/post-3.1.11 consent or payment variants.
- VRP v4 funds-confirmation and repeat consent-deletion cases now restore legacy FCS `asserts_one_of` status-code checks while preserving the single PSU authorisation flow from the old v4 manifest.
- AIS and PIS Read/Write v4.0, v4.0.0, and v4.0.1 plans now filter out legacy v3-only executable variants while retaining their provenance for v3 compatibility.
- AIS, PIS, CBPII, and VRP v4 catalogue cases now emit bundled OpenAPI response-schema assertions for legacy JSON-response scripts that had `schemaCheck: true`.
- CBPII v4 executable assertions now restore missing legacy FAPI interaction and JSON content-type header checks on read, funds-confirmation, and delete flows.
- Business-data requirement badges now render on a consistent line beneath field labels, keeping inputs aligned across AIS, PIS, and CBPII sections even when labels wrap.
- DCR run snapshots now include every selected catalogue execution step, hierarchical reports carry exact runtime statuses, and approved-release policies reach DCR certification eligibility.
- DCR registration requests now derive callback URLs from the Open Banking SSA `software_redirect_uris` claim when no explicit redirect override is configured.
- DCR plans now require an explicit 1 to 18 character Base62 ASPSP `registrationAudience` in every execution mode; discovery-issuer audience compatibility has been removed.

### Removed

- Removed the `integration`, `ozone`, and `e2e` pytest markers, the live-network `make integration` target, the empty `tests/integration/` package, and the orphaned `tests/fixtures/e2e-default.yaml` placeholder. Former integration coverage is reclassified as `component`.
- Removed the unsupported `.github/workflows/ozone-integration.yml` and placeholder `.github/workflows/e2e.yml` workflows. `.github/workflows/ci.yml` remains the single pipeline: the canonical `make check` gate and a parallel Docker build that starts the container and probes `/health/`.
- Removed the `interrogate` and `pydoclint` dev dependencies together with their `pyproject.toml` configuration, their `make lint` commands, their CI steps, and `.github/instructions/docstrings.instructions.md`. Docstring *presence* on modules, packages, public classes, functions, and methods is still enforced by Ruff `D100`–`D104`; mandatory Google-style `Args`/`Returns` sections and universal private-helper docstrings are no longer required.
- Removed checked-in public example payloads from `config/`.
- Removed legacy bundled suite JSON resources and their resolver.
- Removed public config-selected suite support (`config.testSuite`) and legacy suite metadata from result serialization.
- Removed public participant manifest and plan-spec execution support from CLI/API/browser surfaces, including public `--manifest`, `--deselect`, `--plan-spec`, REST `manifest`, REST `planSpec`, and REST `deselectStepIds`.
- Removed the legacy single-page `/plan/` browser builder.
- Removed participant-configurable OAuth intent ID, ACR supported-values metadata, and certificate path roots from test-plan security config; certificate, key, and CA-bundle fields now use direct absolute file paths.
- Removed stale suite-catalog tests and replaced obsolete skipped executor suite coverage with active compiled-plan traceability coverage.

### Security

- Exported plan documents and result traceability avoid inline secret material; sensitive runtime inputs are recorded as provided without serializing their values.
- Browser safe exports for v2 plan documents preserve reusable structure while emptying secret-bearing runtime/config strings by default.
- Existing masking continues to cover credentials, tokens, request objects, client assertions, detached JWS values, authorization codes, and sensitive headers across result JSON, NDJSON logs, API log snapshots, and browser downloads.
- Internal manifest execution remains available only as implementation plumbing for compiled catalogue execution and certification validation; it is no longer exposed as a participant-facing run contract.

[Unreleased]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.7...HEAD
[2.0.0-beta.7]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.6...v2.0.0-beta.7
[2.0.0-beta.6]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.5...v2.0.0-beta.6
[2.0.0-beta.5]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.4...v2.0.0-beta.5
[2.0.0-beta.4]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.3...v2.0.0-beta.4
[2.0.0-beta.3]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.2...v2.0.0-beta.3
[2.0.0-beta.2]: https://github.com/OpenBankingUK/conformance-suite-v2/compare/v2.0.0-beta.1...v2.0.0-beta.2
[2.0.0-beta.1]: https://github.com/OpenBankingUK/conformance-suite-v2/releases/tag/v2.0.0-beta.1
