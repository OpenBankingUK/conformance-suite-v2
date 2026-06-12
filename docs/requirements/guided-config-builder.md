# Guided config builder revamp

The guided config builder should make common conformance runs possible without pasting large JSON payloads, while still allowing advanced/custom environments.

## Product outcome

Participants can choose a known environment, standard/API/version, suite, auth method, and required credentials through structured controls. The UI generates valid config, previews the test plan, and blocks unsupported combinations before launch.

## Modes

| Mode | Purpose |
| --- | --- |
| Preset model-bank mode | Fast path for known Ozone/model-bank environments and supported auth combinations. |
| Custom environment mode | Structured input for participant ASPSP environments without raw JSON-first editing. |
| Advanced JSON mode | Escape hatch for expert users and automation parity. |

## Preset dimensions

- environment/model bank
- standard and spec version
- API family
- suite
- auth method
- PSU mode
- token endpoint client auth method
- mTLS requirement
- FAPI signing requirement
- resource base URL
- redirect URI
- optional/conditional test groups

## Field reduction strategy

Only show fields required by the selected suite and auth bundle:

- discovery URL and environment are prefilled for known model banks
- OAuth client ID appears only for OAuth suites
- redirect URI appears only for PSU flows
- resource base URL appears only for protected-resource suites
- FAPI signing fields appear only when request objects, client assertions, or detached JWS are needed
- TLS cert/key fields appear only when mTLS or custom CA bundle is needed
- open banking intent ID appears only for starter flows that require a pre-existing consent

## Environment capability metadata

The builder needs structured capability metadata for known environments and a declaration path for custom environments.

The implemented evidence blocks use the names `authMetadata` for auth-bundle inventory and `environmentCapabilities` for environment support decisions.

Example capability dimensions:

- supported spec versions
- supported API families
- supported suites
- supported PSU modes
- supported token endpoint auth methods
- whether mTLS is required
- whether mobile QR auth is required or supported
- redirect URI restrictions
- model-bank known base URLs
- known unsupported combinations

## Validation behaviour

- Preview should fail before launch when required fields are missing.
- Unsupported suite/auth/environment combinations should produce specific errors.
- Custom environment mode should distinguish "unknown capability" warnings from hard blockers.
- Generated config should still pass `parse_model_bank_config`.
- Browser flow should keep using Django forms and CSRF protection.

## Relationship to visual plan builder

The guided config builder chooses the environment, suite, and auth profile. The visual plan builder then presents the resulting plan tree and lets the participant opt into or out of applicable coverage.

The two surfaces should share the same underlying capability and suite metadata so the UI does not drift.

## First implementation slices

1. Define environment capability metadata shape.
2. Add model-bank capability presets.
3. Replace current guided fields with progressive field groups.
4. Add auth-method preset selection.
5. Add compatibility validation and launch blockers.
6. Keep advanced JSON mode as a visible escape hatch.
