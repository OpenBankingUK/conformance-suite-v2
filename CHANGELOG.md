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
- Shared pre-run validation evidence and secret-safe test-plan snapshots embedded in JSON run results for canonical plan launches.
- Structured v2 config sections for resource-server headers, OAuth defaults, client credentials, Open Banking signature metadata, AIS/PIS/CBPII business defaults, and conditional properties.
- Result JSON catalogue traceability, runtime input snapshots with sensitive values omitted, certification/non-certification reasons, and per-step catalogue evidence.
- Run-detail catalogue evidence summary for selected endpoint/capability counts, generated test-case counts, catalogue version, and non-certifying reasons.
- Hand-maintained legacy mapping documentation in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.
- Regression coverage for bundled catalogue registration, legacy FCS provenance, restored guided builder capability selection, safe v2 plan-document export, API/CLI capability parity, run-detail catalogue evidence, compiled-plan execution traceability, and result JSON omission of legacy suite metadata.

### Changed

- Participant-facing execution now compiles endpoint selections into catalogue plans and reuses the hardened HTTP, masking, signing, PSU authorisation, logging, and result-evidence execution path.
- Public documentation now describes canonical JSON-first test plans, grouped config plus endpoint/capability execution, and the guided builder workflow instead of checked-in examples, config-selected suites, public manifest authoring, `planSpec`, or generated-test selection.
- Browser import/export now accepts and emits schemaVersion `1.0` JSON-first test plans only.
- Browser wizard sessions now default to server-side file storage so local builder drafts work without running SQLite migrations first.
- Browser config prompts now derive exact runtime values such as resource base URL, consented AIS account id, transaction filters, and CBPII debtor account fields from structured config defaults instead of duplicating them as manual endpoint prompts.
- Browser discovery now treats JWKS as automatic security metadata rather than a participant-facing follow-up choice, removes participant-configurable HTTP and PSU authorisation timeouts, and drives response-signature validation from catalogue coverage.
- Environment labels are removed from new builder plans, runtime config, execution logs, and result JSON because they are metadata-only and not part of FCS conformance behaviour.
- The E2E workflow placeholder config path moved from `config/` to `tests/fixtures/` so the Django config package no longer ships participant-facing examples.

### Fixed

- cVRP is no longer exposed through the bundled Open Banking catalogue registry, Open Banking UK Read/Write v2 builder, or aggregate compiler boundary.
- VRP nested funds-confirmation operations now stay grouped under their parent domestic VRP consent resource group instead of appearing as a separate funds-confirmation resource group.

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
