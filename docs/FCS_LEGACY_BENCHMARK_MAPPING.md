# FCS Legacy Benchmark Mapping

This document records the hand-maintained mapping from legacy Functional
Conformance Suite coverage into the v2 catalogue model. The v3.1.11 source baseline is `OpenBankingUK/conformance-suite` release
`v1.10.0`, commit `1908789b52e26e0a79fc5a565e411f771006c3ce`.
The v4 mapping continues to use the legacy manifest set under `manifests/`.

The v2 product no longer exposes legacy manifests, suite resources, or
pre-selected smoke/slice suite names to participants. Legacy provenance is kept
inside catalogue test-case `compliance_scope` values and result traceability.

## Source manifest families

| Legacy source | v2 catalogue key |
| --- | --- |
| `ob_3.1_accounts_transactions_fca.json` | `open-banking / v3.1 / ais` |
| `ob_3.1_payment_fca.json` | `open-banking / v3.1 / pis` |
| `ob_3.1_cbpii_fca.json` | `open-banking / v3.1 / cbpii` |
| `ob_3.1_variable_recurring_payments.json` | `open-banking / v3.1 / vrp` |
| `ob_4.0_accounts_transactions_fca.json` | `open-banking / v4.0 / ais` |
| `ob_4.0_payment_fca.json` | `open-banking / v4.0 / pis` |
| `ob_4.0_cbpii_fca.json` | `open-banking / v4.0 / cbpii` |
| `ob_4.0_variable_recurring_payments.json` | `open-banking / v4.0 / vrp` |
| `cVRP_4.0_variable_recurring_payments.json` | `open-banking / v4.0 / cvrp` |

Read/Write `3.1.11` binds only to the dedicated internal `v3.1` catalogue keys.
The participant interface does not expose generic `3.1` or earlier v3.1.x
versions. Security profile `FAPI1_ADVANCED` is normalized to
`fapi1-advanced`.

## Read/Write v3.1.11 parity contract

The machine-checkable contract is
[`conformance/standards/ob_read_write/v3_1_11/parity-contract.json`](../conformance/standards/ob_read_write/v3_1_11/parity-contract.json).
It preserves manifest order, duplicate rows, missing versus empty fields,
version predicates, request data, assertion order, context capture, schema
flags, and response-signature flags. Only HTTP method casing and JSON object-key
ordering are normalized. Assertion and data references resolve exclusively
against the pinned v1.10.0 `assertions.json` and `data.json`.

| API | Rows | Unique script IDs | Schema checks | Signature checks |
| --- | ---: | ---: | ---: | ---: |
| AIS | 96 | 96 | 35 | 0 |
| PIS | 32 | 32 | 31 | 22 |
| CBPII | 14 | 13 | 10 | 0 |
| VRP | 14 | 14 | 14 | 11 |

The duplicate `OB-301-CBPII-000009` delete and expiration variants retain
separate row identities. VRP `apiVersion` predicates are evaluated against
`3.1.11`, so pre-3.1.11 variants remain in the audit ledger but do not execute.
CBPII consent expirations execute the pinned UTC `nextDayDate` and
`nextDayDateTime` macro semantics, and the invalid-consent delete replays the
legacy literal identifier `42`.
The four normative OpenAPI snapshots are pinned from
`OpenBankingUK/read-write-api-specs` tag `v3.1.11`.

Each selected row compiles an executable legacy assertion bundle preserving
the source `asserts`, `asserts_one_of`, and `asserts_last_if_all` group
semantics. Response schema checks select the pinned v3.1.11 OpenAPI response
schema by the actual HTTP status. Legacy query variants compile as distinct
requests with their pinned query values; they are not suppressed by v2-only
optional capability gates. Domestic standing-order requests use the
scalar `businessTestData.pis.standingOrderFrequencyV31` value required by v3.1;
the v4 frequency object remains isolated to v4 catalogues.

The contract records one approved correction: AIS row
`OB-301-PRO-103403` uses the normative plural `/products` endpoint instead of
the legacy manifest's invalid singular `/product` path. Any further intentional
departure from observable v1.10.0 behavior must add a source row, original and
replacement behavior, rationale, and regression test to its `corrections`
array. Any unlisted delta is a release blocker.

## Mapping conventions

- Catalogue IDs are new v2 IDs; they do not preserve old suite names.
- Endpoint applicability is exact HTTP method plus standards path.
- Endpoint capabilities are new v2 domain IDs; they describe participant
  implementation scope and are not generated test-case IDs.
- Required capabilities document baseline endpoint coverage and are selected
  automatically for implemented endpoints.
- Optional capabilities document implementation-dependent features, filters,
  variants, or resource/payment types that only generate tests when selected.
- Setup, consent, token, and security prerequisites are dependency test cases,
  not participant-selectable suites.
- Runtime values are represented as catalogue runtime input requirements.
- Legacy script and manifest provenance is carried in `compliance_scope` values
  prefixed with `legacy-` or the older CBPII `legacy_` form.
- v3.1.11 compilation emits only v3.1 paths, schema documents, and source
  provenance. v4 PIS compilation emits only the 29 pinned v4 rows and their
  exact executable assertion bundles.
- Legacy `schemaCheck: true` is represented as executable bundled OpenAPI
  `response_schema` assertions for JSON response bodies. Legacy 204/no-body
  schema checks remain represented by their status assertion because there is no
  JSON body to validate.
- Generated results expose the compiled catalogue traceability block instead of
  a legacy `suite` block.

## Capability traceability

Capability selections are recorded in three places:

1. `resourceGroups[].endpoints[].capabilities` in schemaVersion `1.0` test
   plans records optional capabilities explicitly declared by the participant.
   Required baseline capabilities may be omitted because the compiler selects
   them automatically.
2. Compiler traceability records `selectedCapabilities` with method, path,
   capability ID, label, and required/optional status.
3. Result JSON and run detail surface selected endpoint/capability counts,
   generated test-case IDs, applicability decisions, runtime input snapshots
   with sensitive values omitted, and non-certifying reasons.

This preserves legacy FCS parity while replacing suite/test checkbox selection
with endpoint/capability/config selection.

## AIS accounts and transactions

Catalogue: `open-banking / v4.0 / ais`
Version: `2026.07.legacy-fcs-ais-at.1`
Cases: 96 catalogue cases; full v4 execution filters v3-only variants and covers
all 95 legacy v4 manifest scripts.

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Setup/dependencies for accounts coverage | `ais-at-setup-discovery`, `ais-at-setup-consent`, `ais-at-setup-token` |
| `GET /open-banking/v4.0/aisp/accounts` | `ais-at-accounts-list-200`, `ais-at-accounts-list-401`, `ais-at-accounts-list-playback` |
| `GET /open-banking/v4.0/aisp/accounts/{AccountId}` | `ais-at-account-by-id-200`, `ais-at-account-by-id-401`, `ais-at-account-by-id-playback`, `ais-at-account-by-id-404` |
| Balances | `ais-at-account-balances-200`, `ais-at-account-balances-playback`, `ais-at-account-balances-404`, `ais-at-legacy-balance-*` |
| Beneficiaries | `ais-at-legacy-beneficiary-*` |
| Direct debits | `ais-at-legacy-direct-debit-*` |
| Offers | `ais-at-legacy-offer-*` |
| Parties | `ais-at-legacy-party-*` |
| Products | `ais-at-legacy-product-*` |
| Scheduled payments | `ais-at-legacy-scheduled-payment-*` |
| Standing orders | `ais-at-legacy-standing-order-*` |
| Statements | `ais-at-legacy-statement-*` |
| `GET /open-banking/v4.0/aisp/accounts/{AccountId}/transactions` | `ais-at-account-transactions-200`, `ais-at-account-transactions-401`, `ais-at-account-transactions-playback` |
| `GET /open-banking/v4.0/aisp/transactions` | `ais-at-transactions-list-200`, `ais-at-transactions-list-401`, `ais-at-transactions-list-playback` |

## PIS payments

Catalogue: `open-banking / v4.0 / pis`
Version: `2026.09.legacy-fcs-pis.2`
Cases: 29 catalogue cases covering all 29 legacy v4 manifest rows one-for-one.

The dedicated v4 catalogue executes the pinned legacy rows in source order.
Each case runs the original `asserts`, `asserts_one_of`, and schema-check
semantics rather than retaining some assertion ids as traceability-only
metadata. It also preserves the source response-signature flags, fixed
end-to-end identifiers, next-day UTC-midnight date macros, distinct invalid
standing-order bodies, and the two independent domestic scheduled-payment
consent flows.

The dedicated v3.1.11 adapter executes all 32 selected v3.1 ledger rows as
32 distinct request cases. It preserves the non-authorising and authorising
domestic-consent rows separately, replays the pinned fixed end-to-end
identifiers, reuses the consent initiation values for submissions, and
renders the legacy `nextDayDate` scheduled-payment variants at next-day UTC
midnight.

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Domestic payment consent | `pis-v4-domestic-payment-consent-create`, `pis-v4-domestic-payment-consent-reject-invalid-signature`, `pis-v4-domestic-payment-consent-read-authorised` |
| Domestic payment execution | `pis-v4-domestic-payment-funds-confirmation`, `pis-v4-domestic-payment-create`, `pis-v4-domestic-payment-read` |
| Domestic scheduled payments | `pis-v4-domestic-scheduled-payment-consent-create`, `pis-v4-domestic-scheduled-payment-consent-read`, `pis-v4-domestic-scheduled-payment-consent-create-and-authorise`, `pis-v4-domestic-scheduled-payment-consent-read-after-authorisation`, `pis-v4-domestic-scheduled-payment-create` |
| Domestic standing orders | `pis-v4-domestic-standing-order-consent-create`, `pis-v4-domestic-standing-order-consent-read`, `pis-v4-domestic-standing-order-create`, `pis-v4-domestic-standing-order-read`, `pis-v4-domestic-standing-order-reject-invalid-frequency` |
| International payments | `pis-v4-international-payment-consent-create`, `pis-v4-international-payment-consent-read`, `pis-v4-international-payment-create`, `pis-v4-international-payment-read` |
| International scheduled payments | `pis-v4-international-scheduled-payment-consent-create`, `pis-v4-international-scheduled-payment-consent-read`, `pis-v4-international-scheduled-payment-create`, `pis-v4-international-scheduled-payment-read` |

## CBPII

Catalogue: `open-banking / v4.0 / cbpii`
Version: `2026.7.23`
Cases: 12 catalogue cases covering all legacy v4 CBPII entries, including the
duplicate `OB-400-CBPII-000009` delete and expiration variants.

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Funds-confirmation consent creation | `cbpii-consent-create-core`, `cbpii-consent-create-invalid-account-name`, `cbpii-consent-create-invalid-account-identification`, `cbpii-consent-create-invalid-scheme-name`, `cbpii-consent-create-expiration-milliseconds-z`, `cbpii-consent-create-expiration-milliseconds-offset`, `cbpii-consent-create-expiration-seconds-z`, `cbpii-consent-create-expiration-seconds-offset` |
| Funds-confirmation consent read/delete | `cbpii-consent-get-authorised`, `cbpii-consent-delete`, `cbpii-consent-delete-invalid-id` |
| Funds confirmation | `cbpii-funds-confirmation-create` |

## VRP

Catalogue:

- `open-banking / v4.0 / vrp`

Version: `2026.07.legacy-fcs-vrp-cvrp.1`
Cases: 17 catalogue cases; full v4 execution filters v3.1 pre/post-3.1.11
variants and covers all 11 legacy v4 VRP manifest scripts.

| Endpoint area | VRP catalogue IDs |
| --- | --- |
| Consent creation | `vrp-consent-create-awaiting-authorisation-v4` |
| Consent read/delete | `vrp-consent-get-authorised`, `vrp-consent-delete`, `vrp-consent-get-after-delete`, `vrp-consent-delete-after-delete` |
| Consent funds confirmation | `vrp-consent-funds-confirmation` |
| Payment creation | `vrp-payment-create-initial-v4`, `vrp-payment-create-repeated-v4` |
| Payment read/details | `vrp-payment-get-initial`, `vrp-payment-get-repeated`, `vrp-payment-get-details` |

The VRP catalogue retains full script-ID provenance for
`ob_3.1_variable_recurring_payments.json` and
`ob_4.0_variable_recurring_payments.json`. cVRP is intentionally excluded from
the public Open Banking UK Read/Write v4 boundary because it is not currently
treated as an OBL Read/Write resource group.

## Dynamic Client Registration 3.4

The DCR parity baseline is the separate legacy
[`OpenBankingUK/conformance-dcr` v1.4.0 release at commit
`cc00a0065494e8e180c915621b9996bc2259ec8d`](https://github.com/OpenBankingUK/conformance-dcr/tree/cc00a0065494e8e180c915621b9996bc2259ec8d).
It contains ten scenarios (`DCR-001` through `DCR-011`, excluding
`DCR-006`), 34 cases, and 79 traceable steps.

DCR uses family `OBL_DCR`, top-level POST/GET/PUT/DELETE endpoint scope, and no
resource groups. POST is mandatory and locked; management methods are optional;
token setup is a generated dependency. The exact inventory, request/status and
state contract, canonical document example, legacy configuration mapping, and
approved corrections are recorded in
[`DCR_3_4_PARITY_CONTRACT.md`](DCR_3_4_PARITY_CONTRACT.md) and its
[machine-checkable ledger](../conformance/standards/ob_dcr/v3_4/parity-contract.json).
The registered executable catalogue and typed adapter consume this contract
across browser, CLI, REST, run-result, and certification surfaces.

## Regression expectations

The mapping is guarded by tests that assert:

- The four public Open Banking Read/Write catalogue families are registered in
  both dedicated v3.1 and v4.0 catalogue boundaries in
  `conformance.catalogue_registry`; cVRP remains a private catalogue fixture.
- The v3.1.11 parity contract pins the v1.10.0 commit, source hashes, row
  identities, duplicate CBPII row, selection predicates, and correction list.
- v3.1.11 compilation rejects v4 paths, schema documents, provenance, and case
  identifiers while preserving existing v4 behavior.
- Each bundled catalogue test case carries legacy compliance-scope
  provenance.
- Family-specific catalogue tests compile representative endpoint selections and
  retain expected legacy manifest/source traces, v4-only case selection, and
  executable schema/assertion parity for supported legacy checks.
- Capability tests prove required baseline capabilities are implicit and optional
  implementation features include or exclude only the intended generated cases.
- Compiled plan results include top-level `catalogue` traceability and omit the
  removed legacy `suite` block.
- Browser, REST, CLI, and run-detail tests share the same endpoint/capability
  plan-spec contract and secret-safe export/evidence expectations.
- The DCR parity and product-integration guards pin the v1.4.0 commit,
  scenario/case/step IDs and counts, direct endpoint scope, configuration
  mapping, approved legacy corrections, lifecycle statuses, and masking.

Any expansion of the catalogue must update this document and the matching
family-specific catalogue tests in the same change.
