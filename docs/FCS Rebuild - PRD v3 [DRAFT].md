# FCS Rebuild - PRD v3 [DRAFT]

> **Version:** 3.0
> **Last updated:** 21 April 2026
> **Status:** In review

---

## Problem Statement

The current Functional Conformance Suite (FCS) is built on unsupported technologies (Go, outdated JavaScript) and carries 281 known security vulnerabilities — 17 Critical, 90 High — that cannot be remediated without a full rebuild. Participants running the current Docker-distributed tool are exposed to these vulnerabilities on their own infrastructure, and OBL has received formal support tickets raising this concern.

Beyond security, the platform introduces significant operational and legal risk: test results can be falsified with no mechanism for OBL to detect this; certification outcomes may therefore be inaccurate; and the manual, multi-team certification process is error-prone, slow, and resource-intensive. Internal expertise in the current technology stack is concentrated in a single team, creating a single point of failure.

The time and cost to maintain the current platform now outweighs the cost of rebuilding it on a modern, supported, and extensible stack.

---

## Solution

Rebuild the FCS as a secure, maintainable, and generic conformance testing platform in Python. The platform will be delivered in two phases:

- **Phase 1 (Local):** A hardened single Docker container that participants run locally to execute conformance tests and produce a certification report.
- **Phase 2 (Portal):** A multi-container hosted portal for centralised certification management, run history, dashboards, authenticated API access, and an OBL-managed certification workflow.

**Critical assumption:** Phase 2 business case approval is expected. Phase 1 is being built as the foundation for Phase 2, not as a throwaway. Every Phase 1 decision must hold up under Phase 2 requirements. If the business case is not approved, Phase 1 still operates independently.

The new platform will:

- Replace all unsupported technology, enabling ongoing security vulnerability remediation
- Be implemented in Python (OBL's strategic language) to reduce resourcing risk and internal knowledge concentration
- Use a declarative, configuration-driven architecture so conformance logic is expressed as data (manifests + assertions), not bespoke code
- Be designed so that new standards can be supported through configuration, without code changes to the engine
- Produce deterministic, auditable test results in a standardised report format that includes a built-in certification eligibility assessment
- Support both headless and manual PSU authorisation flows
- Produce structured execution logs throughout a test run for audit and debugging purposes

---

## User Stories

### Participant (ASPSP) Stories

1. As a participant, I want to download a hardened Docker image, so that I can run conformance tests on my own infrastructure without introducing known vulnerabilities.
2. As a participant, I want to provide my configuration (certificates, endpoints, environment details) in a single config file, so that I can set up the tool without manual OBL intervention.
3. As a participant, I want the option to import or convert my existing FCS discovery/config file, so that I do not have to rebuild my configuration from scratch when migrating to the new platform. _(SHOULD — not a hard Phase 1 requirement.)_
4. As a participant, I want mandatory test cases to be pre-populated in my test plan by default, so that I do not have to manually identify required tests. I also want to be able to deselect tests if I need to run a partial plan, with a clear indication that doing so will make the run ineligible for certification.
5. As a participant, I want to select conditional and optional test cases via the UI, so that I can build a test plan that reflects my specific implementation.
6. As a participant, I want to run a full conformance test plan with a single trigger, so that I do not need to manually initiate each test group separately.
7. As a participant, I want tests in independent groups to continue running even if one group fails, so that I receive a complete picture of my conformance status in a single run.
8. As a participant, I want to be guided through PSU authorisation via a redirect URL during testing, so that I can complete the consent flow manually in my test environment.
9. As a participant, I want the option to run tests headlessly, so that I can execute unattended test sessions in CI pipelines.
10. As a participant, I want to receive a structured report at the end of a test run, so that I can review all test outcomes and submit it to OBL for certification.
11. As a participant, I want the report to include a certification eligibility assessment, so that I can self-assess before submitting to OBL — including a clear indication if mandatory tests were missing from the run.
12. As a participant, I want failed tests to include the full request and response details in the report, so that I can diagnose and debug issues without OBL assistance.
13. As a participant, I want sensitive data (account numbers, tokens, keys) to be masked in the report by default, so that I can share the report with OBL without exposing production credentials.
14. As a participant, I want PASS, FAIL, WARN, and SKIPPED outcome states, so that I understand the nature of each result — including tests that could not run due to earlier failures.
15. As a participant, I want warnings to not block certification, so that I am informed of future risks without being prevented from certifying against current requirements.
16. As a participant, I want the tool to validate mandatory fields in API responses, so that I have confidence my implementation meets the specification.
17. As a participant, I want the tool to support FAPI 1 Advanced (OB-flavoured) security profiles including hybrid flow, client certificates, and TLS, so that my security implementation is fully tested.
18. As a participant, I want to provide my transport and signing certificates as PEM files with a configurable CA chain, so that the tool supports OB legacy, third-party, and ETSI certificate types uniformly.
19. As a participant, I want the tool to support testing against v3.1.11 and v4.0 of the OB Read/Write API specification.
20. As a participant, I want the Docker image to be signed so I can verify its authenticity, so that I have confidence I am running the genuine OBL-published tool.
21. As a participant, I want DCR conformance testing to be included in the platform, so that I can test my Dynamic Client Registration implementation as part of the same toolchain.
22. As a participant, I want the tool to support mobile-only journeys in the API test suite, so that I can certify implementations like HSBC Kinetic that do not use browser redirects.
23. As a participant, I want clear, comprehensive documentation and in-app guidance covering setup, configuration, and test plan building, so that I can use the tool without requiring OBL support.
24. As a participant, I want to receive advance notice of changes to configuration formats, test behaviour, or certification flow, so that I can plan updates to my test setup without disruption.
25. As a participant, I want to receive a structured execution log alongside my report, so that I can trace what happened during the run for debugging purposes.

### OBL Standards Team Stories

1. As an OBL Standards team member, I want to define test cases as declarative configuration files (not code), so that I can author and update tests without requiring an Engineering release.
2. As an OBL Standards team member, I want to define reusable assertions as named, versioned building blocks, so that complex validation logic can be composed and reused across test definitions.
3. As an OBL Standards team member, I want mandatory tests to be defined in configuration per spec version and standard, not hardcoded, so that adding or changing required tests does not require code changes.
4. As an OBL Standards team member, I want to add support for new standards by adding new configuration, so that the engine requires no modification.
5. As an OBL Standards team member, I want warnings to be definable per test case in configuration, so that I can signal future-deprecation risks to participants without failing their certification.
6. As an OBL Standards team member, I want the platform to support v3.1.11 and v4.0 manifests, with version loaded from participant configuration.
7. As an OBL Standards team member, I want the test plan schema to be semantically versioned, so that breaking changes are clearly communicated and traceable.
8. As an OBL Standards team member, I want key architecture and design decisions to be documented in a decision log, so that future maintainers understand the rationale. _(Format to be agreed — likely informal notes in Jira or a repo log file.)_

### OBL Certification/Monitoring Team Stories

1. As an OBL certification team member, I want an internal tool that reads a submitted report and verifies all mandatory tests passed, so that I can certify participants faster and more reliably than the current bash script process.
2. As an OBL certification team member, I want the validation to check mandatory test criteria from configuration (not hardcoded), so that the tool remains accurate as the test suite evolves.
3. As an OBL certification team member, I want the tool to confirm the FCS version used is an approved release.
4. As an OBL certification team member, I want the tool to output a certification summary for pasting into Confluence, so that the Phase 1 workflow (Salesforce → validate → Confluence → close) is preserved with reduced manual effort.
5. As an OBL monitoring team member, I want a portal with a certification management workflow — where participants publish results and the OBL team can review, approve, and track them — so that the manual Salesforce/Confluence process is replaced. _(Phase 2 only.)_
6. As an OBL monitoring team member, I want a dashboard showing certification history and test results across participants. _(Phase 2 only.)_

### OBL Engineering Team Stories

1. As an OBL engineer, I want vulnerability scanning embedded in the CI/CD pipeline, scoped to OBL-owned repositories, so that every build is checked before release.
2. As an OBL engineer, I want the Docker base image to be actively maintained and minimal.
3. As an OBL engineer, I want the CI/CD pipeline to follow best practices and be set up from scratch, using the existing pipeline as a reference only.
4. As an OBL engineer, I want a developer mode toggle for unmasked logs, so that I can debug complex failures without production-safe masking interfering. _(Never enabled in release builds.)_
5. As an OBL engineer, I want the engine to expose a REST API (unauthenticated for local Docker, bound to localhost), so that the tool can be called programmatically from scripts or CI pipelines.
6. As an OBL engineer, I want the platform structured so that the Phase 1 local deployment can grow into the Phase 2 portal without rearchitecting.
7. As an OBL engineer, I want the platform to produce structured execution logs that comply with OWASP security logging requirements, so that security-relevant events are captured and auditable.

---

## Technology Stack

The following decisions have been confirmed. All other technology choices — including database, state coordination, HTTP client, and testing framework — are engineering decisions to be made during design and are not requirements at this stage.

| Decision | Status |
| --- | --- |
| **Language** | **Confirmed:** Python throughout (engine, API, CLI, certification validator) |
| **Backend framework** | **Confirmed:** Django. Selected for its security track record, built-in auth/admin/session management needed for Phase 2, and regulatory defensibility. |
| **Frontend (Phase 1)** | **Confirmed:** Django templates + HTMX. Server-side rendering, no JavaScript build pipeline, minimal attack surface. |
| **Frontend (Phase 2)** | **TBD:** Continue HTMX for most features. If complex dashboards or live data exploration are required, introducing React or similar is an option without rearchitecting the backend. |
| **CI/CD** | **Requirement:** Automated pipeline with vulnerability scanning on every build, gated on Critical/High severity findings. Specific tooling TBD (current expectation: GitHub Actions + Snyk). |
| **Config format** | **Under discussion.** Further decision required. |
| **Report output format** | **Under discussion.** JSON as the primary machine-readable certification artefact is expected. Further format decisions TBD. |

---

## Engine Architecture

The conformance engine is the core of the platform. It is domain-agnostic — it has no knowledge of Open Banking, FAPI, or any specific standard. All domain knowledge lives in test configuration (manifests and assertions).

**Key principles:**

- The engine accepts a **TestPlan** as its input — a versioned, structured description of what to test and how. How the plan is produced (via the UI, CLI, API, or hand-authored) is irrelevant to the engine.
- **Test groups have two phases: setup then execution.** The setup phase acquires the consent and access token required for that group. Consent acquisition is itself a conformance test — the FAPI authorisation flow is being validated, not merely used as a stepping stone. Open Banking API types (AIS, PIS, CBPII, VRP) each require a separate consent; they cannot be merged.
- Where manual PSU authorisation is required, consents are collected sequentially across groups before execution begins.
- Once all setup is complete, execution-phase tests within independent groups can proceed concurrently. Steps within a group run sequentially.
- A failed group does not halt other independent groups. All groups run to completion.
- The engine core must be independent of the web framework and infrastructure. It must be possible to swap the deployment mode (local vs portal) by changing how the engine is wired up at startup, not by changing the engine itself.
- Supporting a new standard (e.g. cVRP) must be achievable by adding new manifest configuration, without changing engine code.

### Manifest and Test Configuration Design

- Test cases are expressed declaratively in configuration files. The engine executes them; it does not contain test logic.
- Assertions are generic — they check properties of HTTP responses (status codes, field presence/absence, header values, response schema) without reference to specific Open Banking domain concepts. This ensures the engine can be reused for any standard.
- Mandatory tests are defined in configuration per spec version and standard. They are pre-populated in the test plan by default. Participants may deselect mandatory tests for partial runs, but any run with missing mandatory tests is automatically marked as not eligible for certification in the report.

---

## Security Profile

- Phase 1: FAPI 1 Advanced (OB-flavoured) — hybrid flow, client certificate, TLS, mTLS.
- The security profile configuration must be pluggable so that FAPI 2 or other profiles can be introduced without code changes to the engine. _(FAPI 2 implementation is not in Phase 1 scope.)_
- Certificate configuration: transport cert + key (PEM), signing cert + key (PEM), CA chain (concatenated intermediates + root, PEM). Supports OB legacy, third-party, and ETSI cert types uniformly.
- HSBC trust anchor override: a configurable trust anchor is required to support ASPSPs with non-standard certificate chains. The generalised pattern for this is under Architecture review.
- OWASP Top 10 compliance is a security acceptance criterion for the platform.

---

## PSU Authorisation

Both modes are Phase 1 requirements. Both use the FAPI hybrid flow. The FCS must have a redirect URI it controls registered with the ASPSP. The FCS hosts a callback endpoint at this URI. The `state` parameter in the OAuth2 flow identifies which run a callback belongs to.

**Manual:** The FCS generates the authorisation URL and displays it. The participant completes the PSU consent journey in their browser on the ASPSP's authorisation server. The ASPSP redirects to the FCS callback with the authorisation code. The FCS extracts the code, exchanges it for an access token, and continues.

**Headless:** The FCS submits the authorisation request and waits for the ASPSP to redirect automatically to the callback endpoint — without human PSU interaction. This requires the ASPSP test environment to support automated redirect completion. Whether Ozone and other sandbox environments support this natively, and under what conditions, requires investigation before headless mode is finalised. _(Open todo — see below.)_

**Phase 1 (local):** Callback coordination is in-process. Single process, one run at a time.

**Phase 2 (portal):** Callback coordination across multiple worker processes. Mechanism TBD.

**Mobile consent (QR code flow):** Some ASPSP implementations use mobile-only consent journeys where PSU approval is captured via QR code on a mobile device. Phase 1 preserves the existing AWS Lambda service dependency for this — the FCS makes outbound calls to the existing OBL-managed AWS service. Phase 2 will migrate this to an OBL-managed container within the platform, removing the external Lambda dependency.

**No PSU simulator** (automated browser automation) is in scope for any phase.

---

## Result Outcomes

- Four states: `PASS`, `FAIL`, `WARN`, `SKIPPED`.
- `WARN`: test passed but a deprecation or risk signal applies. Does not block certification.
- `SKIPPED`: test could not run because a prerequisite setup step failed.
- Full request and response captured on `FAIL`, `WARN`, and `SKIPPED`. Summary only on `PASS`.
- Sensitive fields (tokens, keys, account numbers, sort codes) masked by default. Unmasking requires explicit developer mode opt-in — never enabled in release builds.

### Certification Eligibility Assessment

Every test run report includes a built-in certification eligibility assessment. This is a self-service check for participants — not OBL's formal certification decision.

The assessment indicates:

- Whether all mandatory tests were included in the run
- Whether all mandatory tests passed
- Whether the FCS version used is an approved release

If any mandatory tests were deselected or failed, the report explicitly states the run is not eligible for certification submission. The criteria for this assessment are driven by configuration, not hardcoded.

---

## Logging

The FCS must produce structured logs throughout every test run.

- **Execution log:** A running trace of test execution — requests made, responses received, assertion results, and key decision points. This is a separate output from the report and is intended for debugging. Written to the output directory in Phase 1.
- **Security and audit logging:** Security-relevant events (configuration errors, authentication failures, unexpected responses, application errors) must be captured in structured logs in accordance with OWASP A09 requirements.
- **Log levels:** Production default is INFO. Developer mode enables DEBUG with unmasked data. Debug/unmasked logging must never be enabled in release builds.
- Specific logging implementation and format are engineering decisions.

---

## REST API

- **Phase 1 (Local):** Unauthenticated REST API bound to localhost. Supports starting a run, polling run status, and retrieving the report. This allows the tool to be used headlessly from CI pipelines.
- **Phase 2 Portal (initial rollout):** Participants access the platform via the UI. Programmatic API access is not available until the authentication and authorisation model is defined.
- **Phase 2 Portal (later):** Authenticated API, design TBD, aligned to portal user registration and OBL Directory integration.

---

## Spec Version Support

- Phase 1 supports v3.1.11 and v4.0 of the OB Read/Write API specification.
- Spec version is loaded from participant configuration — adding further versions is a manifest addition, not a code change.
- **Ozone dependency:** OBL's Ozone model bank contract is a critical path dependency. The concern is that Ozone is expected to support only one v4 version at a time — once v4.0.1 is published, v4.0.0 may no longer be available. This affects which exact v4 version is testable and must be confirmed with Procurement before Phase 1 scope is locked.

---

## Config Migration

A conversion tool that imports existing FCS discovery/config files into the new format is a Phase 1 **SHOULD** — it reduces participant migration friction but does not block the core function. It is the first deliverable to cut if capacity is constrained.

---

## Deployment Architecture

### Phase 1: Local (Single Docker Container)

Participants run a single Docker container on their own infrastructure. No database, no external services except the existing AWS mobile consent Lambda. Fire-and-forget: configure, run, get a report. **Phase 1 supports one run at a time.**

**Participant provides (read-only):**

- Configuration file — ASPSP endpoints, environment details, certificate paths
- Transport and signing certificates and keys (PEM)
- CA chain (concatenated intermediates + root, PEM)

**Tool produces:**

- Structured JSON report — the certification artefact
- HTML report — human-readable version (if enabled)
- Execution log — running trace of the test run

**Container security:** Hardening is baked into the container, not left as a participant guide. The container must run as a non-root user, with a read-only filesystem (except a tmpfs scratch area), dropped capabilities, no privilege escalation, and localhost-only port binding. Certificates and configuration are provided via read-only volume mount and are never baked into the image. A Docker security recommendations document will be produced separately with full detail.

### Phase 2: Portal (Multi-Container)

OBL hosts a multi-container deployment. Components: web/API server, background worker(s), persistent database, state coordination service, identity management. Phase 2 also includes a certification management feature — a UI for participants to submit results and for the OBL team to review, approve, and track certifications — replacing the current Salesforce/Confluence manual process. Phase 2 supports multiple concurrent runs.

### Deployment Mode Switching

The platform must support switching between local and portal modes through configuration, without changing application code or the Docker image.

| Characteristic | Local (Phase 1) | Portal (Phase 2) |
| --- | --- | --- |
| Persistence | None — fire and forget | Run history, results, and certification records persisted |
| Concurrency | One run at a time | Multiple concurrent runs |
| Authentication | None | Identity management required (participants cannot submit results claiming to be an ASPSP they do not represent) |
| Report delivery | Written to output volume | Stored and downloadable via portal |
| Callback coordination | In-process | Across worker processes (mechanism TBD) |
| Certification workflow | Participant submits report to OBL via Salesforce (preserved from current process) | Managed within portal — submission, OBL review, and certificate publication |

---

## Phase 1 Certification Flow

The Phase 1 certification process is preserved from the current workflow:

1. Participant submits a Salesforce ticket with the report attachment.
2. Billing handled separately before certification.
3. OBL runs the internal CertificationValidator against the report.
4. Validator checks: all mandatory tests present and passed, FCS version is an approved release, report schema is valid.
5. OBL publishes the certificate to Confluence and closes the Salesforce ticket.

The CertificationValidator criteria are driven by configuration, not hardcoded, so they remain accurate as the test suite evolves.

**Result integrity risk:** Reports produced by a locally-run Docker container cannot be cryptographically verified by OBL. This risk is formally accepted for Phase 1. It must be logged on the RAID log with Risk Team sign-off. It is not resolved unless Phase 2 is approved.

---

## Versioning

- TestPlan schema and manifest format are semantically versioned.
- The FCS image version is recorded in every report, enabling the CertificationValidator to confirm the run used an approved release.
- Breaking changes to config or schema require a major version bump and advance participant communication.

---

## Testing Approach

The project will follow a test-driven development (TDD) approach. Tests exercise observable external behaviour through module public interfaces — not internal implementation details.

The following areas require test coverage as a minimum: conformance engine execution logic, assertion evaluation, FAPI HTTP client behaviour, manifest loading and validation, report generation and masking, and certification validation logic.

Integration tests will be run against the Ozone model bank.

Specific testing frameworks and tooling are engineering decisions.

_(Note: a detailed test plan with module-level coverage targets is a suggested engineering deliverable, to be produced during design. Draft targets are available as a working reference but are not locked requirements at this stage.)_

---

## DCR

DCR conformance is absorbed from the existing `conformance-dcr` repo as a first-class test suite within the new platform. Config fields from the existing DCR config (SSA, KID, transport certs, spec version, optional method flags) will be mapped to the new test plan schema. Mobile-only journey support (e.g. HSBC Kinetic) is an API test suite concern, not a DCR concern.

---

## Documentation (Phase 1 Deliverables)

1. Participant setup and configuration guide
2. Test plan schema reference
3. Architecture and decision log _(Format to be agreed — likely informal notes in Jira or a repo log file, not a formal ADR structure.)_
4. OBL operator and certification guide

Documentation lives in the repository as the primary home. In-app contextual help in the UI is a Phase 1 desirable to reduce participant support burden.

---

## Out of Scope (Phase 1)

- **FAPI 2 implementation** — architecture must allow for it; implementation deferred.
- **PSU simulator** (automated browser automation) — confirmed out of scope for all phases.
- **Admin test case builder UI** — Phase 2. Phase 1 supports manifest authoring via config files only.
- **Multi-language / internationalisation** — English only.
- **Spec versions prior to v3.1.11** — not supported.
- **cVRP manifest content** — the engine must support new standards by configuration, but cVRP manifest content is a separate deliverable.
- **Automated certification workflow (Salesforce, Confluence, billing)** — Phase 2.
- **Portal result integrity verification** — risk accepted for Phase 1.
- **Phase 2 portal** — subject to separate business case approval.

---

## Critical Path Blockers

- **Ozone contract confirmation** (Procurement): v3.1.11 and v4.0 availability, which exact v4 subversion will be supported, rate limits, and cost. Blocks Phase 1 scope lock.
- **Portal business case approval**: determines Phase 2 scope and timeline.
- **Phase 1 end-of-year deadline**: scope not formally locked against timeline.

---

## Open Todos / Investigation Items

- **Headless PSU auth feasibility** — investigation required to confirm which ASPSP sandbox environments (including Ozone) support automated redirect completion, and under what configuration. This must be confirmed before headless mode is finalised.
- **Accessibility policy** — internal OBL policy investigation in progress. Determines WCAG target level and CI tooling requirement.
- **HSBC trust anchor pattern** — Architecture team review required before implementation. Expected to generalise to a configurable trust anchor pattern.
- **Beta ASPSP recruitment** — programme ownership and participant recruitment process TBD.
- **Django/asyncio integration** — early engineering spike recommended to validate that the async conformance engine can be invoked correctly from the Django web layer under the intended server configuration.
- **Mobile consent Phase 2 migration** — Phase 1 dependency on AWS Lambda confirmed. Phase 2 containerisation approach to be designed.
- **Config format decision** — format for participant config files and manifests requires further discussion.
- **Report output format** — detailed format requirements for JSON report and execution log require further discussion.
- **Decision log format** — informal Jira notes vs repo log file to be agreed.
- **Logging requirements detail** — structured logging and OWASP A09 compliance requirements to be detailed during design.

---

## RAID Items to Raise

- **Phase 1 result integrity risk** — Docker-run results can be falsified. Requires formal Risk Team acceptance and RAID log entry. Unresolved if Phase 2 is not approved.
- **Ozone contract** — Procurement engagement required as critical path action.
- **Engineering capacity** — BAU, planned leave, competing priorities. Flag impact on Phase 1 timeline.
- **Single-point-of-failure on Standards team knowledge** — documentation is the mitigation.
- **Snyk scope** — confirm only OBL-owned repositories are monitored. Non-OBL repos must not be included.
- **Identity management for Phase 2** — participants publishing certification results must be authenticated as representatives of their ASPSP. Identity framework approach to be confirmed during Phase 2 design.
