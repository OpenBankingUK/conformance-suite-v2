# CI/CD Strategy & Repository Controls

## Document Control

| Field | Value |
|---|---|
| Project | Open Banking UK Conformance Test Tool |
| Scope | Repository governance, branching strategy, pipeline design |
| Status | Approved |
| Date | April 2026 (branching model revised — see [Section 1](#1-branching-strategy-trunk-based-with-staged-release-branches)) |

---

## Table of Contents

- [CI/CD Strategy \& Repository Controls](#cicd-strategy--repository-controls)
  - [Document Control](#document-control)
  - [Table of Contents](#table-of-contents)
  - [1. Branching Strategy: Trunk-Based with Staged Release Branches](#1-branching-strategy-trunk-based-with-staged-release-branches)
    - [1.1 Permanent Branches](#11-permanent-branches)
    - [1.2 Transient Branches](#12-transient-branches)
    - [1.3 Naming Conventions](#13-naming-conventions)
    - [1.4 Branching Diagram](#14-branching-diagram)
    - [1.5 Moving Work Between Planned Releases](#15-moving-work-between-planned-releases)
  - [2. Branch Protection Rules](#2-branch-protection-rules)
    - [2.1 `main` Branch](#21-main-branch)
    - [2.2 `release/**` Branches](#22-release-branches)
    - [2.3 Configuring GitHub Copilot as a Required Reviewer](#23-configuring-github-copilot-as-a-required-reviewer)
  - [3. Repository Security Controls](#3-repository-security-controls)
    - [3.1 GitHub Security Features](#31-github-security-features)
    - [3.2 Snyk Integration](#32-snyk-integration)
    - [3.3 Required Repository Secrets](#33-required-repository-secrets)
  - [4. CI/CD Pipeline Design](#4-cicd-pipeline-design)
    - [4.1 Workflow Overview](#41-workflow-overview)
    - [4.2 Workflow Files](#42-workflow-files)
    - [4.3 Concurrency Control](#43-concurrency-control)
  - [5. Release Process](#5-release-process)
    - [5.1 Tagging and Release Strategy](#51-tagging-and-release-strategy)
    - [5.2 Standard Release (Git Flow)](#52-standard-release-git-flow)
    - [5.3 Beta Releases](#53-beta-releases)
    - [5.4 Release Exception Process](#54-release-exception-process)
    - [5.5 Hotfix Process](#55-hotfix-process)
  - [6. Build Status Badge](#6-build-status-badge)
  - [7. Dependency Management Controls](#7-dependency-management-controls)
  - [8. Code Ownership](#8-code-ownership)
  - [9. Audit \& Compliance](#9-audit--compliance)
  - [10. Onboarding Checklist for New Developers](#10-onboarding-checklist-for-new-developers)
  - [11. Docker Hardened Images](#11-docker-hardened-images)
  - [12. Pull Request Template](#12-pull-request-template)

---

## 1. Branching Strategy: Trunk-Based with Staged Release Branches

The project only ever supports **one released version at a time** — participants
are told to use the latest release or a stated minimum version, not a matrix of
supported versions. Historically the team also needed to **stage more than one
planned release in parallel** (e.g. two independent fixes prepared at the same
time but deliberately shipped in separate releases, with work reassigned
between them as priorities changed). The branching model below is trunk-based
to match the single-supported-version reality, while still supporting that
staging need through **short-lived, independently cuttable `release/**`
branches** rather than a permanent Git Flow `develop` branch.

> **Migration note**: this repository previously documented a Git Flow model
> with a permanent `develop` branch. `develop` was never created and CI never
> ran against it in practice, so this section replaces that model outright. If
> a `develop` branch or its branch ruleset (see
> [BRANCH_RULESETS_DEVELOP.md](settings/BRANCH_RULESETS_DEVELOP.md)) exist in
> GitHub settings, they should be deleted as part of adopting this model.

### 1.1 Permanent Branches

| Branch | Purpose | Direct Push Allowed |
|---|---|---|
| `main` | Trunk. Always green and mergeable, but merging here means "done and available," **not** "will ship next" — actual release contents are composed on `release/**` branches. | **No** |

### 1.2 Transient Branches

| Prefix | Branch From | Merges Into | Purpose |
|---|---|---|---|
| `feature/` | `main` | `main` | New features and enhancements |
| `bugfix/` | `main` | `main` | Non-critical bug fixes |
| `release/` | `main` (cut when release planning starts; **any number can be open at once**) | `main` (on ship) | Stages the exact set of merged commits intended for a specific, plannable release |
| `hotfix/` | `main` | `main` | Critical fixes to the currently released version |

### 1.3 Naming Conventions

```
feature/<issue-number>-<short-description>
bugfix/<issue-number>-<short-description>
release/<semver>                               # e.g. release/1.2.0
hotfix/<issue-number>-<short-description>
```

Examples:
```
feature/42-add-token-endpoint-tests
bugfix/91-fix-result-json-encoding
release/1.2.0
hotfix/107-fix-auth-header-parsing
```

### 1.4 Branching Diagram

```
main       ───●──●──●──●──●──●──●──────────●──(tag v1.2.0)──●──●──
              │  │  │  │  │  │  │          ▲
feature/42 ───┘  │  │  │  │  │  │          │ merge release/1.2.0 back into main
feature/55 ──────┘  │  │  │  │  │          │
feature/60 ─────────┘  │  │  │  │          │
                        │  │  │  │
release/1.2.0 ──────────┴──┴──┴──●cherry-pick──●cherry-pick──●─── (stabilise, tag, merge)
release/1.3.0 (opened in parallel, staged separately) ───────────

hotfix/107 ─── branches from main, merges back to main only (single supported version)
```

Every `feature/`/`bugfix/` PR targets `main` first and passes the same CI gate
regardless of which release (if any) it is ultimately staged for.

### 1.5 Moving Work Between Planned Releases

When two or more releases are being staged concurrently:

1. Once a change is merged to `main`, cherry-pick it into whichever
   `release/x.y.z` branch it's currently planned for.
2. If priorities change before that release ships, **revert the cherry-pick**
   on the original release branch and **cherry-pick it into the new target
   release branch** instead. The commit on `main` never moves — only which
   release branch carries it changes.
3. Once a release branch is tagged and merged into `main`, its contents are
   final. Moving a *shipped* change to a different release is no longer a
   re-staging exercise — it becomes a `hotfix/` against the newly released
   version, or scope for the next release.

This keeps `main` as a single, simple source of truth (one place to test, one
`git log` to read) while still letting the team plan release contents
independently of when a fix or feature happens to be finished.

---

## 2. Branch Protection Rules

These rules **must** be configured in **GitHub → Repository Settings → Branches** for each protected branch. They cannot be bypassed by repository administrators.

### 2.1 `main` Branch

| Rule | Setting |
|---|---|
| Require a pull request before merging | **Enabled** |
| Required approving reviews | **2** (1 Copilot + 1 human) |
| Dismiss stale pull request approvals when new commits are pushed | **Enabled** |
| Require review from Code Owners | **Enabled** |
| Require status checks to pass before merging | Not currently configured |
| Recommended required status checks | `Check`, `Docker Build`, and the external Snyk check |
| Require branches to be up to date before merging | **Enabled** |
| Require conversation resolution before merging | **Enabled** |
| Require linear history | **Enabled** (merge squash or rebase only) |
| Allow administrators to bypass | **Enabled** — repository admins may override in exceptional circumstances; see [Section 5.4](#54-release-exception-process) |
| Restrict who can push to matching branches | Standards Team leads only |

### 2.2 `release/**` Branches

| Rule | Setting |
|---|---|
| Require a pull request before merging (into `main`) | **Enabled** (via `main` rules) |
| CI runs automatically on push | **Enabled** |

Any number of `release/**` branches may be open at once (see [Section 1.5](#15-moving-work-between-planned-releases)); each is protected identically and independently.

### 2.3 Configuring GitHub Copilot as a Required Reviewer

GitHub Copilot code review is enabled as follows:

1. **GitHub Repository Settings → Copilot → Code review**
   - Enable "Automatic review requests" for pull requests targeting `main`
2. In the **branch protection rules** for `main`:
   - Set required approving reviews to **2**
   - The Copilot review counts as one of the required reviews when it approves
3. Add `@github-copilot` to `CODEOWNERS` for all paths (see [CODEOWNERS](../. github/CODEOWNERS))

> **Note**: GitHub Copilot code review requires GitHub Enterprise or Copilot Business/Enterprise licences with the Copilot Code Review feature enabled for the organisation.

---

## 3. Repository Security Controls

### 3.1 GitHub Security Features

The following GitHub security features **must** be enabled at the organisation and repository level:

| Feature | Status |
|---|---|
| Dependency graph | Enabled |
| Dependabot alerts | Enabled |
| Dependabot security updates | Enabled |
| Secret scanning | Enabled |
| Push protection (secret scanning) | **Enabled** — blocks pushes containing detected secrets |
| GitHub Advanced Security | Enabled |

### 3.2 Snyk Integration

Snyk is the primary security scanning platform. The repository is linked directly via the **Snyk portal** — no `SNYK_TOKEN` is required in GitHub Actions. Snyk runs checks automatically when a PR is opened or updated and posts the result as a GitHub status check.

| Scan Type | Trigger | Blocks Merge |
|---|---|---|
| Snyk Open Source (dependencies) | Every PR | `high` + `critical` severity |
| Snyk Code (SAST) | Every PR | `high` + `critical` severity |
| Snyk Container (Docker image) | Every PR | `high` + `critical` severity |

The status check posted by Snyk's GitHub integration is separate from
`.github/workflows/ci.yml`. PRs must not be merged when it reports a high or
critical vulnerability. To enforce this mechanically, add its exact status
context to the repository ruleset.

If the team encounters a security issue they are uncertain how to resolve, the Security team should be consulted. Code containing known `high` or `critical` vulnerabilities must not be merged.

> **Developer tooling**: All developers should install the **Snyk IDE extension** (VS Code or JetBrains) to catch security issues locally before raising a PR. See [Section 10](#10-onboarding-checklist-for-new-developers).

### 3.3 Required Repository Secrets

The following secrets must be configured in **Repository Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; no manual setup |

There are currently no repository-level variables required by CI: the
supported pytest suite is fully offline and does not target a live model bank
or Ozone environment.

---

## 4. CI/CD Pipeline Design

### 4.1 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ Trigger: Pull Request or push (main, release/*, hotfix/*)        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
           [Check]                      [Docker Build]
     make check with full                build image
     tracked-file secret scan           start container
     ruff + mypy                         probe /health/
     unit + component tests
     aggregate coverage

The external Snyk integration reports its own PR status independently.
```

### 4.2 Workflow Files

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to protected branches | Canonical `make check` gate plus parallel Docker build and health check |

### 4.3 Concurrency Control

All workflows use `concurrency` groups to cancel in-progress runs when new commits are pushed to the same branch or PR. This avoids queue pile-up from rapid successive commits.

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

---

## 5. Release Process

### 5.1 Tagging and Release Strategy

Once a PR has been merged into `main`, a repository administrator:

1. Pushes a semver tag from `main`
2. Creates the GitHub Release manually via the GitHub UI, writing a description based on `CHANGELOG.md`

Docker Hub detects the tag automatically and publishes the image — no further action is needed.

**Tag formats:**

| Tag | Example | Type |
|---|---|---|
| `vX.Y.Z` | `v1.2.0` | Stable release |
| `vX.Y.Z-betaN` | `v1.2.0-beta1` | Beta pre-release |

```bash
git tag -a v1.2.0 -m "Release 1.2.0"
git push origin v1.2.0
```

---

### 5.2 Standard Release (Trunk-Based, Staged Release Branch)

```
1. Create a release branch from main, when release planning starts —
   any number of release branches may be open at once:
   git checkout main && git checkout -b release/1.2.0

2. Stage the intended contents by cherry-picking merged commits from main
   (see Section 1.5 for moving work between planned releases), plus any
   version bump / changelog commits made directly on the release branch
   - CI runs automatically on every push

3. Open PR: release/1.2.0 → main
   - Full CI gates enforced (`make check`, Docker build and health check)
   - Requires 2 approvals (Copilot + human)

4. Merge into main (squash or merge commit)

5. An admin tags the merge commit on main and pushes the tag:
   git tag -a v1.2.0 -m "Release 1.2.0"
   git push origin v1.2.0

6. An admin creates the GitHub Release manually via the GitHub UI using the tag.

7. Docker Hub detects the tag and publishes the image automatically.
```

### 5.3 Beta Releases

Beta releases allow pre-release images to be distributed before a final stable tag.

```
1. On a release/ branch, when a build is ready for beta testing:

2. An admin pushes a beta tag:
   git tag -a v1.2.0-beta1 -m "Beta 1 for 1.2.0"
   git push origin v1.2.0-beta1

3. An admin creates the GitHub pre-release manually via the GitHub UI using the tag.

4. Docker Hub detects the beta tag and publishes the pre-release image automatically.

5. Subsequent betas increment the suffix: v1.2.0-beta2, v1.2.0-beta3, etc.
```

### 5.4 Release Exception Process

In exceptional circumstances — for example, when a CI test failure stems from a
confirmed external dependency defect rather than a bug in this codebase — a
repository administrator may merge despite failing status checks.

**When this is appropriate:**
- Tests are failing due to a confirmed external dependency defect (e.g. a
  third-party package or base image regression) unrelated to this codebase
- The team has verified that our implementation and test logic are correct
- Waiting for the external fix would unreasonably block a release

**How to invoke:**
- The PR author documents in the PR description (or a comment) exactly why the tests are failing and confirms the failure is on the external provider's side
- A repository administrator reviews and agrees, then merges using GitHub's bypass option ("Merge without waiting for requirements to be met")

**This process must never be used to merge code that has genuine defects or security vulnerabilities.**

### 5.5 Hotfix Process

```
1. Branch from main:
   git checkout main && git checkout -b hotfix/107-fix-auth-header

2. Fix, commit, push
   - PR against main: requires CI pass + 2 approvals

3. An admin tags on merge:
   git tag -a v1.1.1 -m "Hotfix 1.1.1"
   git push origin v1.1.1

4. Docker Hub detects the tag and publishes automatically.

5. If a release branch is currently staging a future version, cherry-pick the
   hotfix into it too so the next release also carries the fix:
   git checkout release/1.2.0 && git cherry-pick <hotfix-commit>
```

---

## 6. Build Status Badge

The `main` branch CI status badge is embedded in [README.md](../README.md):

```markdown
![CI](https://github.com/OpenBankingUK/ob-conformance-tool/actions/workflows/ci.yml/badge.svg?branch=main)
```

The badge reflects the latest CI run on the `main` branch. A red badge means the last merge to `main` broke CI — this should be treated as a P1 issue and resolved immediately.

---

## 7. Dependency Management Controls

| Control | Mechanism |
|---|---|
| Pinned lockfile | `uv.lock` committed to repo, `uv sync --frozen` in CI |
| Dependency updates | Dependabot raises PRs weekly for security updates |
| Snyk monitoring | Continuous monitoring of production dependency tree |
| No loose version ranges | `pyproject.toml` specifies minimum versions; `uv.lock` pins exact versions |
| Dev/prod separation | `uv sync --no-dev --no-install-project` for production Docker builds |

---

## 8. Code Ownership

Defined in [CODEOWNERS](../.github/CODEOWNERS). The entire repository is owned by the single Standards team. All pull requests automatically request review from the team.

```
# .github/CODEOWNERS
* @OpenBankingUK/ob-sps-developers
```

---

## 9. Audit & Compliance

Given the regulatory context of Open Banking UK:

- Branch protection rules prevent force-pushes and history rewriting on `main` and every `release/**` branch
- All merges to `main` require at least one human sign-off, providing a human accountability chain for every production change
- Pull request and review history is immutable on GitHub
- Any admin bypass of branch protection rules must be documented in the PR (see [Section 5.4](#54-release-exception-process))

---

## 10. Onboarding Checklist for New Developers

Before a new team member can contribute, the following must be completed by a repository admin:

- [ ] Add to the `OpenBankingUK/ob-sps-developers` GitHub team
- [ ] Confirm GitHub Copilot licence is assigned
- [ ] Install the **Snyk IDE extension** for local security scanning (available for [VS Code](https://marketplace.visualstudio.com/items?itemName=snyk-security.snyk-vulnerability-scanner) and JetBrains IDEs) — recommended by the Security team
- [ ] Clone the repository and run `uv sync --frozen --no-install-project` to install dependencies
- [ ] Read this document, [REQUIREMENTS.md](REQUIREMENTS.md), and [TESTING_STRATEGY.md](TESTING_STRATEGY.md)
- [ ] Complete a first PR against `main` to verify the pipeline works end-to-end

---

## 11. Docker Hardened Images

The project uses **Docker Hardened Images (DHI)** as the base image. DHI has no impact on the GitHub Actions pipeline.

The Dockerfile must use a multi-stage build: all package installation and build steps happen in a build stage; the final runtime stage is minimal with no shell or package manager. The application runs as a non-root user (UID 65532) and must bind to port 1025 or above.

---

## 12. Pull Request Template

A lightweight PR template is provided at `.github/pull_request_template.md` and is applied automatically to all new pull requests. It is intentionally minimal — just enough to prompt the author on the key points without adding friction:

```markdown
## What does this PR do?

<!-- One-sentence summary -->

## Checklist

- [ ] Tests added or updated
- [ ] CHANGELOG.md updated (for feat/fix/hotfix/security changes)
- [ ] No hardcoded secrets or credentials
```

The template is a prompt, not a gate. Authors should complete what is relevant and skip sections that do not apply.
