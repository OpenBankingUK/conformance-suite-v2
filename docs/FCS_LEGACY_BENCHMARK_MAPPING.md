# FCS Legacy Benchmark Mapping

This note records the first v2 migration slice for previous public FCS manifests. It keeps the current `ais-certification-baseline` suite intact and adds a separate `ais-fcs-legacy-benchmark` suite for candidate legacy parity work.

## Frozen Legacy Inputs

The benchmark source is the previous public conformance-suite manifest directory at `https://github.com/OpenBankingUK/conformance-suite/tree/develop/manifests`.

Relevant visible files:

- `assertions.json`
- `data.json`
- `ob_3.1_accounts_transactions_fca.json`
- `ob_4.0_accounts_transactions_fca.json`
- `ob_3.1_cbpii_fca.json`
- `ob_4.0_cbpii_fca.json`
- `ob_3.1_payment_fca.json`
- `ob_4.0_payment_fca.json`
- `ob_3.1_variable_recurring_payments.json`
- `ob_4.0_variable_recurring_payments.json`
- `cVRP_4.0_variable_recurring_payments.json`

The first implemented slice uses the already bundled v4 AIS snapshot at `conformance/standards/ob_read_write/v4_0/legacy-ob_4.0_accounts_transactions_fca.json`. Its provenance and hash chunks are recorded in `conformance/standards/ob_read_write/v4_0/sources.json`.

## v4 AIS Legacy Inventory Summary

The legacy v4 AIS accounts/transactions manifest contains 95 scripts. Every script is an HTTP `get` endpoint script. The legacy metadata includes script ID, description/detail, method, URI, URI implementation status, permissions, excluded permissions, query parameters, assertion names, `asserts_one_of`, and `schemaCheck`.

Notable legacy features that are not fully represented by current v2 primitives:

- `schemaCheck` full schema validation.
- `asserts_one_of` assertion-group semantics.
- Goessner-style array predicates such as `Data.Transaction.#(BankTransactionCode)`.
- Exact request-header playback assertions for `x-fapi-interaction-id`.
- Permission-negative flows requiring separate consent/token variants.
- Explicit no-token and client-credentials-token protected-resource variants.
- Reusable legacy assertion references from `assertions.json`.

## First v2 Mapping

The first bundled v2 suite is `ob-read-write-v4.0-fapi1-advanced-ais-fcs-legacy-benchmark.json`, registered as `ob-read-write / v4.0 / fapi1-advanced / ais / ais-fcs-legacy-benchmark`.

Mapping decisions:

- Preserve legacy script IDs as v2 step IDs where a legacy endpoint check is represented.
- Keep setup steps as v2-native IDs: OpenID discovery, JWKS fetch, client credentials token, account-access consent creation, manual PSU authorisation, and authorization-code token exchange.
- Use `${config.oauth.resourceBaseUrl}/open-banking/v4.0/aisp{legacy-uri}` for migrated AIS resource requests.
- Use the current FAPI signing directives for consent creation, PSU request object generation, and token-endpoint authentication.
- Keep `certificationCoverage: partial`.
- Treat legacy `optional` and `conditional` resource rows as v2 `optional: true`, deselected by default.
- Use only current typed v2 assertions: `http_status`, `header`, and `json_field` structural checks.
- Do not claim full permission-differentiation parity while the v2 suite creates one broad account-access consent.

## Implemented Legacy Step IDs

Mandatory default legacy endpoint checks:

- `OB-400-ACC-100400`
- `OB-400-ACC-100200`
- `OB-400-BAL-101200`
- `OB-400-TRA-105100`
- `OB-400-TRA-105110`
- `OB-400-TRA-105120`

Optional or conditional opt-in endpoint checks:

- `OB-400-BAL-101300`
- `OB-400-BEN-101800`
- `OB-400-BEN-101900`
- `OB-400-DIR-102300`
- `OB-400-DIR-102400`
- `OB-400-OFF-102600`
- `OB-400-PAR-102900`
- `OB-400-PAR-102901`
- `OB-400-PRO-103200`
- `OB-400-SCP-103500`
- `OB-400-STO-103800`
- `OB-400-TRA-105200`

## Expansion Path

Next slices should add typed primitives before widening parity claims:

1. Add an assertion-group primitive for `asserts_one_of`.
2. Add schema validation or document a formal waiver for `schemaCheck`.
3. Add flow support for separate permission profiles, no-token, and client-credentials-token protected-resource checks.
4. Generate or maintain a machine-readable inventory report that maps all 95 v4 AIS legacy scripts to implemented, waived, or blocked status.
5. Repeat the migration for v3.1 AIS, PIS/payment, CBPII, VRP, and cVRP only after each family's setup and consent/payment-flow requirements are modelled.
6. Promote this suite to `complete` only after every mandatory legacy script is represented and Standards stakeholders sign off any waivers.
