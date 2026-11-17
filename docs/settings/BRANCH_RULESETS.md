# GitHub Settings: Branch Rulesets

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Rules → Rulesets

## Required checks after the security-pipeline simplification

| Ruleset | ID | Target branches | Required statuses |
|---|---|---|---|
| `main & develop` | `15781582` | Default branch and `develop` | `Check`, `Docker Build`, `Image (linux/amd64)`, `Image (linux/arm64)`, `code/snyk (Standards)` |
| `release branch checks` | `24119519` | `release/*` | `Check`, `Docker Build`, `Image (linux/amd64)`, `Image (linux/arm64)`, `code/snyk (Standards)` |
| `preview vulnerability check` (name retained) | `27127788` | `preview/*` | `code/snyk (Standards)` |

The main/develop and release rulesets also retain **Require code scanning
results → CodeQL**, with errors and security alerts at High or higher.
Strict required-status policy remains enabled. Snyk is the external integration,
not an Actions job. Preview previously required only `Vulnerability Scan`;
replace that requirement rather than leaving the ruleset empty.

The retired `Vulnerability Scan` must no longer be required on any branch.
See the [configuration and migration guide](../IMAGE_SECURITY_APPROVALS.md)
for coordinating this with deletion of its workflow producer.

## Unchanged review and branch protections

The simplification changes scanner requirements, not the normal review process:

| Setting | Main/develop | Release branches |
|---|---|---|
| Required human approvals | 1 | 1 |
| Code Owner review | Required | Required |
| Dismiss stale reviews | Enabled | Enabled |
| Require last-push approval | Enabled | Disabled |
| Resolve review conversations | Required | Required |
| Force pushes | Blocked | Blocked |
| Allowed merge methods | Squash | Merge, squash, rebase |

Main/develop retains its deletion protection and automatic Copilot review.
Copilot review is advisory and does not count as a human approval.
Preserve all additional review/dismissal restrictions when updating the API
payload; do not reconstruct an entire ruleset from this summary.

Repository administrator and OB SPS Admin team bypass actors on main/develop
and release rulesets are unchanged. Preview has no bypass actors. The migration
must not add a bypass or use one to evade remaining checks.

## Updating safely

Read each live ruleset before modifying it and save the non-secret JSON.
Remove only `Vulnerability Scan` from `required_status_checks`, adding the
existing exact Snyk context for preview. Preserve CI integration IDs, CodeQL,
strictness, conditions, bypasses, and unrelated rules. Re-read before and after
the write to detect concurrent changes and verify the intended difference.

Confirm Snyk emits its status on PRs targeting each relevant branch family.
A missing status remains a configuration problem to resolve, not a reason
to disable the requirement.

Publishing environment reviewers are configured separately, under
**Settings → Environments**. Their removal does not remove PR reviewers.
Publishing environments must retain write credentials and allow only `main`.
