# GitHub Settings: General

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → General
> **Last reviewed**: 30 April 2026

---

## Repository name

| Setting | Value |
|---------|-------|
| Repository name | `conformance-suite-v2` |

**Rationale**: Distinguishes this rebuild from the original `conformance-suite` repository, which remains in the same org as the upstream fork parent.

---

## Default branch

| Setting | Value |
|---------|-------|
| Default branch | `main` |

**Rationale**: `main` is the production-ready branch in our Git Flow model. All pull requests and commits target `main` by default. The `develop` branch is used for integration but is not the default because releases are cut from `main`.

---

## Releases

| Setting | Value |
|---------|-------|
| Release immutability | Off |

**Rationale**: Disabled to allow deleting and re-tagging releases during hotfix scenarios. Our release process involves manual tagging (`v*`) and publishing to Docker Hub on tag push — immutability would block the ability to correct a bad tag without involving GitHub Support.

---

## Social preview

No custom image configured. Uses GitHub's auto-generated open graph image.

---

## Features

| Setting | Value |
|---------|-------|
| Template repository | Off |
| Wikis | **On** (restrict editing to push access) |
| Issues | On |
| Sponsorships | Off |
| Preserve this repository | On |
| Discussions | Off |
| Projects | Off |

### Template repository — Off

This is a working application repository, not a scaffold for generating new repos.

### Wikis — On (restricted)

Enabled for lightweight documentation that doesn't belong in the main codebase (e.g. onboarding guides, operational runbooks). Editing is restricted to users with push access to prevent unauthorised changes given the public visibility of the repo.

### Issues — On

Primary mechanism for tracking work. Used with labels and milestones aligned to our Git Flow branching strategy (`feature/`, `bugfix/`, `release/`, `hotfix/`).

### Sponsorships — Off

This is an Open Banking UK organisational tool, not a community-sponsored project.

### Preserve this repository — On

Opts in to the GitHub Archive Program. No cost; provides long-term archival of the codebase.

### Discussions — Off

Not needed. Technical discussions happen in Issues and PR reviews. Enabling would fragment conversation across two surfaces.

### Projects — Off

Project management is handled at the organisation level, not per-repository. Disabling keeps the repo tab bar clean.

---

## Pull requests

| Setting | Value |
|---------|-------|
| Pull request creation | Collaborators only |

**Rationale**: Restricts PR creation to org members and collaborators. Since this is a public fork, this prevents external drive-by PRs while keeping the repo visible for participants to inspect.

---

## Pull Requests — Merge strategies

| Setting | Value |
|---------|-------|
| Allow merge commits | On (Default message) |
| Allow squash merging | On (Default message) |
| Allow rebase merging | Off |

**Rationale**: Both merge commits and squash merging are enabled at the repository level, but branch rulesets further restrict the allowed methods per branch. The `main` branch ruleset enforces **squash-only** merges (see [BRANCH_RULESETS.md](BRANCH_RULESETS.md)), giving a clean linear history where each commit represents one release or hotfix. Merge commits remain available for branches not covered by restrictive rulesets (e.g. `release/` or `hotfix/` merges back into `develop`). Rebase merging is disabled to avoid rewriting commit history, which can cause problems with our Git Flow model and signed commits.

---

## Pull Requests — Branch updates

| Setting | Value |
|---------|-------|
| Always suggest updating PR branches | On |

**Rationale**: Prompts contributors to keep branches up to date with the base branch before merging, reducing merge conflicts and ensuring CI runs against the latest code.

---

## Pull Requests — Auto-merge

| Setting | Value |
|---------|-------|
| Allow auto-merge | On |

**Rationale**: Safe to enable because merges are gated by required status checks (`lint`, `test`, `security-scan`, `docker-build`, `e2e`) and required approvals via branch protection / rulesets. Auto-merge reduces waiting time once all checks pass.

---

## Pull Requests — Branch cleanup

| Setting | Value |
|---------|-------|
| Automatically delete head branches | On |

**Rationale**: Prevents accumulation of stale feature/bugfix branches after merge. Branches can still be restored if needed. Keeps the branch list manageable.

---

## Commits

| Setting | Value |
|---------|-------|
| Require DCO sign-off | Off |
| Allow commit comments | On |

### DCO sign-off — Off

Not required. This is an internal organisational tool, not an open-source project accepting external contributions under a DCO policy.

### Commit comments — On

Left at default. Allows inline discussion on individual commits, which is useful during code review and post-merge analysis.

---

## Archives

| Setting | Value |
|---------|-------|
| Include Git LFS objects in archives | Off |

**Rationale**: No Git LFS objects are used in this repository. Leaving off avoids unnecessary billing if LFS is ever added accidentally.

---

## Pushes

| Setting | Value |
|---------|-------|
| Limit pushes | On — **5** branches/tags per push |

**Rationale**: Safety net against accidental bulk operations (e.g. a misconfigured script deleting or force-pushing many branches). The limit of 5 is generous enough for normal Git Flow work (pushing a feature branch + tag) while blocking potentially destructive bulk pushes.

---

## Issues

| Setting | Value |
|---------|-------|
| Auto-close linked issues on merge | On |

**Rationale**: Automatically closes issues when a PR with `Fixes #N` or `Closes #N` is merged. Keeps the issue tracker in sync with delivered work without manual housekeeping.

---

## Danger Zone

No changes made. Defaults preserved:

- **Visibility**: Public fork (cannot be changed while in fork network)
- **Branch protection rules**: Enabled (not disabled)
- **Fork network**: Still connected to `OpenBankingUK/conformance-suite`
- **Archive status**: Not archived
- **Deletion**: Not applicable
