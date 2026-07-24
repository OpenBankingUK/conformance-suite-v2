# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Catalogue-backed plan-spec model for Open Banking conformance runs, including catalogue keys, security-profile applicability, implemented endpoints, runtime input requirements, assertion override tracking, compiled execution graphs, and traceability metadata.
- Legacy FCS-derived bundled catalogues for AIS/accounts-transactions, PIS/payments, CBPII, VRP, and cVRP under `conformance/catalogues/`, with registry coverage through `conformance.catalogue_registry`.
- CLI, REST API, and browser plan-builder support for compiled catalogue plan execution via `--plan-spec` and `planSpec`.
- Browser implemented-endpoint selection grouped by resource area, endpoint-derived runtime prompts, compiled-plan preview counts, launch integration, and hidden-by-default generated-test audit details.
- Result JSON catalogue traceability, runtime input snapshots with sensitive values omitted, certification/non-certification reasons, and per-step catalogue evidence.
- Hand-maintained legacy mapping documentation in `docs/FCS_LEGACY_BENCHMARK_MAPPING.md`.
- Regression coverage for bundled catalogue registration, legacy FCS provenance, removed public example payloads, compiled-plan execution traceability, and result JSON omission of legacy suite metadata.

### Changed

- Participant-facing execution now compiles endpoint selections into catalogue plans and reuses the hardened HTTP, masking, signing, PSU authorisation, logging, and result-evidence execution path.
- Public documentation now describes config plus plan-spec execution and the browser implemented-endpoint workflow instead of checked-in examples, config-selected suites, or public manifest authoring.
- The E2E workflow placeholder config path moved from `config/` to `tests/fixtures/` so the Django config package no longer ships participant-facing examples.

### Removed

- Removed checked-in public example payloads from `config/`.
- Removed legacy bundled suite JSON resources and their resolver.
- Removed public config-selected suite support (`config.testSuite`) and legacy suite metadata from result serialization.
- Removed public participant manifest execution support from CLI/API/browser surfaces, including public `--manifest`, `--deselect`, REST `manifest`, and REST `deselectStepIds`.
- Removed stale suite-catalog tests and replaced obsolete skipped executor suite coverage with active compiled-plan traceability coverage.

### Security

- Exported plan specs and result traceability avoid inline secret material; sensitive runtime inputs are recorded as provided without serializing their values.
- Existing masking continues to cover credentials, tokens, request objects, client assertions, detached JWS values, authorization codes, and sensitive headers across result JSON, NDJSON logs, API log snapshots, and browser downloads.
- Internal manifest execution remains available only as implementation plumbing for compiled catalogue execution and certification validation; it is no longer exposed as a participant-facing run contract.
