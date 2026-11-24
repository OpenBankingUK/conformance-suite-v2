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

## Review and branch protections

The simplification changes scanner requirements, not the normal review process:

| Setting | Main/develop | Release branches |
|---|---|---|
| Required human approvals | 1 | 1 |
| Code Owner review | Required | Required |
| Dismiss stale reviews | Enabled | Enabled |
| Require last-push approval | Enabled | Disabled |
| Resolve review conversations | Required | Required |
| Force pushes | Blocked | Blocked |
| Allowed merge methods | Merge only | Merge only |

Main/develop retains its deletion protection and automatic Copilot review.
Copilot review is advisory and does not count as a human approval.
Preserve all additional review/dismissal restrictions when updating the API
payload; do not reconstruct an entire ruleset from this summary.

Repository administrator and OB SPS Admin team bypass actors on main/develop
and release rulesets are unchanged. Preview has no bypass actors. The migration
must not add a bypass or use one to evade remaining checks.

## Merge strategy

Merge commits are the only allowed method for every PR, including feature and
Dependabot PRs. Repository [merge settings](GENERAL.md#pull-requests--merge-strategies)
disable squash and rebase merging, while the main/develop and release rulesets
set the pull-request rule's `allowed_merge_methods` to `["merge"]`.
Required linear history remains disabled because it would reject merge commits.
Preview branches inherit the repository-wide merge-method restriction without
adding or removing rules from their existing ruleset.

This permanent policy preserves ancestry when synchronising long-lived
branches, such as `release/*` into `main` and `main` into `develop`. Squashing a
long-lived branch can cause later PRs to repeat changes or conflicts because
the source commits are not recorded as ancestors of the target. Merge commits
avoid that ancestry loss, but cannot prevent conflicts caused by new changes.

These documents record the settings held in GitHub; editing them does not apply
configuration automatically. Change the repository settings and rulesets through
GitHub's settings UI or API, preserving all review, CI and security requirements.

## Updating safely

Read each live ruleset before modifying it and save the non-secret JSON.
Remove only `Vulnerability Scan` from `required_status_checks`, adding the
existing exact Snyk context for preview. Preserve CI integration IDs, CodeQL,
strictness, conditions, bypasses, and unrelated rules. Re-read before and after
the write to detect concurrent changes and verify the intended difference.

For merge-policy updates, change only `allowed_merge_methods` in the existing
pull-request rules and the repository's `allow_merge_commit`,
`allow_squash_merge` and `allow_rebase_merge` settings. Allow `merge` in the
rulesets before disabling squash at repository level, so the two settings do
not temporarily conflict.

Confirm Snyk emits its status on PRs targeting each relevant branch family.
A missing status remains a configuration problem to resolve, not a reason
to disable the requirement.

Publishing environment reviewers are configured separately, under
**Settings → Environments**. Their removal does not remove PR reviewers.
Publishing environments must retain write credentials and allow only `main`.
