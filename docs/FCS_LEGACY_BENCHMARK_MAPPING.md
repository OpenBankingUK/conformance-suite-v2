# FCS Legacy Benchmark Mapping

This document records the hand-maintained mapping from legacy Functional
Conformance Suite coverage into the v2 catalogue model. The source baseline is
the legacy `OpenBankingUK/conformance-suite` manifest set under `manifests/`.

The v2 product no longer exposes legacy manifests, suite resources, or
pre-selected smoke/slice suite names to participants. Legacy provenance is kept
inside catalogue test-case `compliance_scope` values and result traceability.

## Source manifest families

| Legacy source | v2 catalogue key |
| --- | --- |
| `ob_3.1_accounts_transactions_fca.json`, `ob_4.0_accounts_transactions_fca.json` | `open-banking / v4.0 / ais` |
| `ob_3.1_payment_fca.json`, `ob_4.0_payment_fca.json` | `open-banking / v4.0 / pis` |
| `ob_3.1_cbpii_fca.json`, `ob_4.0_cbpii_fca.json` | `open-banking / v4.0 / cbpii` |
| `ob_3.1_variable_recurring_payments.json`, `ob_4.0_variable_recurring_payments.json` | `open-banking / v4.0 / vrp` |
| `cVRP_4.0_variable_recurring_payments.json` | `open-banking / v4.0 / cvrp` |

Legacy v3.1 and v4.0 inputs are folded into v4.0 catalogue boundaries where the
current v2 compiler has one canonical catalogue per API family. Security
profiles remain applicability filters, not duplicate catalogues.

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
- Generated results expose the compiled catalogue traceability block instead of
  a legacy `suite` block.

## Capability traceability

Capability selections are recorded in three places:

1. `planSpec.implementedEndpoints[].capabilities` records optional capabilities
   explicitly declared by the participant. Required baseline capabilities may be
   omitted because the compiler selects them automatically.
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
Cases: 19

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Setup/dependencies for accounts coverage | `ais-at-setup-discovery`, `ais-at-setup-consent`, `ais-at-setup-token` |
| `GET /open-banking/v4.0/aisp/accounts` | `ais-at-accounts-list-200`, `ais-at-accounts-list-401`, `ais-at-accounts-list-playback` |
| `GET /open-banking/v4.0/aisp/accounts/{AccountId}` | `ais-at-account-by-id-200`, `ais-at-account-by-id-401`, `ais-at-account-by-id-playback`, `ais-at-account-by-id-404` |
| `GET /open-banking/v4.0/aisp/accounts/{AccountId}/balances` | `ais-at-account-balances-200`, `ais-at-account-balances-playback`, `ais-at-account-balances-404` |
| `GET /open-banking/v4.0/aisp/accounts/{AccountId}/transactions` | `ais-at-account-transactions-200`, `ais-at-account-transactions-401`, `ais-at-account-transactions-playback` |
| `GET /open-banking/v4.0/aisp/transactions` | `ais-at-transactions-list-200`, `ais-at-transactions-list-401`, `ais-at-transactions-list-playback` |

## PIS payments

Catalogue: `open-banking / v4.0 / pis`
Version: `2026.07.legacy-fcs-pis.1`
Cases: 23

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Domestic payment consent | `pis-v4-domestic-payment-consent-create`, `pis-v4-domestic-payment-consent-reject-invalid-signature`, `pis-v4-domestic-payment-consent-read-authorised` |
| Domestic payment execution | `pis-v4-domestic-payment-funds-confirmation`, `pis-v4-domestic-payment-create`, `pis-v4-domestic-payment-read` |
| Domestic scheduled payments | `pis-v4-domestic-scheduled-payment-consent-create`, `pis-v4-domestic-scheduled-payment-consent-read`, `pis-v4-domestic-scheduled-payment-create`, `pis-v4-domestic-scheduled-payment-read` |
| Domestic standing orders | `pis-v4-domestic-standing-order-consent-create`, `pis-v4-domestic-standing-order-consent-read`, `pis-v4-domestic-standing-order-create`, `pis-v4-domestic-standing-order-read`, `pis-v4-domestic-standing-order-reject-invalid-frequency` |
| International payments | `pis-v4-international-payment-consent-create`, `pis-v4-international-payment-consent-read`, `pis-v4-international-payment-create`, `pis-v4-international-payment-read` |
| International scheduled payments | `pis-v4-international-scheduled-payment-consent-create`, `pis-v4-international-scheduled-payment-consent-read`, `pis-v4-international-scheduled-payment-create`, `pis-v4-international-scheduled-payment-read` |

## CBPII

Catalogue: `open-banking / v4.0 / cbpii`
Version: `2026.7.23`
Cases: 7

| Endpoint area | Representative v2 catalogue IDs |
| --- | --- |
| Funds-confirmation consent creation | `cbpii-consent-create-core`, `cbpii-consent-create-invalid-account-data`, `cbpii-consent-create-expiration-formats` |
| Funds-confirmation consent read/delete | `cbpii-consent-get-authorised`, `cbpii-consent-delete`, `cbpii-consent-delete-invalid-id` |
| Funds confirmation | `cbpii-funds-confirmation-create` |

## VRP and cVRP

Catalogues:

- `open-banking / v4.0 / vrp`
- `open-banking / v4.0 / cvrp`

Version: `2026.07.legacy-fcs-vrp-cvrp.1`
Cases: 11 per family

| Endpoint area | VRP catalogue IDs | cVRP catalogue IDs |
| --- | --- | --- |
| Consent creation | `vrp-consent-create-awaiting-authorisation` | `cvrp-consent-create-awaiting-authorisation` |
| Consent read/delete | `vrp-consent-get-authorised`, `vrp-consent-delete`, `vrp-consent-get-after-delete`, `vrp-consent-delete-after-delete` | `cvrp-consent-get-authorised`, `cvrp-consent-delete`, `cvrp-consent-get-after-delete`, `cvrp-consent-delete-after-delete` |
| Consent funds confirmation | `vrp-consent-funds-confirmation` | `cvrp-consent-funds-confirmation` |
| Payment creation | `vrp-payment-create-initial`, `vrp-payment-create-repeated` | `cvrp-payment-create-initial`, `cvrp-payment-create-repeated` |
| Payment read/details | `vrp-payment-get-initial`, `vrp-payment-get-repeated`, `vrp-payment-get-details` | `cvrp-payment-get-initial`, `cvrp-payment-get-repeated`, `cvrp-payment-get-details` |

## Regression expectations

The mapping is guarded by tests that assert:

- All five legacy-derived API families are registered in
  `conformance.catalogue_registry`.
- Each bundled catalogue test case carries legacy compliance-scope
  provenance.
- Family-specific catalogue tests compile representative endpoint selections and
  retain expected legacy manifest/source traces.
- Capability tests prove required baseline capabilities are implicit and optional
  implementation features include or exclude only the intended generated cases.
- Compiled plan results include top-level `catalogue` traceability and omit the
  removed legacy `suite` block.
- Browser, REST, CLI, and run-detail tests share the same endpoint/capability
  plan-spec contract and secret-safe export/evidence expectations.

Any expansion of the catalogue must update this document and the matching
family-specific catalogue tests in the same change.
