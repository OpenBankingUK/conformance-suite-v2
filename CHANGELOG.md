# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

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

### Changed

- Browser navigation now removes the redundant home-page health action and returns participants to the home page from finished run pages.
- Participant-facing execution now compiles endpoint selections into catalogue plans and reuses the hardened HTTP, masking, signing, PSU authorisation, logging, and result-evidence execution path.
- CBPII catalogue coverage now executes the distinct legacy invalid-account and expirationDateTime variants from the 3.1.11, 4.0.0, and 4.0.1 FCS manifests instead of grouping them into aggregated cases.
- PIS, AIS, and VRP catalogue coverage now has explicit parity guards for all legacy v3.1 and v4.0 FCS manifest scripts, with AIS expanded across the remaining accounts-and-transactions resource families.
- Public documentation now describes canonical JSON-first test plans, grouped config plus endpoint/capability execution, and the guided builder workflow instead of checked-in examples, config-selected suites, public manifest authoring, `planSpec`, or generated-test selection.
- Browser import/export now accepts and emits schemaVersion `1.0` JSON-first test plans only.
- Browser wizard sessions now default to server-side file storage so local builder drafts work without running SQLite migrations first.
- Browser config prompts now derive exact runtime values such as resource base URL, consented AIS account id, transaction filters, and CBPII debtor account fields from structured config defaults instead of duplicating them as manual endpoint prompts.
- Browser discovery now treats JWKS as automatic security metadata rather than a participant-facing follow-up choice, removes participant-configurable HTTP and PSU authorisation timeouts, and drives response-signature validation from catalogue coverage.
- Environment labels are removed from new builder plans, runtime config, execution logs, and result JSON because they are metadata-only and not part of FCS conformance behaviour.
- The E2E workflow placeholder config path moved from `config/` to `tests/fixtures/` so the Django config package no longer ships participant-facing examples.
- AIS accounts-and-transactions catalogue execution now creates separate legacy basic and detail permission consents/tokens, so full AIS scope exercises both PSU-authorised permission profiles instead of one broad all-permissions consent.
- PIS catalogue coverage now exposes the legacy missing-signature-claim, scheduled-payment datetime format, and v3.1 no-`x-fapi-financial-id` consent behaviours as distinct generated tests.
- VRP catalogue coverage now exposes the legacy v3.1 pre/post-3.1.11 consent and payment body variants as distinct generated tests, with v4 VRP and cVRP retaining separate executable provenance.

### Fixed

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
