# Visual plan builder design

The plan builder already validates config/manifest input, previews rows, supports selection, and launches runs. The visual tree selection UI makes that existing plan structure easier to explore without requiring participants to edit raw manifests or JSON for common flows.

## Product outcome

Participants can select a test plan through a tree of API capabilities, understand mandatory/optional/conditional coverage, see auth requirements, and launch compatible runs with minimal free-text input.

## Implemented tree model

```text
OB Read/Write
  v4.0.1
    AIS
      Accounts
        GET /accounts
          detail
            Auth bundle: ais-detail-consent
              accounts-list
          basic
            Auth bundle: ais-basic-consent
              accounts-basic-list
      Transactions
        GET /accounts/{AccountId}/transactions
          detail
          basic
      Setup and discovery
      Consent setup
      PSU authorisation
      Token exchange
```

The hierarchy is derived from:

- `SuiteMetadata` for `standard`, `specVersion`, `api`, and `profile`
- OpenAPI paths and tags for `resourceGroup` and `endpoint`
- manifest step analysis for non-resource groups
- auth bundle permissions for variant labels

Tree nodes follow this hierarchy:

- `standard`
- `specVersion`
- `api`
- `resourceGroup`
- `endpoint`
- `variant`
- `step`

The UI must not take tree metadata from participant config. Config remains the source for execution inputs such as URLs, credentials, and environment capability, but the tree structure itself is derived from bundled suite metadata and manifests.

Non-resource steps are grouped under generated sections:

- Setup and discovery
- Consent setup
- PSU authorisation
- Token exchange

## Selection behaviour

- Selecting a parent selects all selectable children unless a child is incompatible with the current environment/auth choice.
- Deselecting a parent deselects all children, but mandatory deselection must show certification impact.
- Optional rows are deselected by default.
- Conditional rows should show the condition and why the participant might select them.
- The preview must distinguish "deselected" from runtime `skipped`.

## Data needed by the UI

The current manifest and `PlanPreview` model already expose some of this data, including step ID, name, kind, group, phase, mandatory, optional, and auth inventory. The tree view now relies on derived metadata from `conformance/openapi_plan_metadata.py` rather than participant-supplied tree hints:

- resource group
- endpoint family
- variant labels
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
