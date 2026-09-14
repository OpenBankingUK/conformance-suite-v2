# Current test-plan pipeline characterization

This note records compatibility findings captured by the PR 1 golden fixtures
under `tests/fixtures/current_pipeline/`. These are observations about the
current implementation, not accepted contracts for the replacement
architecture. Production behaviour is intentionally unchanged.

## Golden journeys

| Journey | Current boundaries exercised |
| --- | --- |
| MVP support matrix | Full-scope canonical compilation and synthetic lowering for AIS, PIS, CBPII, and VRP on Read/Write v3.1.11 and v4.0.1, plus all DCR 3.4 operations |
| PIS v4.0.1 domestic standing order | Canonical plan parsing, structured creditor/amount/frequency mapping, required and optional endpoint capability selection, consent/submission/read dependency expansion, synthetic PSU/token steps, result traceability, and eligibility |
| PIS v3.1.11 domestic standing order | The equivalent legacy-version journey with scalar v3.1 frequency input, v3.1 endpoints and catalogue IDs, synthetic PSU/token steps, result traceability, and eligibility |
| AIS account transactions | Canonical AIS account/date mapping, required and optional capability applicability, consent/token dependencies, synthetic manifest generation, trace-safe runtime input evidence, and eligibility |
| DCR 3.4 full management | Canonical direct-endpoint parsing for POST, GET, PUT, and DELETE, all 34 cases and 79 execution steps, generated dependencies, the DCR-specific execution boundary, hierarchical result traceability, and eligibility |

The support matrix is an MVP preservation requirement, not a claim that one
golden test establishes normative conformance. Existing version-specific parity
contracts remain authoritative for complete legacy row, source, schema, and
signature inventories. The matrix golden proves that every supported catalogue
family can still be selected through the canonical participant-plan boundary,
compiled with all endpoints and capabilities, and lowered to the current
execution boundary.

## Compatibility findings

| ID | Observation | Why it is suspicious | Treatment |
| --- | --- | --- | --- |
| `TPCF-001` | PIS and AIS `businessTestData` values are copied through legacy config shapes and then rediscovered as flat runtime input IDs. | Logical participant inputs and technical runtime bindings are not separate, and the schema permits more structure than the selected catalogue necessarily consumes. | Preserve in PR 1. Replace only in the later contracts/compiler layers. |
| `TPCF-002` | Selecting a PIS standing-order submission or read causes consent creation and retrieval cases to be added even though those consent endpoints are absent from the participant's selected endpoint list. | Dependency expansion can execute operations outside the literal participant endpoint selection; the selected-endpoint trace alone does not describe all network operations. | Preserve and golden-test the expanded case and manifest step order. Later resolved plans must explain each inferred operation. |
| `TPCF-003` | `EndpointCapability.required` means automatically selected for an implemented endpoint, while optional capability IDs are participant-selected on that endpoint. | This is executable-test applicability, not a normative statement that an endpoint or capability is required by the Open Banking specification. | Preserve the current labels and booleans only as compatibility evidence. Do not reuse them as the future requirements model. |
| `TPCF-004` | Read/Write lowering generates 32-character request values and UUID-based invalid resource identifiers while building the synthetic manifest. | Two compilations can lower to manifests with different request bytes even when the plan and catalogue are identical. | Golden fixtures normalise only these explicitly runtime-generated values and preserve all surrounding structure. Deterministic generated-value instructions belong to the later execution-manifest layer. |
| `TPCF-005` | DCR compilation is passed through `_compiled_plan_to_manifest`, which produces a complete synthetic manifest with no steps, before execution branches to `DcrCatalogueExecutionAdapter`. | Read/Write and DCR do not currently share one executable-manifest contract; treating the empty DCR facade as executable would produce no coverage. | Golden-test the empty facade and DCR catalogue result trace separately. Preserve the dedicated adapter until the manifest-boundary PR. |
| `TPCF-006` | Participant-side eligibility is calculated from mandatory emitted steps, manifest coverage, compiled non-certifying reasons, and an approved tool-version policy. | It does not independently resolve normative endpoint/capability completeness from a requirements catalogue. A passing current result is therefore a pipeline self-assessment, not an independent conformance judgement. | Golden-test both the approved-policy eligible result and the otherwise-identical missing-policy ineligible result. Keep independent assessment separate. |
| `TPCF-007` | The same Read/Write family exposes materially different generated case IDs, request paths, step counts, and standing-order frequency bindings between v3.1.11 and v4.0.1. | Treating the v4.0.1 walking skeleton as representative of all supported versions would conceal version-specific compatibility behavior. | Preserve a detailed standing-order golden for each version and a full eight-entry Read/Write matrix golden. |

## Fixture stability

The snapshots intentionally include complete compiler applicability decisions,
ordered generated cases and dependencies, trace-safe runtime input snapshots,
synthetic step order and request shapes, result step IDs, and DCR trace-group
hierarchy. Timestamps are excluded. Sensitive values are not supplied.
Explicitly runtime-generated 32-character request identifiers are represented
as `<generated-32-character-value>`, and generated invalid-resource UUIDs are
represented as `<generated-invalid-resource-id>`.

`mvp_support_matrix.golden.json` is intentionally more compact than the journey
goldens. For each supported family/version it records the complete catalogue
case and capability IDs, selected endpoint and capability scope, applicability
and compiled case IDs, runtime input IDs, lowered manifest step IDs, and
mandatory-step count. DCR additionally records all protocol-neutral execution
step IDs because its current synthetic manifest is empty.
