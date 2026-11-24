# Read/Write endpoint implementation requirements

This matrix records the implementation requirement stated in each individual
Open Banking UK Read/Write resource page's endpoint table. It covers **v4.0.1,
v4.0.0 and v3.1.11**, independently reviewed on 2026-10-05 against
[`OpenBankingUK/read-write-api-docs-pub` at `0fbe637`](https://github.com/OpenBankingUK/read-write-api-docs-pub/tree/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a).
Upstream calls v4.0.0 **v4.0** and stores its documentation in `docs/v4.0/`,
not `docs/v4.0.0/`.

Every requirement cell links to the corresponding version's individual resource
page, not just the resource-group README. The review covers 34 endpoint-bearing
resource pages per version (102 in total), plus the group summaries and relevant
profile definitions. The resource index and AIS product data-model pages do not
define additional endpoints. There are 93 method/path pairs in each v4 version
and 91 in v3.1.11; the difference is the two VRP consent migration operations.

This is a specification evidence document, **not an implementation or test
coverage inventory**. Its machine-readable counterpart is
`conformance/endpoint_requirements.py`; the builder uses it for labels, mandatory
selection and resource-POST dependencies in both previews and saved scope forms.
It does not redefine the compiler's declared-scope contract. OAuth/OIDC
authorization and token endpoints, discovery, JWKS and DCR are outside this
Read/Write resource-page matrix. Paths below are relative paths exactly as
published in the endpoint tables, without an ASPSP host or API base path.

## Interpretation

The categorisation definitions are in the Read/Write Data API Profile:
[v4.0.1][401-profile], [v4.0.0][400-profile], [v3.1.11][311-profile].

| Code | Published requirement | Meaning |
| --- | --- | --- |
| M | Mandatory | Required for the applicable API/profile being implemented. This does not require every participant to select every API family. |
| C | Conditional | Required when the applicable condition holds, including regulatory obligations or availability through the ASPSP's existing online channel. Not synonymous with Optional. |
| O | Optional | The ASPSP may implement the endpoint. |
| MP | Mandatory (if resource POST implemented) | Required when the corresponding resource's POST endpoint is implemented; not unconditionally mandatory across the API family. |
| MD | Mandatory (if immediate debit supported) | Required when immediate debit is supported for international scheduled payments. |
| CN | Conditional (See Note 1) | Event-management condition described below; retain the exception rather than treating this as an unconditional MP. |
| - | Not listed in this version | Do not inherit the operation from a later version. |

**Endpoint implementation requirements are separate from mandatory tests or
capabilities for an implemented endpoint.** A catalogue's existing "Baseline"
label must not be used to derive any cell in this matrix.

Conditional endpoints can be declared in or out of a participant's test scope,
but omission alone is not evidence that the participant complies with its
regulatory obligations. The profiles require ASPSPs to document their implemented
Conditional and Optional endpoints on their developer portals.

In the builder, M endpoints that have catalogue coverage are selected and locked
within the selected applicable API family; MP endpoints become required when
their resource POST is declared implemented. MD and CN need their specific
condition and are not blanket-locked. C and O remain participant-selectable,
without claiming that a conditional regulatory requirement has been evaluated.
Tests are selected from the declared endpoint scope; required applicable tests
for a selected endpoint remain required. The registry covers the full documented
inventory, but does not invent catalogue coverage for endpoints lacking tests.
Catalogue-only negative-test paths are explicitly shown as not classified in
endpoint tables, rather than guessed to be Optional.

These constraints apply when editing the guided scope form. Imported/review-edited
JSON and CLI/REST plans retain their existing declared-scope semantics; this
registry does not yet enforce whole-API implementation completeness on those
paths or determine participant-specific regulatory conditions.

## AIS: accounts and transactions

| Method and relative endpoint | v4.0.1 | v4.0.0 | v3.1.11 |
| --- | --- | --- | --- |
| `POST /account-access-consents` | [M][401-aac] | [M][400-aac] | [M][311-aac] |
| `GET /account-access-consents/{ConsentId}` | [M][401-aac] | [M][400-aac] | [M][311-aac] |
| `DELETE /account-access-consents/{ConsentId}` | [M][401-aac] | [M][400-aac] | [M][311-aac] |
| `GET /accounts` | [M][401-accounts] | [M][400-accounts] | [M][311-accounts] |
| `GET /accounts/{AccountId}` | [M][401-accounts] | [M][400-accounts] | [M][311-accounts] |
| `GET /accounts/{AccountId}/balances` | [M][401-balances] | [M][400-balances] | [M][311-balances] |
| `GET /balances` | [O][401-balances] | [O][400-balances] | [O][311-balances] |
| `GET /accounts/{AccountId}/transactions` | [M][401-transactions] | [M][400-transactions] | [M][311-transactions] |
| `GET /transactions` | [O][401-transactions] | [O][400-transactions] | [O][311-transactions] |
| `GET /accounts/{AccountId}/beneficiaries` | [C][401-beneficiaries] | [C][400-beneficiaries] | [C][311-beneficiaries] |
| `GET /beneficiaries` | [O][401-beneficiaries] | [O][400-beneficiaries] | [O][311-beneficiaries] |
| `GET /accounts/{AccountId}/direct-debits` | [C][401-direct-debits] | [C][400-direct-debits] | [C][311-direct-debits] |
| `GET /direct-debits` | [O][401-direct-debits] | [O][400-direct-debits] | [O][311-direct-debits] |
| `GET /accounts/{AccountId}/standing-orders` | [C][401-standing-orders] | [C][400-standing-orders] | [C][311-standing-orders] |
| `GET /standing-orders` | [O][401-standing-orders] | [O][400-standing-orders] | [O][311-standing-orders] |
| `GET /accounts/{AccountId}/product` | [C][401-products] | [C][400-products] | [C][311-products] |
| `GET /products` | [O][401-products] | [O][400-products] | [O][311-products] |
| `GET /accounts/{AccountId}/offers` | [C][401-offers] | [C][400-offers] | [C][311-offers] |
| `GET /offers` | [O][401-offers] | [O][400-offers] | [O][311-offers] |
| `GET /accounts/{AccountId}/parties` | [C][401-parties] | [C][400-parties] | [C][311-parties] |
| `GET /accounts/{AccountId}/party` | [C][401-parties] | [C][400-parties] | [C][311-parties] |
| `GET /party` | [C][401-parties] | [C][400-parties] | [C][311-parties] |
| `GET /accounts/{AccountId}/scheduled-payments` | [C][401-scheduled-payments] | [C][400-scheduled-payments] | [C][311-scheduled-payments] |
| `GET /scheduled-payments` | [O][401-scheduled-payments] | [O][400-scheduled-payments] | [O][311-scheduled-payments] |
| `GET /accounts/{AccountId}/statements` | [C][401-statements] | [C][400-statements] | [C][311-statements] |
| `GET /accounts/{AccountId}/statements/{StatementId}` | [C][401-statements] | [C][400-statements] | [C][311-statements] |
| `GET /accounts/{AccountId}/statements/{StatementId}/file` | [O][401-statements] | [O][400-statements] | [O][311-statements] |
| `GET /accounts/{AccountId}/statements/{StatementId}/transactions` | [C][401-statements] | [C][400-statements] | [C][311-statements] |
| `GET /statements` | [O][401-statements] | [O][400-statements] | [O][311-statements] |

## PIS: payment initiation

| Method and relative endpoint | v4.0.1 | v4.0.0 | v3.1.11 |
| --- | --- | --- | --- |
| `POST /domestic-payment-consents` | [M][401-dpc] | [M][400-dpc] | [M][311-dpc] |
| `GET /domestic-payment-consents/{ConsentId}` | [M][401-dpc] | [M][400-dpc] | [M][311-dpc] |
| `GET /domestic-payment-consents/{ConsentId}/funds-confirmation` | [M][401-dpc] | [M][400-dpc] | [M][311-dpc] |
| `POST /domestic-payments` | [M][401-dp] | [M][400-dp] | [M][311-dp] |
| `GET /domestic-payments/{DomesticPaymentId}` | [M][401-dp] | [M][400-dp] | [M][311-dp] |
| `GET /domestic-payments/{DomesticPaymentId}/payment-details` | [O][401-dp] | [O][400-dp] | [O][311-dp] |
| `POST /domestic-scheduled-payment-consents` | [C][401-dspc] | [C][400-dspc] | [C][311-dspc] |
| `GET /domestic-scheduled-payment-consents/{ConsentId}` | [MP][401-dspc] | [MP][400-dspc] | [MP][311-dspc] |
| `POST /domestic-scheduled-payments` | [C][401-dsp] | [C][400-dsp] | [C][311-dsp] |
| `GET /domestic-scheduled-payments/{DomesticScheduledPaymentId}` | [MP][401-dsp] | [MP][400-dsp] | [MP][311-dsp] |
| `GET /domestic-scheduled-payments/{DomesticScheduledPaymentId}/payment-details` | [O][401-dsp] | [O][400-dsp] | [O][311-dsp] |
| `POST /domestic-standing-order-consents` | [C][401-dsoc] | [C][400-dsoc] | [C][311-dsoc] |
| `GET /domestic-standing-order-consents/{ConsentId}` | [MP][401-dsoc] | [MP][400-dsoc] | [MP][311-dsoc] |
| `POST /domestic-standing-orders` | [C][401-dso] | [C][400-dso] | [C][311-dso] |
| `GET /domestic-standing-orders/{DomesticStandingOrderId}` | [MP][401-dso] | [MP][400-dso] | [MP][311-dso] |
| `GET /domestic-standing-orders/{DomesticStandingOrderId}/payment-details` | [O][401-dso] | [O][400-dso] | [O][311-dso] |
| `POST /international-payment-consents` | [C][401-ipc] | [C][400-ipc] | [C][311-ipc] |
| `GET /international-payment-consents/{ConsentId}` | [MP][401-ipc] | [MP][400-ipc] | [MP][311-ipc] |
| `GET /international-payment-consents/{ConsentId}/funds-confirmation` | [MP][401-ipc] | [MP][400-ipc] | [MP][311-ipc] |
| `POST /international-payments` | [C][401-ip] | [C][400-ip] | [C][311-ip] |
| `GET /international-payments/{InternationalPaymentId}` | [MP][401-ip] | [MP][400-ip] | [MP][311-ip] |
| `GET /international-payments/{InternationalPaymentId}/payment-details` | [O][401-ip] | [O][400-ip] | [O][311-ip] |
| `POST /international-scheduled-payment-consents` | [C][401-ispc] | [C][400-ispc] | [C][311-ispc] |
| `GET /international-scheduled-payment-consents/{ConsentId}` | [MP][401-ispc] | [MP][400-ispc] | [MP][311-ispc] |
| `GET /international-scheduled-payment-consents/{ConsentId}/funds-confirmation` | [MD][401-ispc] | [MD][400-ispc] | [MD][311-ispc] |
| `POST /international-scheduled-payments` | [C][401-isp] | [C][400-isp] | [C][311-isp] |
| `GET /international-scheduled-payments/{InternationalScheduledPaymentId}` | [MP][401-isp] | [MP][400-isp] | [MP][311-isp] |
| `GET /international-scheduled-payments/{InternationalScheduledPaymentId}/payment-details` | [O][401-isp] | [O][400-isp] | [O][311-isp] |
| `POST /international-standing-order-consents` | [C][401-isoc] | [C][400-isoc] | [C][311-isoc] |
| `GET /international-standing-order-consents/{ConsentId}` | [MP][401-isoc] | [MP][400-isoc] | [MP][311-isoc] |
| `POST /international-standing-orders` | [C][401-iso] | [C][400-iso] | [C][311-iso] |
| `GET /international-standing-orders/{InternationalStandingOrderPaymentId}` | [MP][401-iso] | [MP][400-iso] | [MP][311-iso] |
| `GET /international-standing-orders/{InternationalStandingOrderPaymentId}/payment-details` | [O][401-iso] | [O][400-iso] | [O][311-iso] |
| `POST /file-payment-consents` | [C][401-fpc] | [C][400-fpc] | [C][311-fpc] |
| `POST /file-payment-consents/{ConsentId}/file` | [C][401-fpc] | [C][400-fpc] | [C][311-fpc] |
| `GET /file-payment-consents/{ConsentId}` | [MP][401-fpc] | [MP][400-fpc] | [MP][311-fpc] |
| `GET /file-payment-consents/{ConsentId}/file` | [C][401-fpc] | [C][400-fpc] | [C][311-fpc] |
| `POST /file-payments` | [C][401-fp] | [C][400-fp] | [C][311-fp] |
| `GET /file-payments/{FilePaymentId}` | [MP][401-fp] | [MP][400-fp] | [MP][311-fp] |
| `GET /file-payments/{FilePaymentId}/report-file` | [C][401-fp] | [C][400-fp] | [C][311-fp] |
| `GET /file-payments/{FilePaymentId}/payment-details` | [O][401-fp] | [O][400-fp] | [O][311-fp] |

## CBPII: confirmation of funds

| Method and relative endpoint | v4.0.1 | v4.0.0 | v3.1.11 |
| --- | --- | --- | --- |
| `POST /funds-confirmation-consents` | [M][401-fcc] | [M][400-fcc] | [M][311-fcc] |
| `GET /funds-confirmation-consents/{ConsentId}` | [M][401-fcc] | [M][400-fcc] | [M][311-fcc] |
| `DELETE /funds-confirmation-consents/{ConsentId}` | [M][401-fcc] | [M][400-fcc] | [M][311-fcc] |
| `POST /funds-confirmations` | [M][401-fc] | [M][400-fc] | [M][311-fc] |

## VRP: variable recurring payments

| Method and relative endpoint | v4.0.1 | v4.0.0 | v3.1.11 |
| --- | --- | --- | --- |
| `POST /domestic-vrp-consents` | [M][401-vrpc] | [M][400-vrpc] | [M][311-vrpc] |
| `GET /domestic-vrp-consents/{ConsentId}` | [M][401-vrpc] | [M][400-vrpc] | [M][311-vrpc] |
| `DELETE /domestic-vrp-consents/{ConsentId}` | [M][401-vrpc] | [M][400-vrpc] | [M][311-vrpc] |
| `POST /domestic-vrp-consents/{ConsentId}/funds-confirmation` | [M][401-vrpc] | [M][400-vrpc] | [M][311-vrpc] |
| `PUT /domestic-vrp-consents/{ConsentId}` | [O][401-vrpc] | [O][400-vrpc] | - |
| `PATCH /domestic-vrp-consents/{ConsentId}` | [O][401-vrpc] | [O][400-vrpc] | - |
| `POST /domestic-vrps` | [C][401-vrp] | [C][400-vrp] | [C][311-vrp] |
| `GET /domestic-vrps/{DomesticVRPId}` | [C][401-vrp] | [C][400-vrp] | [C][311-vrp] |
| `GET /domestic-vrps/{DomesticVRPId}/payment-details` | [O][401-vrp] | [O][400-vrp] | [O][311-vrp] |

The individual VRP payment tables label both POST and GET **Conditional**; do not
rewrite GET as MP merely because other payment resources use that pattern.
The v4 VRP profiles describe consent PUT/PATCH migration operations as
**conditional**, although the individual endpoint tables and VRP summaries label
them **Optional**. This matrix preserves the endpoint-table classification and
records that discrepancy below.

## Event notifications and management

| Method and relative endpoint | v4.0.1 | v4.0.0 | v3.1.11 |
| --- | --- | --- | --- |
| `POST /event-subscriptions` | [O][401-es] | [O][400-es] | [O][311-es] |
| `GET /event-subscriptions` | [MP][401-es] | [MP][400-es] | [MP][311-es] |
| `PUT /event-subscriptions/{EventSubscriptionId}` | [CN][401-es] | [CN][400-es] | [CN][311-es] |
| `DELETE /event-subscriptions/{EventSubscriptionId}` | [CN][401-es] | [CN][400-es] | [CN][311-es] |
| `POST /callback-urls` | [O][401-cu] | [O][400-cu] | [O][311-cu] |
| `GET /callback-urls` | [MP][401-cu] | [MP][400-cu] | [MP][311-cu] |
| `PUT /callback-urls/{CallbackUrlId}` | [CN][401-cu] | [CN][400-cu] | [CN][311-cu] |
| `DELETE /callback-urls/{CallbackUrlId}` | [CN][401-cu] | [CN][400-cu] | [CN][311-cu] |
| `POST /event-notifications` | [O][401-en] | [O][400-en] | [O][311-en] |
| `POST /events` | [O][401-events] | [O][400-events] | [O][311-events] |

**CN condition (Note 1 on both management resource pages, all three versions):**
PUT and DELETE are Optional when the ASPSP supports aggregated polling only
**and** only a single event type for aggregated polling. Otherwise they are
Mandatory when the corresponding POST (`/callback-urls` or
`/event-subscriptions`) is implemented.

**Endpoint owner matters:** `/event-notifications` is hosted by the **TPP** and
called by the ASPSP, unlike the other ASPSP-hosted endpoints in this matrix. Its
table says Optional, while its notes require a TPP receiving event notifications
to expose the endpoint and acknowledge notifications. Do not treat it as an
optional ASPSP-hosted inbound endpoint. Also, the Aggregated Polling API profile
calls implementation of that API **conditional**, although the `/events`
resource endpoint table says **Optional**:
[v4.0.1][401-polling], [v4.0.0][400-polling], [v3.1.11][311-polling].

## Source discrepancies requiring explicit treatment

The following disagreements are present in the pinned sources. The matrix
deliberately transcribes the individual resource endpoint tables; this is a
documentation convention, **not a claim that conflicting normative text has
been resolved**. Do not infer stronger mandatory locks from the summary alone.
Check applicable published known issues/errata or obtain standards clarification
before using these disputed classifications for a certification judgement.

| Endpoint(s) | Versions | Individual resource table | Other source |
| --- | --- | --- | --- |
| `GET /file-payment-consents/{ConsentId}` | All three | MP | PIS summary: C |
| `POST /file-payment-consents/{ConsentId}/file` | All three | C | PIS summary: MP |
| `GET /international-scheduled-payment-consents/{ConsentId}/funds-confirmation` | All three | MD | PIS summary: MP; the individual operation text specifically describes immediate debit |
| Callback URL and event subscription PUT/DELETE (four endpoints) | All three | CN, with the aggregated-polling exception above | Event summary: MP, without the exception |
| `PUT /domestic-vrp-consents/{ConsentId}` and `PATCH /domestic-vrp-consents/{ConsentId}` | v4.0.1, v4.0.0 | O | VRP profile migration section: Conditional |
| `POST /events` | All three | O | Aggregated Polling API profile: API implementation Conditional |

Summary sources: PIS [v4.0.1][401-pis-summary], [v4.0.0][400-pis-summary],
[v3.1.11][311-pis-summary]; events [v4.0.1][401-event-summary],
[v4.0.0][400-event-summary], [v3.1.11][311-event-summary].
VRP migration profile sources: [v4.0.1][401-vrp-profile],
[v4.0.0][400-vrp-profile].

The AIS direct-debits page also says an ASPSP "must provide this endpoint" in its
account-specific operation description, despite its endpoint table saying
Conditional. Preserve the table's C classification and assess the applicable
condition; do not turn this sentence into a universal M classification.

## Totals and version comparison

These counts are endpoint-table classifications, not counts of tests or claims
that all listed endpoints apply to every ASPSP. MP, MD and CN are kept separate
so their conditions are not lost.

| Version | M | C | O | MP | MD | CN | Total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4.0.1 | 20 | 29 | 24 | 15 | 1 | 4 | 93 |
| v4.0.0 | 20 | 29 | 24 | 15 | 1 | 4 | 93 |
| v3.1.11 | 20 | 29 | 22 | 15 | 1 | 4 | 91 |

For endpoints present in all three versions, the individual tables' requirement
classifications are identical. v4.0.0 and v4.0.1 additionally list Optional VRP
consent PUT and PATCH; v3.1.11 does not list them. Identical classifications do
not imply identical schemas, security requirements or test expectations.

## Maintenance

When refreshing this matrix, review each version's individual resource page and
its notes independently. Reconcile the method/path inventory with each group's
README to detect missing endpoints, but record disagreements instead of
overwriting the detailed table. Update the upstream commit, review date, links,
version differences and totals together. Do not derive implementation status
from OpenAPI operation presence or from test catalogue labels.

[401-profile]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/profiles/read-write-data-api-profile.md#categorisation-of-implementation-requirements
[400-profile]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/profiles/read-write-data-api-profile.md#categorisation-of-implementation-requirements
[311-profile]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/profiles/read-write-data-api-profile.md#categorisation-of-implementation-requirements
[401-aac]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/account-access-consents.md#endpoints
[400-aac]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/account-access-consents.md#endpoints
[311-aac]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/account-access-consents.md#endpoints
[401-accounts]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Accounts.md#endpoints
[400-accounts]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Accounts.md#endpoints
[311-accounts]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Accounts.md#endpoints
[401-balances]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Balances.md#endpoints
[400-balances]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Balances.md#endpoints
[311-balances]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Balances.md#endpoints
[401-transactions]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Transactions.md#endpoints
[400-transactions]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Transactions.md#endpoints
[311-transactions]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Transactions.md#endpoints
[401-beneficiaries]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Beneficiaries.md#endpoints
[400-beneficiaries]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Beneficiaries.md#endpoints
[311-beneficiaries]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Beneficiaries.md#endpoints
[401-direct-debits]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/direct-debits.md#endpoints
[400-direct-debits]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/direct-debits.md#endpoints
[311-direct-debits]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/direct-debits.md#endpoints
[401-standing-orders]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/standing-orders.md#endpoints
[400-standing-orders]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/standing-orders.md#endpoints
[311-standing-orders]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/standing-orders.md#endpoints
[401-products]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Products.md#endpoints
[400-products]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Products.md#endpoints
[311-products]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Products.md#endpoints
[401-offers]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Offers.md#endpoints
[400-offers]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Offers.md#endpoints
[311-offers]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Offers.md#endpoints
[401-parties]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Parties.md#endpoints
[400-parties]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Parties.md#endpoints
[311-parties]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Parties.md#endpoints
[401-scheduled-payments]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/scheduled-payments.md#endpoints
[400-scheduled-payments]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/scheduled-payments.md#endpoints
[311-scheduled-payments]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/scheduled-payments.md#endpoints
[401-statements]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/aisp/Statements.md#endpoints
[400-statements]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/aisp/Statements.md#endpoints
[311-statements]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/aisp/Statements.md#endpoints
[401-dpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-payment-consents.md#endpoints
[400-dpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-payment-consents.md#endpoints
[311-dpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-payment-consents.md#endpoints
[401-dp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-payments.md#endpoints
[400-dp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-payments.md#endpoints
[311-dp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-payments.md#endpoints
[401-dspc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-scheduled-payment-consents.md#endpoints
[400-dspc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-scheduled-payment-consents.md#endpoints
[311-dspc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-scheduled-payment-consents.md#endpoints
[401-dsp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-scheduled-payments.md#endpoints
[400-dsp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-scheduled-payments.md#endpoints
[311-dsp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-scheduled-payments.md#endpoints
[401-dsoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-standing-order-consents.md#endpoints
[400-dsoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-standing-order-consents.md#endpoints
[311-dsoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-standing-order-consents.md#endpoints
[401-dso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/domestic-standing-orders.md#endpoints
[400-dso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/domestic-standing-orders.md#endpoints
[311-dso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/domestic-standing-orders.md#endpoints
[401-ipc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-payment-consents.md#endpoints
[400-ipc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-payment-consents.md#endpoints
[311-ipc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-payment-consents.md#endpoints
[401-ip]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-payments.md#endpoints
[400-ip]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-payments.md#endpoints
[311-ip]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-payments.md#endpoints
[401-ispc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-scheduled-payment-consents.md#endpoints
[400-ispc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-scheduled-payment-consents.md#endpoints
[311-ispc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-scheduled-payment-consents.md#endpoints
[401-isp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-scheduled-payments.md#endpoints
[400-isp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-scheduled-payments.md#endpoints
[311-isp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-scheduled-payments.md#endpoints
[401-isoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-standing-order-consents.md#endpoints
[400-isoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-standing-order-consents.md#endpoints
[311-isoc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-standing-order-consents.md#endpoints
[401-iso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/international-standing-orders.md#endpoints
[400-iso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/international-standing-orders.md#endpoints
[311-iso]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/international-standing-orders.md#endpoints
[401-fpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/file-payment-consents.md#endpoints
[400-fpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/file-payment-consents.md#endpoints
[311-fpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/file-payment-consents.md#endpoints
[401-fp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/file-payments.md#endpoints
[400-fp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/file-payments.md#endpoints
[311-fp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/file-payments.md#endpoints
[401-fcc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/cbpii/funds-confirmation-consent.md#endpoints
[400-fcc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/cbpii/funds-confirmation-consent.md#endpoints
[311-fcc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/cbpii/funds-confirmation-consent.md#endpoints
[401-fc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/cbpii/funds-confirmation.md#endpoints
[400-fc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/cbpii/funds-confirmation.md#endpoints
[311-fc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/cbpii/funds-confirmation.md#endpoints
[401-vrpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/vrp/domestic-vrp-consents.md#endpoints
[400-vrpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/vrp/domestic-vrp-consents.md#endpoints
[311-vrpc]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/vrp/domestic-vrp-consents.md#endpoints
[401-vrp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/vrp/domestic-vrps.md#endpoints
[400-vrp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/vrp/domestic-vrps.md#endpoints
[311-vrp]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/vrp/domestic-vrps.md#endpoints
[401-es]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/event-notifications/event-subscription.md#endpoints
[400-es]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/event-notifications/event-subscription.md#endpoints
[311-es]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/event-notifications/event-subscription.md#endpoints
[401-cu]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/event-notifications/callback-url.md#endpoints
[400-cu]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/event-notifications/callback-url.md#endpoints
[311-cu]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/event-notifications/callback-url.md#endpoints
[401-en]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/event-notifications/event-notifications.md#endpoints
[400-en]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/event-notifications/event-notifications.md#endpoints
[311-en]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/event-notifications/event-notifications.md#endpoints
[401-events]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/event-notifications/events.md#endpoints
[400-events]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/event-notifications/events.md#endpoints
[311-events]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/event-notifications/events.md#endpoints
[401-pis-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/pisp/README.md#endpoints
[400-pis-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/pisp/README.md#endpoints
[311-pis-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/pisp/README.md#endpoints
[401-event-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/resources-and-data-models/event-notifications/README.md#endpoints
[400-event-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/resources-and-data-models/event-notifications/README.md#endpoints
[311-event-summary]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/resources-and-data-models/event-notifications/README.md#endpoints
[401-vrp-profile]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/profiles/vrp-profile.md#migration-of-a-consent-to-a-new-version
[400-vrp-profile]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/profiles/vrp-profile.md#migration-of-a-consent-to-a-new-version
[401-polling]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0.1/profiles/aggregated-polling-api-profile.md
[400-polling]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v4.0/profiles/aggregated-polling-api-profile.md
[311-polling]: https://github.com/OpenBankingUK/read-write-api-docs-pub/blob/0fbe63706f2d3c4b7cc4a5a789b9a2eb5764be7a/docs/v3.1.11/profiles/aggregated-polling-api-profile.md
