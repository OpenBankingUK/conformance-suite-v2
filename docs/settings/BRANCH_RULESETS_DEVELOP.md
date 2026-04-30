# GitHub Settings: Branch Rulesets — develop

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Rules → Rulesets
> **Ruleset ID**: 15781582
> **Last reviewed**: 30 April 2026

---

## Ruleset: "develop"

| Setting | Value |
|---------|-------|
| Name | **develop** |
| Enforcement status | **Active** |
| Target | **Branch** |

---

## Target branches

| Type | Pattern |
|------|---------|
| Include | `refs/heads/develop` |
| Exclude | _(none)_ |

**Rationale**: The ruleset targets only the `develop` branch — the primary integration branch for day-to-day development in the git flow model. All feature, bugfix, and release branches merge into `develop` before being promoted to `main`.

---

## Bypass list

| Actor | Type | Bypass mode |
|-------|------|-------------|
| Repository admin | Role | Always |
| OB SPS Admin | Team | Always |

**Rationale**: Identical to the `main` ruleset. Repository admins and the OB SPS Admin team can bypass rules in exceptional circumstances (e.g. emergency fixes, repository maintenance). The "Always" mode means bypass is permitted without requiring a pull request.

---

## Enabled rules

### Restrict deletions

| Setting | Value |
|---------|-------|
| Enabled | ✅ |

**Rationale**: Prevents deletion of the `develop` branch. Only bypass actors can delete matching refs. Essential protection for the primary integration branch.

### Block force pushes

| Setting | Value |
|---------|-------|
| Enabled | ✅ |

**Rationale**: Prevents rewriting history on `develop` via force push. Force pushes destroy audit trails and can cause data loss for collaborators. Non-negotiable for a regulated financial services repository.

### Require a pull request before merging

| Setting | Value |
|---------|-------|
| Enabled | ✅ |
| Required approvals | **1** |
| Dismiss stale reviews on push | ✅ Enabled |
| Require approval of the most recent reviewable push | ✅ Enabled |
| Require conversation resolution before merging | ✅ Enabled |
| Require review from Code Owners | ❌ Disabled |
| Allowed merge methods | **Squash only** |
| Require review from specific teams | _(none configured)_ |
| Restrict dismissals to authorised actors only | ❌ Disabled |
| Ignore approvals from contributors | ❌ Disabled |

**Rationale**:

- **1 required approval**: Ensures every change to `develop` is reviewed by at least one other developer. Balances review rigour with team velocity for a small team.
- **Dismiss stale reviews on push**: If new commits are pushed after approval, the approval is invalidated. Prevents approved PRs from being silently modified before merge.
- **Require last push approval**: The person who pushed the most recent commit cannot be the one who approves the PR. Enforces genuine peer review — no self-approving.
- **Conversation resolution**: All review comments must be resolved before merge. Prevents unaddressed feedback from slipping through.
- **Code Owners disabled**: Will be enabled after a `CODEOWNERS` file is committed to the repository (see TODO below).
- **Squash only**: Enforces a clean, linear history on `develop`. Each PR becomes a single commit, making `git log` readable and `git bisect` effective. Eliminates merge commits and messy rebase histories.

### Automatically request Copilot code review

| Setting | Value |
|---------|-------|
| Enabled | ✅ |
| Review new pushes | ✅ Enabled |
| Review draft pull requests | ✅ Enabled |

**Rationale**: Copilot automatically reviews every PR targeting `develop`, including drafts and subsequent pushes. This provides an AI-powered first pass before human review, catching common issues early. Note: Copilot review is advisory only — it does not produce a blocking approval or rejection status.

---

## Disabled rules

The following rules are available but intentionally **not enabled**:

| Rule | Reason not enabled |
|------|--------------------|
| Restrict creations | Not needed — branch creation is not a risk for `develop` (it already exists) |
| Restrict updates | Not needed — updates are governed by the pull request requirement |
| Require linear history | Redundant — squash-only merge method already guarantees linear history |
| Require merge queue | Not needed at current team size; can be reconsidered as contributor count grows |
| Require deployments to succeed | No deployment environments configured yet |
| Require signed commits | Not enforced at this stage; may revisit based on organisation security policy |
| Require status checks to pass | Deferred until CI workflows are committed and producing status checks (see TODO below) |
| Require code scanning results | Deferred until CodeQL or equivalent is configured in CI |
| Require code quality results | Not yet configured |
| Restrict commit metadata | Not needed at this stage |
| Restrict branch names | Not needed at this stage |

---

## Post-setup TODOs

- [ ] **Add required status checks** — Once the first CI workflow is committed and has run at least once, enable the "Require status checks to pass" rule and add the relevant check contexts (e.g. `ci / lint`, `ci / test`, `ci / type-check`)
- [ ] **Enable Code Owners review** — After committing a `CODEOWNERS` file to the repository, enable `require_code_owner_review` in the pull request rule to enforce ownership-based approvals

---

## Relationship to the "main" ruleset

This ruleset is intentionally configured identically to the ["main" branch ruleset](BRANCH_RULESETS.md) (ID: 15778634), with the sole difference being the target branch (`refs/heads/develop` instead of `~DEFAULT_BRANCH`). Both long-lived branches receive the same level of protection to maintain consistency across the git flow model.
