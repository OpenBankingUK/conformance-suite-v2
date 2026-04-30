# CI/CD Strategy & Repository Controls

## Document Control

| Field | Value |
|---|---|
| Project | Open Banking UK Conformance Test Tool |
| Scope | Repository governance, branching strategy, pipeline design |
| Status | Approved |
| Date | April 2026 |

---

## Table of Contents

- [CI/CD Strategy \& Repository Controls](#cicd-strategy--repository-controls)
  - [Document Control](#document-control)
  - [Table of Contents](#table-of-contents)
  - [1. Branching Strategy: Git Flow](#1-branching-strategy-git-flow)
    - [1.1 Permanent Branches](#11-permanent-branches)
    - [1.2 Transient Branches](#12-transient-branches)
    - [1.3 Naming Conventions](#13-naming-conventions)
    - [1.4 Git Flow Diagram](#14-git-flow-diagram)
  - [2. Branch Protection Rules](#2-branch-protection-rules)
    - [2.1 `main` Branch](#21-main-branch)
    - [2.2 `develop` Branch](#22-develop-branch)
    - [2.3 `release/**` Branches](#23-release-branches)
    - [2.4 Configuring GitHub Copilot as a Required Reviewer](#24-configuring-github-copilot-as-a-required-reviewer)
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

## 1. Branching Strategy: Git Flow

The project follows the **Git Flow** branching model with two permanent branches and three transient branch types.

### 1.1 Permanent Branches

| Branch | Purpose | Direct Push Allowed |
|---|---|---|
| `main` | Represents production-ready, released code | **No** |
| `develop` | Integration branch; all feature work merges here | **No** |

### 1.2 Transient Branches

| Prefix | Branch From | Merges Into | Purpose |
|---|---|---|---|
| `feature/` | `develop` | `develop` | New features and enhancements |
| `bugfix/` | `develop` | `develop` | Non-critical bug fixes |
| `release/` | `develop` | `main` + `develop` | Release stabilisation and prep |
| `hotfix/` | `main` | `main` + `develop` | Critical production fixes |

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

### 1.4 Git Flow Diagram

```
main     ────────●──────────────────────────────────────●──────
                 ↑ initial commit                        ↑ merge from release/1.0.0
                 │                                       │
develop  ────────●──────●──────●──────●──────────────────●──────
                        ↑      ↑      ↑                  ↑
feature/42 ─────────────┘      │      │            merged
feature/55 ────────────────────┘      │
release/1.0.0 ────────────────────────────────────●──── (bumps version, fixes)
hotfix/107 ─────────────────────────── (branches from main, merges back to main + develop)
```

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
| Require status checks to pass before merging | **Enabled** |
| Required status checks | `lint`, `test`, `security-scan`, `docker-build`, `e2e` |
| Require branches to be up to date before merging | **Enabled** |
| Require conversation resolution before merging | **Enabled** |
| Require linear history | **Enabled** (merge squash or rebase only) |
| Allow administrators to bypass | **Enabled** — repository admins may override in exceptional circumstances; see [Section 5.4](#54-release-exception-process) |
| Restrict who can push to matching branches | Standards Team leads only |

### 2.2 `develop` Branch

| Rule | Setting |
|---|---|
| Require a pull request before merging | **Enabled** |
| Required approving reviews | **1** (human) |
| Dismiss stale pull request approvals when new commits are pushed | **Enabled** |
| Require review from Code Owners | **Enabled** |
| Require status checks to pass before merging | **Enabled** |
| Required status checks | `lint`, `test`, `security-scan`, `docker-build`, `e2e` |
| Require branches to be up to date before merging | **Enabled** |
| Require conversation resolution before merging | **Enabled** |
| Allow administrators to bypass | **Enabled** — repository admins may override in exceptional circumstances; see [Section 5.4](#54-release-exception-process) |

### 2.3 `release/**` Branches

| Rule | Setting |
|---|---|
| Require a pull request before merging (into `main`) | **Enabled** (via `main` rules) |
| CI runs automatically on push | **Enabled** |

### 2.4 Configuring GitHub Copilot as a Required Reviewer

GitHub Copilot code review is enabled as follows:

1. **GitHub Repository Settings → Copilot → Code review**
   - Enable "Automatic review requests" for pull requests targeting `main` and `develop`
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

The `security-scan` status check (posted by Snyk's GitHub integration) is a required check on both `main` and `develop`. PRs **must not** be merged if this check is failing.

If the team encounters a security issue they are uncertain how to resolve, the Security team should be consulted. Code containing known `high` or `critical` vulnerabilities must not be merged.

> **Developer tooling**: All developers should install the **Snyk IDE extension** (VS Code or JetBrains) to catch security issues locally before raising a PR. See [Section 10](#10-onboarding-checklist-for-new-developers).

### 3.3 Required Repository Secrets

The following secrets must be configured in **Repository Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; no manual setup |

The following optional variables may be configured as repository-level variables (not secrets):

| Variable | Description |
|---|---|
| `E2E_MODEL_BANK_URL` | Default model bank base URL for E2E tests in CI |

---

## 4. CI/CD Pipeline Design

### 4.1 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ Trigger: Pull Request (→ develop, main, release/*, hotfix/*)     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
      [lint]              [security-scan]       (parallel)
   ruff check           via Snyk portal —
   ruff format          posts status check
   mypy                 to the PR
          │                    │
          └────────────┬───────┘
                       ▼
                    [test]
               pytest (not e2e)
               coverage ≥ 80%
                       │
                       ▼
                [docker-build]
               build image
                       │
                       ▼
                    [e2e]
               testcontainers
               model bank tests
               result file assert
               (all PRs → develop + main)
```

### 4.2 Workflow Files

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to protected branches | Lint, test, Docker build |
| `.github/workflows/e2e.yml` | PR → `develop` or `main`, push to `release/**`, manual | End-to-end conformance tests |

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

### 5.2 Standard Release (Git Flow)

```
1. Create release branch from develop:
   git checkout develop && git checkout -b release/1.2.0

2. Stabilise on release branch (version bump, changelog, final fixes)
   - CI + E2E run automatically on every push

3. Open PR: release/1.2.0 → main
   - Full CI + E2E gates enforced
   - Requires 2 approvals (Copilot + human)

4. Merge into main (squash or merge commit)

5. An admin tags the merge commit on main and pushes the tag:
   git tag -a v1.2.0 -m "Release 1.2.0"
   git push origin v1.2.0

6. An admin creates the GitHub Release manually via the GitHub UI using the tag.

7. Docker Hub detects the tag and publishes the image automatically.

8. Merge main back into develop to capture the version bump:
   git checkout develop
   git merge main
   git push origin develop
```

### 5.3 Beta Releases

Beta releases allow pre-release images to be distributed before a final stable tag.

```
1. On develop or a release/ branch, when a build is ready for beta testing:

2. An admin pushes a beta tag:
   git tag -a v1.2.0-beta1 -m "Beta 1 for 1.2.0"
   git push origin v1.2.0-beta1

3. An admin creates the GitHub pre-release manually via the GitHub UI using the tag.

4. Docker Hub detects the beta tag and publishes the pre-release image automatically.

5. Subsequent betas increment the suffix: v1.2.0-beta2, v1.2.0-beta3, etc.
```

### 5.4 Release Exception Process

In exceptional circumstances — for example, when E2E tests fail because of a bug in the Ozone model bank rather than in our code — a repository administrator may merge despite failing status checks.

**When this is appropriate:**
- Tests are failing due to a confirmed external provider defect (e.g. Ozone model bank behaving incorrectly)
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
   - PR against main: requires CI + E2E pass + 2 approvals

3. An admin tags on merge:
   git tag -a v1.1.1 -m "Hotfix 1.1.1"
   git push origin v1.1.1

4. Docker Hub detects the tag and publishes automatically.

5. Also merge/cherry-pick into develop:
   git checkout develop && git merge hotfix/107-fix-auth-header
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
| Dev/prod separation | `uv sync --no-dev` for production Docker builds |

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

- Branch protection rules prevent force-pushes and history rewriting on `main` and `develop`
- All merges to `main` require at least one human sign-off, providing a human accountability chain for every production change
- Pull request and review history is immutable on GitHub
- Any admin bypass of branch protection rules must be documented in the PR (see [Section 5.4](#54-release-exception-process))

---

## 10. Onboarding Checklist for New Developers

Before a new team member can contribute, the following must be completed by a repository admin:

- [ ] Add to the `OpenBankingUK/ob-sps-developers` GitHub team
- [ ] Confirm GitHub Copilot licence is assigned
- [ ] Install the **Snyk IDE extension** for local security scanning (available for [VS Code](https://marketplace.visualstudio.com/items?itemName=snyk-security.snyk-vulnerability-scanner) and JetBrains IDEs) — recommended by the Security team
- [ ] Clone the repository and run `uv sync` to install dependencies
- [ ] Read this document, [REQUIREMENTS.md](REQUIREMENTS.md), and [TESTING_STRATEGY.md](TESTING_STRATEGY.md)
- [ ] Complete a first PR against `develop` to verify the pipeline works end-to-end

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
