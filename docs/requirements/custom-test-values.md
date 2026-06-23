# Custom test values and certification gates

Participants should be able to experiment with custom values, but certification runs must remain controlled, repeatable, and auditable.

## Implemented architecture

The shipped model splits custom data across three layers:

1. **Suite manifests** own `testValues.baseline`, `generatedKeys`, and `allowedCustomKeys`.
2. **Participant Configuration** provides `testData.values` for environment- or ASPSP-specific deltas.
3. **RunConfiguration** is the compiled execution artifact produced from the manifest, selected run plan, and participant test data.

`RunConfigurationCompiler` normalises same-as-baseline values away, preserves only effective baseline deltas, and reports missing required keys when neither source provides them.

| Layer | Purpose | Certification impact |
| --- | --- | --- |
| `testValues.baseline` | OBL-defined default values used for certifiable conformance. | Certifiable only when coverage also passes and no baseline deltas exist. |
| `testData.values` | Participant-specific deltas for a target ASPSP or environment. | Allowed for exploratory runs; only baseline-delta keys are counted. |
| `RunConfiguration` | Compiled execution view used by the engine. | Normalises same-as-baseline values away and blocks launch when required keys are missing. |

## Override examples

Potential custom-data classes include:

- request query parameters such as date ranges, pagination, or filters
- consent permissions or excluded permissions
- request body values for consent/payment resources
- selected auth method where multiple methods are allowed
- environment URLs and endpoint roots
- expected optional/conditional resource availability

Sensitive custom values must remain masked wherever the existing masking policy applies.

## Required audit shape

Result JSON and execution logs should be able to identify:

- manifest baseline source
- affected requirement ID
- affected manifest step ID
- baseline-delta keys
- participant source (`ui`, `api`, `cli`, or config file`)
- consumed test-value key
- certification impact
- masked baseline value if sensitive
- masked custom value if sensitive
- timestamp or run event where the override was accepted

## UI requirements

- Show all deviations from the suite baseline before launch.
- Use visual emphasis for certification-impacting changes.
- Require an explicit acknowledgement before launching a non-certifiable exploratory run.
- Show deviations in the run detail/result summary after completion.
- Provide a clear reset-to-default path.

## Certification rules

Working assumption:

- Any baseline delta makes `certificationEligibility.eligible` false unless Standards explicitly approves that value class.
- The OBL-side validator must recompute custom-value impact from trusted report metadata rather than trusting participant-side eligibility.

Open policy question:

- Whether some custom-value classes can remain certifiable after Standards approval.

## Implementation notes

- Prefer a manifest/testData contract over Python branches for individual values.
- Do not widen the manifest placeholder allow-list to expose secrets or arbitrary config traversal.
- Keep masking mandatory in result JSON, NDJSON logs, API snapshots, browser downloads, and errors.
- Add schema/versioning to any custom-data report block because certification validators will depend on it.
