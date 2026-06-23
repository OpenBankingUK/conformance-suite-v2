# FCS v2 PRD v4 Draft

> Status: Draft
> Planning home: `docs/requirements/`
> First certifiable target: Open Banking Read/Write v4.0.1 AIS

## 1. Product goal

FCS v2 should become a secure, maintainable, declarative conformance platform that can certify Open Banking UK implementations through a local Phase 1 Docker-distributed tool, while preserving architecture compatibility with a future Phase 2 hosted portal.

The immediate objective is no longer "start the rebuild". The platform already exists. The objective is now to turn the current partial-coverage rails into a certifiable, auditable, AI-agent-maintainable product.

## 2. Current implementation baseline

The repository currently includes:

- Python 3.14+, Django 6, Django templates/HTMX, strict typing, ruff, docstring checks, pytest coverage gates, and Makefile local validation.
- CLI, loopback-guarded REST API, and browser plan-builder/run-monitoring surfaces.
- JSON participant config with TLS, OAuth, FAPI signing, approved-release policy, suite selection, and output path validation.
- v0/v1 manifest parsing, HTTP steps, PSU authorisation steps, generic assertions, response-schema assertions, form bodies, setup/execution groups, FAPI signing directives, detached JWS, token-endpoint auth policy, mandatory/optional metadata, and `certificationCoverage`.
- First-class test plans with default mandatory/non-optional selection, optional opt-in rows, and certification-impacting mandatory deselection.
- Schema-versioned participant-owned Run Plans with manifest-hash drift detection, import/export, custom test values, and exploratory-run gating.
- Group-aware execution, masked request/response evidence, structured NDJSON execution logs, and deterministic result ordering.
- Result JSON with metadata, tool version, suite metadata, plan summary, certification eligibility, approved-release self-assessment, and partial/complete coverage state.
- OBL-side certification validator CLI that recomputes mandatory coverage and approved-release checks from independent inputs.
- Bundled partial suites for v3.1.11, v4.0, and v4.0.1 rails, including AIS baseline/slice entries, v4 AIS legacy benchmark, and starter rails for AIS/PIS/CBPII/VRP.

## 3. Guiding principles

- Security, masking, auditability, deterministic behaviour, and reproducibility are non-negotiable.
- Previous FCS test coverage is the minimum parity baseline, not the ceiling.
- Certification claims must remain conservative: `partial` until coverage, validator behaviour, documentation, and Standards sign-off are complete.
- The engine should stay domain-agnostic. Domain policy belongs in manifests, bundled standards assets, suite coverage ledgers, and docs.
- The SWE is now primarily the AI agent manager. Requirements must be explicit enough for agents to implement safely without tacit project memory.
- Phase 1 is local and single-active-run; Phase 2 compatibility must be preserved without overbuilding portal features now.

## 4. Users and delivery actors

| Actor | Need |
| --- | --- |
| Participant / ASPSP | Configure the tool, select relevant tests, run conformance, debug failures, and produce shareable masked certification artefacts. |
| OBL Standards team | Define coverage, mandatory/optional/conditional rules, waivers, assertions, and suite promotion readiness without hardcoding domain rules in the engine. |
| OBL Certification and Monitoring team | Validate submitted reports against trusted manifests and approved-release policy with a repeatable, auditable process. |
| OBL Engineering / AI agent manager | Convert requirements into bounded agent-ready slices, review outputs, preserve security, and keep repo artefacts aligned. |
| AI development agents | Execute small well-specified implementation slices with clear acceptance criteria, validation commands, and constraints. |

## 5. Phase 1 scope

Phase 1 remains a local conformance tool distributed as a hardened Docker image. It must support CLI, local API, and browser-guided flows for configuring, selecting, running, and reviewing conformance tests.

Phase 1 must prioritise:

1. v4.0.1 AIS certification readiness.
2. Prior-FCS parity inventory and implementation.
3. Machine-readable requirements and coverage ledgers.
4. Visual, guided test-plan selection.
5. Certification-safe handling of custom test values.
6. Multi-auth and auth-method selection.
7. Release readiness: Docker hardening, image signing policy, approved-release policy, docs, and validation gates.

## 6. Phase 2 constraints

Phase 2 portal work is not the active implementation target for this PRD, but Phase 1 choices must not block:

- persistent run history and certification records
- authenticated participant identity and ASPSP representation
- multiple concurrent runs
- worker/process-separated callback coordination
- hosted result submission, OBL review, approval, and certificate publication
- authenticated API access after the identity model is defined

## 7. Requirements

### 7.1 Prior-FCS parity and coverage

- The first certifiable suite must prove at least prior-FCS parity for its target area.
- Previous FCS scripts, assertions, permissions, content variants, negative flows, and known gaps must be captured in a machine-readable ledger.
- Each prior-FCS item must be classified as implemented, partially implemented, requiring a new primitive, blocked, waived, legacy issue, superseded, or not started.
- Waivers and legacy issues must be explicit and traceable to Standards sign-off.
- The v4.0.1 AIS target must bridge from the existing v4 AIS legacy benchmark rather than assuming v4.0 parity automatically applies.

### 7.2 v4.0.1 AIS certification readiness

- v4.0.1 AIS is the first certifiable vertical slice.
- Mandatory matrix ownership sits with Standards; Engineering owns manifest implementation, engine primitives, validation, and docs.
- The suite remains `partial` until every mandatory coverage item is mapped, implemented or waived, tested, documented, and validated by the OBL-side certification validator.
- Certification promotion must include a deliberate manifest change to `certificationCoverage: complete`.

### 7.3 Visual plan builder and guided config

- The UI should show test selection as a tree: standard, version, API family, resource group, endpoint, permission/content variant, auth bundle, and step.
- Participants should be able to select or deselect branches and individual steps.
- Mandatory, optional, conditional, and certification-impacting deselection must be obvious before launch.
- The guided config builder should minimise free-text JSON and use structured presets for common environments, suites, API families, and auth methods.
- Custom environments must remain possible, but compatibility errors must be clear.

### 7.4 Custom test values

- [Implemented] Suite manifests own `testValues.baseline`, `generatedKeys`, and `allowedCustomKeys`; participant inputs provide `testData.values`; the compiled `RunConfiguration` is the execution artifact.
- [Implemented] `RunConfigurationCompiler` normalises same-as-baseline values away, preserves only effective baseline deltas, and reports `missing_required_keys` when neither the suite baseline nor participant data supplies a required key.
- [Implemented] Certification uses two independent gates: coverage (mandatory steps present/passed) and value purity (`baselineDeltaKeys` must be empty for certifiable runs).
- [Implemented] Run-plan and result evidence expose baseline-delta impact only for genuine deltas; the UI shows the “Test Data Customisation” panel and “N custom value reference(s)” badges only when deltas exist.
- [Implemented] Result JSON and execution logs record affected requirement/step, baseline-delta keys, custom value impact, and masked value evidence where sensitive.
- [Implemented] The Run Plan remains participant-owned, while participant configuration continues to hold environment and credential inputs; launch is blocked when required keys are missing from both baseline and participant data.

### 7.5 Multi-auth

- Test plans must support multiple auth bundles, including separate consent/token paths for content or permission variants such as basic vs detail.
- The UI must show which selected steps consume which auth bundle.
- Supported auth choices should include manual PSU, headless PSU where feasible, `private_key_jwt`, `tls_client_auth`/mTLS, and later mobile QR code auth.
- Environment capability metadata must prevent impossible suite/auth combinations from launching silently.
- Mobile QR code auth should be planned as a separate feature because its external dependency and UX are distinct.

### 7.6 Reporting, masking, and validation

- Persisted result JSON, NDJSON execution logs, API snapshots, browser downloads, and error payloads must remain masked by default.
- Report metadata must be sufficient for OBL-side validation: tool version, suite identity, manifest coverage status, plan selection, mandatory outcomes, custom-value deviations, and approved-release status.
- The OBL-side validator must not trust participant-side eligibility. It must recompute against trusted manifest, coverage, and approved-release inputs.

### 7.7 Agent-compatible delivery

- Requirements must have stable IDs, statuses, acceptance criteria, affected modules, test commands, and documentation impact.
- Agent slices must be small enough to review and validate independently.
- Machine-readable ledgers must be the durable source for coverage and requirement status.
- PRs generated by agents must preserve security boundaries, include focused tests, update docs when behaviour changes, and avoid unrelated edits.

## 8. Out of scope for immediate Phase 1 implementation

- Phase 2 portal implementation beyond architecture-preserving constraints.
- PSU simulator/browser automation.
- Admin test-case builder UI for Standards authors.
- Internationalisation.
- Certifiable claims for non-AIS families before their parity and Standards coverage ledgers exist.

## 9. Open decisions

Open decisions are tracked in `decisions.md`. The most urgent are environment capability metadata, custom-value certification policy, headless PSU feasibility, Standards sign-off representation, DCR timing, accessibility policy, and Phase 1 Docker runtime hardening details.
