# DHI image refresh and security approvals

This runbook describes the GitHub configuration required by the trusted DHI
image assessment and promotion workflows. These settings are manual; repository
code does not create or modify environments, reviewers, secrets, or rulesets.

## What the workflows enforce

- Dependabot proposes the digest-pinned DHI builder/runtime pair together,
  daily, for `main` and the configured active release branch. The candidate
  image family remains fixed to Python 3.14 / Debian 13.
- After successful CI, `security-assessment.yml` validates the CI workflow
  identity, run attempt, PR association and merge parents. It scans the exact
  uploaded amd64 and arm64 images with trusted policy code from the default
  branch. The check is `Vulnerability Scan`, attached to the PR head commit
  (or push commit).
- Findings with fixes available at any severity are blocked. Unfixed
  high/critical/unknown findings and explicitly missing signed SLSA v1 DHI
  provenance may be accepted only by a fresh GitHub Environment approval.
  Signature, digest, subject, registry, scanner, authentication, and parsing
  errors always block. Medium/low unfixed findings are reported.
- Promotion requires a receipt bound to the exact successful CI run and
  candidate SHA, then performs a fresh vulnerability rescan. If that rescan
  finds an eligible risk, it requests another fresh approval before the
  existing release publication Environment gate. Fixable findings remain
  unoverrideable.
- Candidate-controlled exception files are not used by either trusted
  assessment. The source-controlled exception list remains for explicit
  persistent policy exceptions and historical rescans.

## Configure the `security-exception` Environment

In **Repository Settings → Environments**, create an environment named
`security-exception` exactly.

1. Add the small, designated security-risk approver set under **Required
   reviewers**. Only people who can make and record vulnerability risk
   acceptance should be selected.
2. Enable **Prevent administrators from bypassing configured protection
   rules** where the repository plan exposes this setting. Do not add a
   wait timer; the security approval is an explicit human decision, not a
   time delay.
3. Restrict deployment branches to `main`. The assessment workflow runs from
   the trusted default branch; promotion workflows are also required to be
   dispatched from `main`.
4. Self-approval is permitted by the current policy decision. If the
   approver is also the submitter, the workflow records a risk acceptance,
   not independent review.
5. Do not add Docker Hub publishing credentials to this environment.
   `security-exception` gates risk acceptance only; the existing
   `preview-release`, `beta-release`, and `ga-release` environments remain the
   independent publication approval and credential boundary.

When an approval is requested, inspect both platform report artifacts in the
assessment run. The reviewer must enter the exact comment shown in that run's
summary: `APPROVE <full candidate image commit SHA>`. The workflow checks the
actual Actions run approval record, environment name, and comment. A new
assessment or promotion run requires a new approval; approvals are not reused.

## Require status checks

In **Settings → Rules → Rulesets**, update each applicable ruleset for `main`,
`develop`, and `release/*`. GitHub only offers check names it has already
seen, so search for each name and add it explicitly:

| Check | Source |
|---|---|
| `Check` | CI: secret scan, lint, types, tests, coverage |
| `Docker Build` | CI: build and smoke test |
| `Image (linux/amd64)` | CI: exact amd64 candidate image |
| `Image (linux/arm64)` | CI: exact arm64 candidate image |
| `Vulnerability Scan` | Trusted assessment, including any approved risk exception |
| `code/snyk (Standards)` | Snyk integration |

- Require branches to be up to date before merging.
- Separately, **Require code scanning results → CodeQL** with security alerts
  at **High or higher** once CodeQL default setup is enabled. CodeQL source
  analysis complements, and does not replace, the image vulnerability gate.

The `Vulnerability Scan` check is emitted by a trusted `workflow_run`
workflow; it is not the former scanner job inside PR-controlled CI. Fork pull
requests cannot receive repository scan credentials and need to be assessed
from a same-repository branch before they can satisfy this check.

**Bootstrap:** `workflow_run` workflows execute from the default branch, so
the PR that introduces `security-assessment.yml` cannot receive a
`Vulnerability Scan` check from it. Merging that PR requires a recorded admin
bypass; subsequent PRs are assessed normally.

Bypass applies to the whole ruleset, including required checks. To merge solo
work without bypassing automated checks, keep checks and branch safety in a
ruleset with no bypass actors, and put human-review rules in a separate
ruleset whose bypass is limited to maintainers **for pull requests only**.
Copilot code review is advisory and does not count as an approval.

## Verify Actions and secrets

In **Settings → Actions → General**, retain the least-privilege default
`GITHUB_TOKEN` permission and confirm workflows can grant their explicitly
declared `checks: write`, `security-events: write`, `actions: read`, and
`contents: read` permissions. The assessment requires `SNYK_TOKEN` and the
read-only Docker organization credentials as repository Actions secrets.
The read-only `DOCKER_ORG_USERNAME` and `DOCKER_ORG_ACCESS_TOKEN` are also
needed as Dependabot secrets for private DHI pulls. Never place the
write-capable Docker token in repository or Dependabot secrets; keep it only
in the existing publication Environments.

## Manual promotion

The manual `promote-preview`, `promote-beta`, and `promote-ga` workflows now
require `security_assessment_run_id`. Supply the Actions run ID of a successful
`Trusted security assessment` run whose receipt matches the requested CI
source SHA. Use the run ID from the Actions run URL, not the PR number or CI
run ID. Promotion still validates the CI artifacts, rescans current findings,
and pauses for the existing publication approval.

## Enablement checklist

- [x] Create and protect `security-exception`.
- [x] Confirm `SNYK_TOKEN` and read-only DHI secrets are in the correct scopes.
- [ ] Merge the trusted assessment workflow and observe `Vulnerability Scan`
      on a same-repository PR.
- [x] Require `Vulnerability Scan` in the relevant branch rulesets.
- [ ] Confirm the existing publication Environments still require reviewers
      and restrict deployments to `main`.
- [ ] Make a manual promotion only with the successful assessment run ID for
      the exact candidate commit.
