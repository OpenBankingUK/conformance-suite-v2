# Multi-auth model

Multi-auth is required because conformance plans can need separate consent/token paths for different permission sets, content variants, and auth methods. The v4.0.1 AIS target should model this explicitly rather than treating "the access token" as a single global run property.

## Product outcome

Participants can see which auth bundles are required, understand why multiple consents/tokens are needed, and select compatible auth methods before launch.

## Auth bundle concept

An auth bundle is the unit that connects:

- consent creation step
- PSU authorisation step
- token exchange step
- token endpoint client auth method
- required OAuth scopes
- required Open Banking permissions
- excluded permissions
- consuming resource steps
- environment capability requirements

## Example AIS bundles

| Bundle | Permissions | Consuming steps | Purpose |
| --- | --- | --- | --- |
| `ais-detail` | `ReadAccountsDetail`, `ReadBalances`, `ReadTransactionsDetail` | account detail, balances, detail transactions | Detail/default certification path. |
| `ais-basic-transactions` | `ReadAccountsBasic`, `ReadTransactionsBasic`, `ReadTransactionsCredits`, `ReadTransactionsDebits` | transaction basic checks | Basic-only content boundary. |
| `ais-negative-permission` | excludes required resource permissions | 401/403 negative checks | Prior-FCS parity for invalid/incorrect tokens. |
| `ais-client-credentials` | client credentials token only | selected protected-resource checks | Prior-FCS client-credentials-token variants. |
| `ais-no-token` | no token | selected protected-resource checks | Prior-FCS no-token variants. |

## Auth method dimensions

| Dimension | Values |
| --- | --- |
| PSU mode | `manual`, `headless`, later `mobile_qr` |
| Token endpoint client auth | `private_key_jwt`, `tls_client_auth` |
| Transport | server TLS, optional mTLS client cert/key |
| Request object | generated PS256 JAR from FAPI signing config |
| Detached JWS | generated PS256 detached JWS for signed request bodies |

## Environment compatibility

The guided UI should not let users launch impossible combinations. Environment capability metadata should eventually answer:

- Does the environment support manual PSU?
- Does it support headless PSU?
- Does it require or allow mobile QR auth?
- Which token endpoint auth methods are registered for the client?
- Is mTLS required and are cert/key paths configured?
- Which redirect URI patterns are accepted?
- Which API family/spec versions are available?
- Which resource base URL should be used?

Custom environments can still be supported by requiring participants to declare these capabilities or by showing warnings where support cannot be verified.

## UI requirements

- Show auth bundles in plan preview and tree view.
- Show selected steps that consume each bundle.
- Show required config fields for each bundle and auth method.
- Show compatibility blockers before launch.
- Avoid exposing raw tokens, request objects, client assertions, private keys, certificate paths, or arbitrary config traversal.

## Execution and result requirements

- Runtime context should bind tokens to their producing auth bundle or token step.
- Placeholder resolution must not allow cross-group/token leakage beyond intended step references.
- Result JSON should expose safe auth-bundle metadata, not secrets.
- Execution logs should record auth decisions and bundle identifiers with masked credential values.
- Certification validator should be able to detect whether mandatory rows used required auth/content variants.

## First implementation slices

1. Formalise auth bundle metadata in manifests or companion suite coverage artefacts.
2. Carry auth bundle IDs through plan preview and selected-step metadata.
3. Render auth bundle inventory in the visual tree.
4. Add environment capability validation for manual/headless and token auth methods.
5. Add prior-FCS negative/no-token/client-credentials variants as separate agent slices.

