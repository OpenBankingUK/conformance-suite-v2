# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Security

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
