# Decision log

[Project README](../README.md) | [Developer guide](DEVELOPER_GUIDE.md)

This lightweight log makes current design choices discoverable. These initial
entries are retrospective summaries of checked-in documentation and
implementation, not a record of historical approval meetings. **Status:
implemented** means the choice is present in the repository; it does not imply
certification approval. Historical dates, approvers and undocumented rejected
alternatives are not reconstructed.

| ID | Decision | Status |
| --- | --- | --- |
| [DL-001](#dl-001-shared-canonical-test-plan) | Shared canonical test plan across browser, CLI and REST | Implemented |
| [DL-002](#dl-002-catalogue-owned-generated-tests) | Catalogue-owned tests derived from participant scope | Implemented |
| [DL-003](#dl-003-local-report-assurance) | Local report validation is not proof of authenticity | Implemented Phase 1 boundary |
| [DL-004](#dl-004-server-side-drafts-and-masked-evidence) | Server-side drafts and masked evidence | Implemented |
| [DL-005](#dl-005-offline-unit-and-component-tests) | Deterministic offline unit/component suite | Implemented |

## DL-001: Shared canonical test plan

**Context:** Browser, CLI and REST must agree about participant scope and runtime
configuration.

**Decision:** Use a canonical JSON-first schemaVersion `1.0` document, prepared
and validated through the shared test-plan boundary. Keep internal manifests as
an execution facade, not participant-selectable configuration.

**Recorded rationale:** The developer guide describes reuse of the hardened
HTTP, signing, masking, PSU and evidence engine while replacing legacy
participant surfaces. A common plan contract keeps the three entry points
aligned.

**Tradeoffs:** Old config-selected suites, public manifest/deselection arguments
and legacy DCR JSON are deliberately not supported. Browser import can recover
fields for editing, but launch still requires shared validation.

**Evidence:** [Shared contract](DEVELOPER_GUIDE.md#shared-plan-document-contract),
[removed surfaces](DEVELOPER_GUIDE.md#removed-public-surfaces),
[validation implementation](../conformance/test_plan_validation.py).

## DL-002: Catalogue-owned generated tests

**Context:** Participants describe what they implement; conformance coverage
must remain traceable without allowing mandatory checks to be removed.

**Decision:** Generate tests from versioned catalogues using implemented
endpoints and endpoint-scoped capabilities. Required capabilities are selected
automatically; generated tests are read-only in the builder.

**Recorded rationale:** Coverage and mandatory applicability remain
catalogue-owned while optional implementation-dependent checks follow declared
scope. Legacy mappings and pinned specification snapshots provide traceability.

**Tradeoffs:** Participants cannot choose arbitrary generated case IDs.
Specification endpoint obligations and required tests for declared endpoints
are distinct concepts; the builder and compiler must preserve that distinction.

**Evidence:** [Catalogue architecture](DEVELOPER_GUIDE.md#catalogue-architecture),
[endpoint matrix](READ_WRITE_ENDPOINT_REQUIREMENTS.md),
[legacy mapping](FCS_LEGACY_BENCHMARK_MAPPING.md),
[compiler/model](../conformance/catalogue.py).

## DL-003: Local report assurance

**Context:** Phase 1 executes on participant-controlled containers/filesystems.

**Decision:** The internal validator checks report consistency and
certification-readiness against independently supplied criteria and approved
release policy. It does not authenticate local reports or make a formal
certification decision. This beta is not certification-approved.

**Recorded rationale:** A participant controls local execution and evidence, so
deliberate tampering cannot be excluded. The developer guide assigns trusted
provenance and immutable audit history to future portal-managed execution.

**Tradeoffs:** Locally validated evidence remains subject to OBL review.
Uploading a local report does not establish its authenticity. The Phase 2 portal
is future scope, not an implemented guarantee of this release.

**Evidence:** [Certification validation and assurance boundary](DEVELOPER_GUIDE.md#certification-validation),
[beta restriction](../README.md), [validator](../conformance/certification_validator.py).

## DL-004: Server-side drafts and masked evidence

**Context:** Imported plans and pasted/uploaded credentials can contain private
keys, tokens and assertions.

**Decision:** Use server-side file-backed Django sessions by default, not
signed-cookie drafts. Accept each supported credential by file reference or
inline material, with safe exports omitting secrets and explicit secret-bearing
exports for local use. Mask recognised credentials in logs/results.

**Recorded rationale:** The developer guide explicitly warns that signed-cookie
sessions would expose secret-bearing imported plans to the browser. File-backed
sessions also avoid a database-migration prerequisite for the local builder.
Read-only credential mounts remain an alternative to putting credentials in
the draft.

**Tradeoffs:** Inline secrets still live in server-side session storage for the
draft lifetime; safe exports need credentials restored before reuse. Masking is
not anonymisation: business data, identifiers, URLs and paths require manual
review before sharing. Unmasked developer logging is only for local debugging.

**Evidence:** [Session/credential behaviour](DEVELOPER_GUIDE.md#environment-variables),
[settings](../config/settings.py),
[safe plan snapshots](../conformance/test_plan_validation.py),
[credential configuration](../conformance/plan_configuration.py).

## DL-005: Offline unit and component tests

**Context:** Conformance logic and evidence need repeatable checks without bank
availability or real credentials.

**Decision:** The supported pytest suite has exactly two categories, unit and
component, and a session-wide non-loopback socket guard. Mock HTTP at external
boundaries; use loopback fixtures only when transport behaviour is under test.
Container startup/health checking is a separate packaging concern.

**Recorded rationale:** The testing strategy requires deterministic offline
coverage and enforces it rather than trusting marker names or conventions.

**Tradeoffs:** This suite does not demonstrate interoperability with a live
ASPSP. Protocol fixtures must justify why a socket boundary is necessary;
packaging checks do not replace conformance assertions.

**Evidence:** [Testing strategy](TESTING_STRATEGY.md),
[test enforcement](../tests/conftest.py), [local checks](DEVELOPER_GUIDE.md#local-checks).

## Maintaining this log

Add an entry for a material architecture, trust-boundary or conformance-contract
decision in the same change that implements it. Use the next stable `DL-nnn`
identifier, context, decision, status, recorded rationale, tradeoffs and evidence
links. If rationale or approval history is unavailable, say so rather than
inventing it. Proposed decisions must be labelled proposed, not implemented.

Do not silently rewrite an obsolete choice into its replacement: mark it
superseded and link to the new entry. Keep detailed operational instructions
and normative contracts in their own guides, linked from here.
