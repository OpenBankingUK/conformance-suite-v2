# Custom test values and certification profiles

Participants should be able to experiment with custom values, but certification runs must remain controlled, repeatable, and auditable.

## Product outcome

The tool supports exploratory custom-value runs while making it impossible to accidentally submit a modified test profile as a clean certification run.

## Profile model

| Profile | Purpose | Certification impact |
| --- | --- | --- |
| `certification` | OBL-defined defaults used for certifiable conformance. | Eligible only if all other criteria pass and no unapproved overrides are present. |
| `exploratory` | Participant experiments with values to understand endpoint behaviour. | Not certifiable by default. |
| `approved-override` | Future policy option for Standards-approved override classes. | Open decision; not assumed now. |

## Override examples

Potential override classes include:

- request query parameters such as date ranges, pagination, or filters
- consent permissions or excluded permissions
- request body values for consent/payment resources
- selected auth method where multiple methods are allowed
- environment URLs and endpoint roots
- expected optional/conditional resource availability

Sensitive override values must remain masked wherever the existing masking policy applies.

## Required audit shape

Result JSON and execution logs should be able to identify:

- run profile
- affected requirement ID
- affected manifest step ID
- default profile reference
- override source (`ui`, `api`, `cli`, or config file)
- certification impact
- masked default value if sensitive
- masked custom value if sensitive
- timestamp or run event where the override was accepted

## UI requirements

- Show all deviations from the certification profile before launch.
- Use visual emphasis for certification-impacting changes.
- Require an explicit acknowledgement before launching a non-certifiable exploratory run.
- Show deviations in the run detail/result summary after completion.
- Provide a clear reset-to-default path.

## Certification rules

Working assumption:

- Any unapproved custom value makes `certificationEligibility.eligible` false.
- The OBL-side validator must recompute custom-value impact from trusted report/profile metadata rather than trusting participant-side eligibility.

Open policy question:

- Whether some override classes can remain certifiable after Standards approval.

## Implementation notes

- Prefer a manifest/profile data contract over Python branches for individual values.
- Do not widen the manifest placeholder allow-list to expose secrets or arbitrary config traversal.
- Keep masking mandatory in result JSON, NDJSON logs, API snapshots, browser downloads, and errors.
- Add schema/versioning to any custom-profile report block because certification validators will depend on it.

