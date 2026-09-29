# GitHub Settings: Branch Rulesets

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Rules → Rulesets
> **Ruleset ID**: 15778634
> **Last reviewed**: 6 May 2026

---

## Ruleset: "main"

| Setting | Value |
|---------|-------|
| Name | **main** |
| Enforcement status | **Active** |
| Target | **Branch** |

---

## Target branches

| Type | Pattern |
|------|---------|
| Include | `~DEFAULT_BRANCH` (i.e. `main`) |
| Exclude | _(none)_ |

**Rationale**: The ruleset targets only the default branch (`main`). `main` is
the stable GA branch, so these protections prevent direct pushes, force pushes,
and deletions while enforcing release review requirements.

---

## Bypass list

| Actor | Type | Bypass mode |
|-------|------|-------------|
| Repository admin | Role | Always |
| OB SPS Admin | Team | Always |

**Rationale**: Repository admins and the OB SPS Admin team can bypass rules in exceptional circumstances (e.g. emergency hotfixes, repository maintenance). The "Always" mode means bypass is permitted without requiring a pull request. This is restricted to the smallest set of privileged actors necessary.

---

## Enabled rules

### Restrict deletions

| Setting | Value |
|---------|-------|
| Enabled | ✅ |

**Rationale**: Prevents deletion of the `main` branch. Only bypass actors can
delete matching refs. Essential protection for the stable release branch.

### Block force pushes

| Setting | Value |
|---------|-------|
| Enabled | ✅ |

**Rationale**: Prevents rewriting history on `main` via force push. Force pushes destroy audit trails and can cause data loss for collaborators. Non-negotiable for a regulated financial services repository.

### Require a pull request before merging

| Setting | Value |
|---------|-------|
| Enabled | ✅ |
| Required approvals | **2** |
| Dismiss stale reviews on push | ✅ Enabled |
| Require approval of the most recent reviewable push | ✅ Enabled |
| Require conversation resolution before merging | ✅ Enabled |
| Require review from Code Owners | ✅ Enabled |
| Allowed merge methods | **Squash only** |
| Require review from specific teams | _(none configured)_ |
| Restrict dismissals to authorised actors only | ❌ Disabled |
| Ignore approvals from contributors | ❌ Disabled |

**Rationale**:

- **2 required approvals**: Requires Copilot review plus human review before a
  change reaches the stable release branch.
- **Dismiss stale reviews on push**: If new commits are pushed after approval, the approval is invalidated. Prevents approved PRs from being silently modified before merge.
- **Require last push approval**: The person who pushed the most recent commit cannot be the one who approves the PR. Enforces genuine peer review — no self-approving.
- **Conversation resolution**: All review comments must be resolved before merge. Prevents unaddressed feedback from slipping through.
- **Code Owners enabled**: Ensures the Standards team reviews changes in owned paths.
- **Squash only**: Enforces a clean, linear history on `main`. Each PR becomes a single commit, making `git log` readable and `git bisect` effective. Eliminates merge commits and messy rebase histories.

### Automatically request Copilot code review

| Setting | Value |
|---------|-------|
| Enabled | ✅ |
| Review new pushes | ✅ Enabled |
| Review draft pull requests | ✅ Enabled |

**Rationale**: Copilot automatically reviews every PR targeting `main`, including drafts and subsequent pushes. This provides an AI-powered first pass before human review, catching common issues early. Note: Copilot review is advisory only — it does not produce a blocking approval or rejection status.

---

## Disabled rules

The following rules are available but intentionally **not enabled**:

| Rule | Reason not enabled |
|------|--------------------|
| Restrict creations | Not needed — branch creation is not a risk for `main` (it already exists) |
| Restrict updates | Not needed — updates are governed by the pull request requirement |
| Require linear history | Redundant — squash-only merge method already guarantees linear history |
| Require merge queue | Not needed at current team size; can be reconsidered as contributor count grows |
| Require deployments to succeed | No deployment environments configured yet |
| Require signed commits | Not enforced at this stage; may revisit based on organisation security policy |
| Require status checks to pass | Enabled for `Check`, candidate image quality gate where applicable, and Snyk status checks; add `Vulnerability Scan` after the release merge-back (see TODO below) |
| Require code scanning results | Deferred until CodeQL or equivalent is configured in CI |
| Require code quality results | Not yet configured |
| Restrict commit metadata | Not needed at this stage |
| Restrict branch names | Not natively available for restricting _source_ branches merging into `main`; requires a CI workflow approach (see TODO below) |

---

## Post-setup TODOs

- [ ] **Verify required status check names** after the release workflows are created and add their exact GitHub check contexts to this ruleset
- [ ] **Add branch/channel validation CI job** — Create a CI workflow job that checks `[project].version`, source branch, target branch, and intended image tag are compatible
- [ ] **Enable required status checks and Code Owners review (vulnerability gate)** — apply once `release/2.0.0` (hardened DHI image) has been merged back to `main` and `main` has merged into every `release/*` branch; before then the `Vulnerability Scan` check fails on `main`'s legacy Alpine image, or never reports on release branches without the gate, which would block every PR. Apply to both the `main & develop` ruleset (id `15781582`) and the `release branch checks` ruleset (id `24119519`), saving `gh api` output before and after:
  - `required_status_checks`: `Check`, `Docker Build`, `Vulnerability Scan`, `code/snyk (Standards)` (strict).
  - `pull_request`: `required_approving_review_count` ≥ 1 and `require_code_owner_review: true` (CODEOWNERS now protects `security/`, `.github/`, `scripts/`, `Dockerfile`, `pyproject.toml`, `uv.lock`).
  - `main & develop`: remove the **Always** bypass actors (use "pull request only" bypass at most), so exceptions and the gate cannot be merged around.
  - Example: `gh api repos/OpenBankingUK/conformance-suite-v2/rulesets/24119519 > before.json`, edit `rules`, then `gh api -X PUT repos/OpenBankingUK/conformance-suite-v2/rulesets/24119519 --input after.json`.
- [ ] **Add branch naming validation CI job** — Create a CI workflow job that checks the source branch name on PRs to `main` matches the allowed pattern (`feature/`, `bugfix/`, `release/`, `hotfix/`). GitHub rulesets cannot natively restrict which _source_ branches may merge into a target branch
- [ ] **Investigate Copilot review status check** — Determine whether Copilot code review produces a check status that could be added as a required status check to strengthen the review gate
