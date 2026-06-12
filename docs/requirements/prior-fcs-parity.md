# Prior-FCS parity approach

Prior-FCS parity is the baseline for certification readiness. The goal is not to copy the previous tool blindly; it is to ensure every previous test behaviour is either implemented in v2, intentionally improved, explicitly waived, recorded as a legacy issue, or blocked by a known missing primitive.

## Current evidence

- The bundled prior-FCS AIS source at `conformance/standards/ob_read_write/v4_0/legacy-ob_4.0_accounts_transactions_fca.json` contains 95 scripts.
- The generated inventory at `docs/requirements/suite-coverage/v4-ais-prior-fcs-inventory.json` captures all 95 scripts as machine-readable rows.
- `docs/FCS_LEGACY_BENCHMARK_MAPPING.md` documents the first v4 AIS migration slice and known gaps.
- `conformance/suites/ob-read-write-v4.0-fapi1-advanced-ais-fcs-legacy-benchmark.json` preserves selected previous script IDs in a partial v4 benchmark suite.
- `conformance/suites/ob-read-write-v4.0.1-fapi1-advanced-ais-certification-baseline.json` is the current v4.0.1 AIS baseline, but it is not yet a full prior-FCS parity ledger.

## Inventory schema

The suite coverage ledger should classify each previous script with:

| Field | Meaning |
| --- | --- |
| `legacyScriptId` | Previous FCS script identifier, preserved where possible. |
| `legacySourceVersion` | Previous spec/source version used for the baseline. |
| `resource` | API resource family such as Account, Balance, Transaction, Beneficiary. |
| `uri` | Previous FCS URI pattern. |
| `method` | Previous FCS HTTP method. |
| `uriImplementation` | Previous mandatory/optional/conditional signal. |
| `permissions` | Permissions required by the legacy script. |
| `excludedPermissions` | Permissions explicitly excluded by the legacy script. |
| `asserts` | Previous named assertions. |
| `assertsOneOf` | Previous assertion group semantics, if present. |
| `schemaCheck` | Whether previous FCS expected schema validation. |
| `v2Status` | `implemented`, `partially_implemented`, `requires_new_primitive`, `blocked`, `waived`, `legacy_issue`, `superseded`, `not_started`, or `requires_v401_confirmation`. |
| `v2ManifestStepIds` | V2 step IDs implementing the behaviour. |
| `v2RequirementIds` | Requirement IDs from `requirements.yml`. |
| `certificationImpact` | Whether unresolved status blocks suite promotion. |
| `notes` | Human-readable rationale, especially for waivers and legacy issues. |

## Mapping rules

- Preserve previous script IDs as v2 step IDs when the v2 step represents the same endpoint check.
- Use clear v2-native IDs for setup/auth steps that did not exist as previous endpoint scripts.
- Treat previous `uriImplementation: mandatory` as certification-blocking until Standards confirms otherwise.
- Treat previous optional/conditional scripts as visual-plan-builder opt-in candidates unless Standards reclassifies them.
- Record basic/detail and other permission/content variants as separate auth-bundle requirements.
- Do not promote the target suite to `complete` while mandatory prior-FCS rows are `not_started`, `blocked`, `requires_new_primitive`, or `requires_v401_confirmation`.

## Known primitive and modelling gaps

- Assertion-group semantics for `asserts_one_of`.
- Remaining schema-check parity and any required schema waivers.
- Goessner-style array predicates.
- `x-fapi-interaction-id` playback assertions.
- Permission-negative flows requiring separate consent/token variants.
- No-token and client-credentials-token protected-resource checks.
- Reusable previous assertion references from `assertions.json`.

## Generated inventory summary

The generated inventory currently records:

- total scripts: 95
- mandatory scripts: 24
- optional scripts: 34
- conditional scripts: 37
- implemented in the current v4 benchmark: 19
- not implemented in the current v4 benchmark: 76

All rows still require v4.0.1 Standards applicability review before they can be used as certification evidence.

## Next implementation slice

Review the generated 95-row inventory and replace default statuses with explicit v4.0.1 decisions. This should be treated as a data/requirements PR, not a certification claim.

Suggested acceptance criteria:

1. Every generated row has a reviewed `v401Applicability`.
2. Every mandatory row has a certification-impacting status: implemented, waived, legacy issue, superseded, blocked, or requires new primitive.
3. Existing v4 benchmark step IDs are checked against the v4.0.1 suite strategy.
4. Standards records waivers and legacy issues explicitly.
5. Rows that need new generic primitives become agent-ready implementation slices.
