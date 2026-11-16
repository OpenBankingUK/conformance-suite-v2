# CI security checks and automatic publication

This guide replaces the former DHI risk-approval runbook. Repository code does
not configure GitHub rulesets or environment protection rules; administrators
must coordinate the settings below with the workflow rollout.

## Retained checks

| Check | Source |
|---|---|
| `Check` | CI: full tracked-file secret scan, Ruff including security rules, mypy, offline tests and coverage |
| `Docker Build` | CI: build and smoke test |
| `Image (linux/amd64)` | CI: exact amd64 image, evidence and smoke test |
| `Image (linux/arm64)` | CI: exact arm64 image, evidence and smoke test |
| `code/snyk (Standards)` | Existing external Snyk PR integration |
| CodeQL | GitHub default setup; required code-scanning results at High or higher |

Snyk reports its own PR status. No Actions or promotion workflow invokes Snyk or
requires `SNYK_TOKEN`. GitHub secret scanning/push protection, Dependabot updates,
normal PR reviews, Code Owners, and fork workflow approval policy remain.

Custom Scout, Snyk Container and pip-audit scans, DHI provenance/VEX risk
assessment, exception receipts, promotion rescans, and daily vulnerability-issue
sync are removed. PR source/dependency checks are **not equivalent** to the
removed multi-platform OS/container scans or surveillance of published images.
Existing vulnerability issues and historical reports are not marked fixed or
deleted; automatic updates stop.

## Required PR checks

Update only the affected check requirements, preserving review rules, bypass
actors, strict checks, and other branch protections:

- `main & develop` (`15781582`) and `release branch checks` (`24119519`):
  remove `Vulnerability Scan`; keep the five CI/Snyk statuses above and the
  existing CodeQL rule.
- Preview ruleset (`27127788`, named `preview vulnerability check`):
  replace its sole `Vulnerability Scan` requirement with `code/snyk (Standards)`.
  Confirm Snyk reports for PRs targeting preview branches before relying on it.

Do not leave a required check whose producer has been deleted, invent an
always-successful replacement, or drop Snyk when it is missing. Repair the
integration's branch coverage instead.

## Publishing environments

Keep `preview-release`, `beta-release`, and `ga-release` as credential boundaries:

1. Restrict deployment branches to **only `main`**, with no tag patterns.
2. Remove required reviewers and wait timers. There is no security or
   publication approval after a PR is merged and eligible push CI passes.
3. Preserve each environment's write-capable `DOCKER_ORG_ACCESS_TOKEN`.
   Never move this token into repository or Dependabot secrets.
4. Keep the read-only `DOCKER_ORG_USERNAME` and `DOCKER_ORG_ACCESS_TOKEN`
   credentials available for DHI pulls in repository and Dependabot scopes.

The reusable publisher retains `environment:` and checks out trusted `main`;
GitHub resolves its environment secret in preference to the read-only secret
passed by the caller. It validates the exact CI run, candidate metadata,
archive checksums, source branch/merged PR, and publication digests. It never
rebuilds or executes candidate code with publishing credentials.

The unused `security-exception` environment and stored `SNYK_TOKEN` can remain
inert during migration. They are not used by the new workflows.

## Cutover and verification

1. Save current non-secret ruleset/environment configuration and inspect pending
   runs. Do not release an old queued deployment by removing its reviewer gate.
2. Coordinate removal of the retired required check and retirement of the old
   assessment/rescan automation with the reviewed workflow change on `main`.
   Cancel only confirmed superseded scanner runs.
3. Apply the publication environment restrictions before removing reviewers.
   Re-read settings to verify credentials and unrelated protections were retained.
4. Synchronize active release/preview branches with trusted main pipeline files
   before producing new candidates. `workflow_run` executes from the default
   branch, and promotion requires matching pipeline files.
5. Verify a fresh PR receives Snyk, CodeQL where configured, and its normal CI
   checks without `Vulnerability Scan`. Verify the next eligible push CI starts
   publication without an approval pause.

Manual `promote-preview`, `promote-beta`, and `promote-ga` dispatches remain
recovery/backfill paths from `main`; they no longer take an assessment run ID.
They still require a successful matching push CI run and immutable artifacts.
After successful publication, beta/GA release finalization remains unchanged.
Do not manufacture a version or publish an old candidate just to test rollout.
