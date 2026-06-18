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
- Use current typed v2 assertions for migrated checks. The transaction-basic alignment also uses the bundled OpenAPI response schema primitive for `OBTransaction6Basic` where present.
- Keep broad account-access consent for the original migrated slice, but use a separate basic-only consent, PSU authorisation, and token exchange for transaction-basic parity checks so they are not run under `ReadTransactionsDetail`. The current v4.0 AIS certification baseline mirrors this permission-boundary shape with a basic-only transaction flow and optional basic bulk coverage.

### v4.0 AIS Transaction-Basic Alignment

The bundled v2 suite now represents legacy manifest parity for:

- `OB-400-TRA-105000`, a mandatory account transactions check.
- `OB-400-TRA-105200`, an optional bulk transactions check.

Both checks use a basic-only token created from consent permissions `ReadAccountsBasic`, `ReadTransactionsBasic`, `ReadTransactionsDebits`, and `ReadTransactionsCredits`. `ReadTransactionsDetail` is deliberately excluded to match the legacy basic-permission scenario. `OB-400-TRA-105200` remains optional and deselected by default; when explicitly selected it runs through the same basic-only path and is expected to reproduce the known Model Bank failure.

For these two checks, the v2 assertions represent the legacy detail-absence treatment by asserting that detail-only transaction fields are absent on all returned transaction items. They also include the bundled OpenAPI `OBTransaction6Basic` response schema assertion where present in the suite. This remains partial legacy coverage and does not imply that all 95 legacy AIS scripts are implemented.

## Implemented Legacy Step IDs

Mandatory default legacy endpoint checks:

- `OB-400-ACC-100400`
- `OB-400-ACC-100200`
- `OB-400-BAL-101200`
- `OB-400-TRA-105000`
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
2. Extend schema validation coverage or document a formal waiver for remaining legacy `schemaCheck` gaps.
3. Add flow support for remaining permission profiles, no-token, and client-credentials-token protected-resource checks.
4. Generate or maintain a machine-readable inventory report that maps all 95 v4 AIS legacy scripts to implemented, waived, or blocked status.
5. Repeat the migration for v3.1 AIS, PIS/payment, CBPII, VRP, and cVRP only after each family's setup and consent/payment-flow requirements are modelled.
6. Promote this suite to `complete` only after every mandatory legacy script is represented and Standards stakeholders sign off any waivers.

## v4 PIS Legacy Benchmark

### Suite: `pis-fcs-legacy-benchmark`

Suite file: `conformance/suites/ob-read-write-v4.0-fapi1-advanced-pis-fcs-legacy-benchmark.json`  
Registered key: `ob-read-write / v4.0 / fapi1-advanced / pis / pis-fcs-legacy-benchmark`

Source manifest: `ob_4.0_payment_fca.json` (OpenBankingUK/conformance-suite, develop branch)  
Inventory: `docs/requirements/suite-coverage/v4-pis-prior-fcs-inventory.json`

### Mandatory Domestic Payment Steps (default-selected, 8 rows)

| Legacy ID | URI | Method | Coverage | Known Gaps |
|---|---|---|---|---|
| OB-400-DOP-100100 | /domestic-payment-consents | POST | broad | — |
| OB-400-DOP-100110 | /domestic-payment-consents | POST | broad | — |
| OB-400-DOP-100300 | /domestic-payment-consents | POST | broad | — |
| OB-316-DOP-100310 | /domestic-payment-consents | POST | broad | — |
| OB-400-DOP-100400 | /domestic-payment-consents/{consentId} | GET | broad | — |
| OB-400-DOP-100500 | /domestic-payment-consents/{consentId}/funds-confirmation | GET | broad | — |
| OB-400-DOP-100600 | /domestic-payments | POST | broad | — |
| OB-400-DOP-100700 | /domestic-payments/{paymentId} | GET | broad | — |

### Conditional Payment Type Steps (default-selected when prerequisites are available, 21 rows)

#### Domestic Scheduled Payment (7 rows)
OB-400-DOP-100800 through OB-400-DOP-101101 — require `scheduledPaymentDateTime` test value.

#### Domestic Standing Order (6 rows)
OB-400-DOP-101200 through OB-400-DOP-101503 — require `frequency` and standing-order date test values.

#### International Payment (4 rows)
OB-400-DOP-101600 through OB-400-DOP-101900 — require `currencyOfTransfer` and international creditor/agent test values.

#### International Scheduled Payment (4 rows)
OB-400-DOP-102000 through OB-400-DOP-102300 — require `scheduledPaymentDateTime` and international payment test values.

### Known Gaps

- `OB-400-DOP-101503`: model-bank known issue remains reproducible when both `FinalPaymentDateTime` and `Frequency.CountPerPeriod` are present; model bank may return `201` where legacy expectation is `400`.
- `certificationCoverage: partial` — this suite must not be used to claim PIS certification parity.

Legacy `validateSignature: true` rows now map to v2 `response_signature` assertions. These verify the ASPSP response `x-jws-signature` over the exact response body using the JWKS fetched by `jwks-fetch`.
