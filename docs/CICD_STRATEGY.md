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
| Require status checks to pass before merging | Not currently configured |
| Recommended required status checks | `Check`, `Docker Build`, and the external Snyk check |
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
| Require status checks to pass before merging | Not currently configured |
| Recommended required status checks | `Check`, `Docker Build`, and the external Snyk check |
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

Snyk is the company's primary security scanning platform. The repository is
linked directly via the **Snyk portal** for dependency and code checks. The
candidate-image workflow also runs the blocking Snyk container scan and a
blocking Docker Scout scan:

| Scan Type | Trigger | Blocks Merge |
|---|---|---|
| Snyk Open Source (dependencies) | Every PR | `high` + `critical` severity |
| Snyk Code (SAST) | Every PR | `high` + `critical` severity |
| Snyk Container (candidate image) | Eligible candidate build | `high` + `critical` severity, subject only to the two `.snyk` policy entries below |
| Docker Scout (candidate image) | Eligible candidate build | `high` + `critical` severity after applying Docker's signed OpenVEX statement |

Snyk remains required for application and image coverage. Docker Scout is
required because it consumes Docker Hardened Images' signed OpenVEX statement,
allowing the gate to distinguish assessed, mitigated base-image findings from
genuine vulnerabilities. Both steps fail closed on scanner/authentication
errors and findings outside their documented exception/VEX scope.

The root `.snyk` policy ignores only `SNYK-DEBIAN13-ZLIB-19520500`
(CVE-2026-85091, `zlib1g`) and `SNYK-DEBIAN13-EXPAT-19964593`
(CVE-2026-93990, `libexpat1`). Each reason cites Docker's signed OpenVEX
statement for `dhi.io/python:3.14-debian13@sha256:e1a5bd571d9585d7eb80c8278b54b69a0e0bf5a9bb2b1424b9e4576374df6659`,
with status `not_affected` and justification
`inline_mitigations_already_exist`. The entries are scoped to `'*'` and expire
at `2026-12-28T00:00:00.000Z`; do not add broader IDs, packages, or paths.
Before expiry, Security must recheck the signed VEX status and current base
image, then remove or renew only through a reviewed PR with supporting
evidence. Candidate CI validates the Dockerfile's runtime stage against that
exact DHI digest before building or running either image scanner. Trusted
promotion repeats the check against `Dockerfile` read directly from the source
commit and rejects any mismatch; it also compares the validation script and
`.snyk` against `main`, so a candidate cannot bypass the guard or widen the
policy.

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
| `DOCKER_ORG_USERNAME` | Organisation Docker account used to pull Docker Hardened Images, authenticate Docker Scout, and publish the approved image |
| `DOCKER_ORG_ACCESS_TOKEN` (repository secret) | **Read-only** organisation access token used by CI jobs to pull Docker Hardened Images from `dhi.io` and to authenticate the Docker Scout CLI (`DOCKER_SCOUT_HUB_USER`/`DOCKER_SCOUT_HUB_PASSWORD`) |
| `DOCKER_ORG_ACCESS_TOKEN` (environment secret on `preview-release`, `beta-release`, `ga-release`) | **Write-capable** organisation access token scoped to push on `openbanking/conformance-suite-v2`; overrides the read-only repository secret only inside the approved promotion job |
| `SNYK_TOKEN` | Token used by candidate-image jobs for the blocking container scan |

Before enabling promotion, a repository administrator must create the Docker
Hub repository `openbanking/conformance-suite-v2` (public) and configure two
organisation access tokens under the same secret name:

1. A **read-only** token as the repository-level `DOCKER_ORG_ACCESS_TOKEN`,
   used by every CI run (PRs included) to pull DHI base images and run
   Docker Scout.
2. A **write-capable** token (image push on `openbanking/conformance-suite-v2`
   only) as an **environment secret** named `DOCKER_ORG_ACCESS_TOKEN` on each
   of `preview-release`, `beta-release`, and `ga-release`.

The reusable promotion job declares `environment:`, so GitHub resolves the
environment secret in preference to the repository secret passed by the
caller. Push credentials are therefore only released after a required
reviewer approves the Environment gate; candidate builds never publish
images.

There are currently no repository-level variables required by CI: the
supported pytest suite is fully offline and does not target a live model bank
or Ozone environment.

The `preview-release`, `beta-release`, and `ga-release` GitHub Environments
gate image publication. Each Environment must require human reviewers and
must restrict deployment branches to `main`; promotion workflows must also be
dispatched from `main`.

---

## 4. CI/CD Pipeline Design

### 4.1 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ Trigger: Pull Request or push (main, develop, preview/*,         │
│ release/*, hotfix/*)                                            │
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

Pushes to `preview/*`, `release/*`, and `main` additionally build candidate
artifacts when the source tree contains the hardened Docker contract (a DHI
base, explicit UID/GID `65532:65532`, and `docker/entrypoint.py`). Each
`linux/amd64` and `linux/arm64` image is built once, smoke-tested, blocked on
Snyk and Docker Scout `high`/`critical` findings, accompanied by an SPDX SBOM,
and uploaded as a GitHub Actions artifact. Candidate jobs have read-only
repository permissions and never push an image.

This Docker-contract check is a deliberate bootstrap compatibility gate:
today's `main` Docker build and smoke test remain unchanged, so installing the
trusted promotion code does not require importing the MVP Docker/application
implementation. A `release/2.0.0` branch containing that hardened Docker
implementation automatically produces candidates with versions such as
`2.0.0-beta.N`; once the same implementation reaches `main`, GA candidates
are produced there too.

### 4.2 Workflow Files

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to protected branches | Canonical checks, unchanged baseline Docker smoke test, and gated multi-architecture candidate artifacts |
| `.github/workflows/auto-promote.yml` | Successful push CI completion on `main`, `release/**`, or `preview/**` | Resolve eligible candidates and automatically start their gated promotion |
| `.github/workflows/promote-preview.yml` | Manual dispatch from `main` | Recovery/backfill path for a `preview/*` candidate |
| `.github/workflows/promote-beta.yml` | Manual dispatch from `main` | Recovery/backfill path for a `release/X.Y.Z` beta candidate |
| `.github/workflows/promote-ga.yml` | Manual dispatch from `main` | Recovery/backfill path for a tagged `main` candidate |
| `.github/workflows/_promote-image.yml` | Called by automatic and manual promotion workflows | Trusted validation and exact-artifact Docker Hub publication implementation |
| `.github/workflows/_finalize-ga-release.yml` | Called after successful GA publication | Create the GA Git tag at the source SHA and open the develop merge-back PR |

Promotion locates the successful push CI run for the exact source SHA and
branch, downloads its immutable artifacts, verifies archive checksums,
revalidates release metadata using scripts checked out from `main`, and
confirms the source SHA belongs to the requested branch and a merged,
approved pull request. Automatic promotion uses the exact CI run that triggered
it; manual recovery dispatches locate the matching run. Candidate CI and
release-script files must exactly match the trusted copies on `main`; pipeline
changes therefore land on `main` before release branches consume them. Only
then does the Environment-gated job receive Docker Hub credentials; it stages
the platform images under SHA-specific internal tags and assembles the
already-tested images without rebuilding. Promotion is serialized to prevent
tag races, rejects an existing immutable version tag discovered through the
Docker Hub tags API, and attests the published
multi-architecture manifest with provenance and both platform SBOMs.

For each successful eligible push, `auto-promote.yml` confirms the CI run
uploaded a promotion manifest, reads only `pyproject.toml` from its source
commit, and checks branch/channel compatibility and existing Docker Hub tags. Runs
without a candidate artifact, with a mismatched channel, or for an already
published version are skipped with a notice and never create a pending
Environment approval. An eligible run starts the matching promotion
automatically and pauses only at its required Environment reviewer gate. After
a successful GA publication, the shared finalizer creates `vX.Y.Z` at the
promoted source SHA if needed and opens the main-to-develop PR; it never merges
that PR automatically.

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

Publication starts automatically after successful CI for eligible `main`,
`release/**`, and `preview/**` pushes; it never rebuilds from the registry.
The only routine human action is approving the matching GitHub Environment
deployment, which is required before Docker Hub credentials are used. Manual
dispatch of the `promote-*` workflows is reserved for recovery or backfill.

**Tag formats:**

| Tag | Example | Type |
|---|---|---|
| `vX.Y.Z` | `v1.2.0` | Stable release |
| `X.Y.Z-beta.N` in `pyproject.toml` | `2.0.0-beta.1` | Beta image |

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
   - CI runs automatically on every push

3. Open PR: release/1.2.0 → main
   - Full CI gates enforced (`make check`, Docker build and health check)
   - Requires 2 approvals (Copilot + human)

4. Merge into main (squash or merge commit). Successful `main` push CI
   produces the GA candidate, then automatic promotion starts and waits for
   approval of the `ga-release` Environment deployment.

5. Approve the `ga-release` Environment deployment. The workflow validates and
   publishes the exact candidate artifacts to Docker Hub as `X.Y.Z` and `latest`,
   with provenance and SBOM attestations.

6. After publication, the workflow creates `vX.Y.Z` at the promoted source
   commit if it is missing and opens the main-to-develop PR. Review and merge
   that PR; it is never merged automatically.

Manual **Promote GA image** dispatch is available from `main` only for
recovery/backfill.
```

### 5.3 Beta Releases

Beta releases allow pre-release images to be distributed before a final stable tag.

```
1. Set `[project].version` to `X.Y.Z-beta.N` on the matching
   `release/X.Y.Z` branch and merge the change through an approved PR.
2. Wait for that exact branch SHA's push CI run to produce both candidate
   artifacts and the promotion manifest. Automatic promotion then starts and
   waits for approval of the `beta-release` Environment deployment.
3. Approve the Environment deployment. The workflow publishes the exact
   artifacts as immutable
   `X.Y.Z-beta.N`; it does not create or move `latest`.
4. Increment `N` in a new approved change for each subsequent beta.

Manual **Promote beta image** dispatch is available from `main` only for
recovery/backfill.
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

3. After merge, successful `main` candidate CI and approval of the
   `ga-release` Environment publish the exact image to Docker Hub. The
   finalizer creates the `vX.Y.Z` tag at the promoted source commit.

4. Also merge/cherry-pick into develop:
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
- [ ] Clone the repository and run `uv sync --frozen --no-install-project` to install dependencies
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
