# Visual plan builder design

The current plan builder already validates config/manifest input, previews rows, supports selection, and launches runs. The next UX step is to make test selection visual and structured enough for participants who should not need to edit raw manifests or JSON for common flows.

## Product outcome

Participants can select a test plan through a tree of API capabilities, understand mandatory/optional/conditional coverage, see auth requirements, and launch compatible runs with minimal free-text input.

## Proposed tree model

```text
OB Read/Write
  v4.0.1
    AIS
      Accounts
        GET /accounts
          ReadAccountsDetail
            Auth bundle: ais-detail-consent
              accounts-list
          ReadAccountsBasic
            Auth bundle: ais-basic-consent
              accounts-basic-list
      Transactions
        GET /accounts/{AccountId}/transactions
          ReadTransactionsDetail
          ReadTransactionsBasic
      Optional and conditional resources
        Beneficiaries
        Direct Debits
        Offers
```

Tree nodes should support:

- `standard`
- `specVersion`
- `api`
- `resourceGroup`
- `endpoint`
- `contentVariant`
- `permissionSet`
- `authBundle`
- `step`

## Selection behaviour

- Selecting a parent selects all selectable children unless a child is incompatible with the current environment/auth choice.
- Deselecting a parent deselects all children, but mandatory deselection must show certification impact.
- Optional rows are deselected by default.
- Conditional rows should show the condition and why the participant might select them.
- The preview must distinguish "deselected" from runtime `skipped`.

## Data needed by the UI

The current manifest and `PlanPreview` model already expose some of this data, including step ID, name, kind, group, phase, mandatory, optional, and auth inventory. The tree view will need additional structured metadata from manifests or companion coverage/profile artefacts:

- resource group
- endpoint family
- content/permission variant
- conditional rationale
- auth bundle requirement
- default selected profile
- certification impact
- prior-FCS or Standards requirement IDs

## Guided config builder changes

- Replace raw JSON-first workflows with structured presets for common model-bank paths.
- Keep an advanced/custom environment mode.
- Prompt for OAuth, resource base URL, TLS, and FAPI signing only when the selected suite/auth method needs them.
- Add environment capability validation before preview and launch.
- Make auth method selection explicit: manual PSU, headless PSU, `private_key_jwt`, `tls_client_auth`/mTLS, and later mobile QR code auth.

## Validation and safety

- Browser posts must continue through Django forms and CSRF-protected UI views.
- API automation can keep the JSON contract; visual UX should not remove the programmatic path.
- Any new launch blocker must be tested through plan-builder and UI view tests.
- Raw secrets, certificate paths, signing material, tokens, request objects, client assertions, and detached JWS values must not become manifest placeholders or browser-persisted clear text.

## First implementation slices

1. Add tree metadata to plan preview rows without changing selection behaviour.
2. Render a read-only grouped tree in the UI.
3. Add branch selection/deselection.
4. Add environment/auth compatibility blockers.
5. Replace common raw text fields with presets while preserving advanced custom mode.

