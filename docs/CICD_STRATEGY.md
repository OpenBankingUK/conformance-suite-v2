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
    - [3.2 Vulnerability Scanning and the PR Gate](#32-vulnerability-scanning-and-the-pr-gate)
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
| Require status checks to pass before merging | Pending enablement after the `release/2.0.0` merge-back; see [branch rulesets](settings/BRANCH_RULESETS.md) |
| Required status checks to enable | `Check`, `Docker Build`, `Vulnerability Scan`, and `code/snyk (Standards)` |
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
| Require status checks to pass before merging | Pending enablement after the `release/2.0.0` merge-back; see [branch rulesets](settings/BRANCH_RULESETS.md) |
| Required status checks to enable | `Check`, `Docker Build`, `Vulnerability Scan`, and `code/snyk (Standards)` |
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

### 3.2 Vulnerability Scanning and the PR Gate

Snyk is the company's primary security scanning platform. The repository is
linked via the **Snyk portal** for dependency and code checks, and CI runs a
consolidated container vulnerability gate on **every pull request and push**:

| Scan Type | Trigger | Blocks |
|---|---|---|
| Snyk Open Source (dependencies) | Every PR (Snyk portal, `code/snyk (Standards)`) | `high` + `critical` severity |
| Snyk Code (SAST) | Every PR (Snyk portal) | `high` + `critical` severity |
| **`Vulnerability Scan`** (Docker Scout + Snyk Container `--app-vulns` + pip-audit of `uv.lock`) | Every PR and push, per platform | See policy below |
| Promotion re-scan | Every promotion, **before** the Environment approval gate | Same policy, current data, `main`'s exceptions |
| Scheduled re-scan (`security-rescan.yml`) | Daily: `main`, `release/*`, and every published Docker Hub tag | Opens/updates one issue per vulnerability |

**Policy** (`scripts/vulnerability_gate.py`):

- Any finding with a fixed version available fails, **whatever its severity**.
- Unfixed `critical`/`high` findings fail. Findings with no severity from any
  scanner are treated as `high` (fail closed).
- Unfixed `medium`/`low` findings are reported but do not fail.
- When published, Docker's signed OpenVEX for the exact DHI runtime base
  (author must be `@docker.com`; see `scripts/validate_docker_base.py`)
  suppresses only base-image OS findings with the exact package version. It
  never applies to application packages under `/app/`. If Docker Scout
  explicitly reports that the image has no VEX attestations, scanning
  continues without VEX suppressions.
- Scanner, authentication, signature or parsing errors fail closed. Snyk runs
  from an empty directory, so a `.snyk` file cannot silently ignore findings.

The job summary, GitHub annotations and SARIF all report **every** finding:
blocking findings as errors, informational open findings as warnings, and
VEX-assessed or accepted findings as notices/suppressed SARIF results. The
required check is the aggregate `Vulnerability Scan` job.

**Trust model**: PR image builds run in an untrusted job that checks out
the PR head, builds the Docker archive, and performs only non-secret local
verification; it does not receive `SNYK_TOKEN` or run the vulnerability gate.
The secret-bearing scan runs later in a clean dependent job. That job checks
out the PR base commit as the workspace root, falls back to `main` if the base
predates the gate, and uses the PR head's Dockerfile, lock file, pyproject and
exception file only as data under `.scan-input/`. Only the one-time PR that
introduces the gate may bootstrap from its own copy, and it emits an explicit
warning. Because the workflow definition for `pull_request` events is still
read from the PR head, CODEOWNERS review of `.github/` remains the control for
workflow changes; CODEOWNERS review of `scripts/` and `security/` protects the
policy code and exception data. When available, Docker's VEX is fetched with
`--verify` against Docker's DHI signing key, checked in as
`.github/actions/vulnerability-scan/dhi-signing-key.pub` and pinned by
SHA-256 (rotate via a reviewed PR against
[docker-hardened-images/keyring](https://github.com/docker-hardened-images/keyring)).
`--skip-tlog` is used because Docker does not publish DHI VEX attestations to
the public Rekor log; the signature itself is still verified. An explicit
"no VEX attestations found" response is the only non-fatal VEX-fetch outcome;
all other failures remain blocking. PRs from forks have no secrets and fail
closed; re-push them to a branch in this repository.

#### Exceptions

`security/vulnerability-exceptions.toml` is the **only** place a vulnerability
may be accepted. Each entry needs `id` (CVE/GHSA/…), `package`, `reason`,
`owner` and `approver` (distinct GitHub users or teams), `created` and
`expires` (at most **90 days**). The gate fails when an exception is expired,
malformed, duplicated, when a fix becomes available, or when it is **stale**
(the vulnerability is no longer found or is already covered by VEX). Upgrade
PRs — including Dependabot's — must therefore delete the matching exception.
Add exceptions only through a reviewed PR with Security approval; never widen
an entry beyond one ID and one package.

#### Release hygiene

The promotion re-scan and daily re-scan mean a newly disclosed vulnerability
can block an already-built candidate. Fix it (or add a reviewed exception on
`main`, then merge `main` into the release branch) **before** bumping the
release version, so the version bump is the last commit and produces the
candidate that is promoted.

> **Developer tooling**: All developers should install the **Snyk IDE extension** (VS Code or JetBrains) to catch security issues locally before raising a PR. See [Section 10](#10-onboarding-checklist-for-new-developers).

### 3.3 Required Repository Secrets

The following secrets must be configured in **Repository Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; no manual setup |
| `DOCKER_ORG_USERNAME` | Organisation Docker account used to pull Docker Hardened Images, authenticate Docker Scout, and publish the approved image |
| `DOCKER_ORG_ACCESS_TOKEN` (repository secret) | **Read-only** organisation access token used by CI jobs to pull Docker Hardened Images from `dhi.io` and to authenticate the Docker Scout CLI (`DOCKER_SCOUT_HUB_USER`/`DOCKER_SCOUT_HUB_PASSWORD`) |
| `DOCKER_ORG_ACCESS_TOKEN` (environment secret on `preview-release`, `beta-release`, `ga-release`) | **Write-capable** organisation access token scoped to push on `openbanking/conformance-suite-v2`; overrides the read-only repository secret only inside the approved promotion job |
| `SNYK_TOKEN` | Token used by the `Vulnerability Scan`, promotion re-scan and scheduled re-scan jobs (also required as a Dependabot secret) |

Before enabling promotion, a repository administrator must create the Docker
Hub repository `openbanking/conformance-suite-v2` (public) and configure two
organisation access tokens under the same secret name:

1. A **read-only** token as the repository-level `DOCKER_ORG_ACCESS_TOKEN`,
   used by CI runs from this repository to pull DHI base images and run
   Docker Scout (see the Dependabot note below).
2. A **write-capable** token (image push on `openbanking/conformance-suite-v2`
   only) as an **environment secret** named `DOCKER_ORG_ACCESS_TOKEN` on each
   of `preview-release`, `beta-release`, and `ga-release`.

The reusable promotion job declares `environment:`, so GitHub resolves the
environment secret in preference to the repository secret passed by the
caller. Push credentials are therefore only released after a required
reviewer approves the Environment gate; candidate builds never publish
images.

**Dependabot pull requests** cannot read repository Actions secrets; they only
receive secrets configured under **Settings → Secrets and variables →
Dependabot**. Because hardened `Docker Build` runs log in to `dhi.io` and every
PR runs the `Vulnerability Scan`, also add `DOCKER_ORG_USERNAME`, the
**read-only** `DOCKER_ORG_ACCESS_TOKEN` and `SNYK_TOKEN` as Dependabot secrets.
Never store the write-capable token there. Without them, Dependabot PRs fail
closed. `.github/dependabot.yml` updates `uv.lock`, Dockerfile base-image
digests and GitHub Actions; a DHI digest update must also update
`EXPECTED_RUNTIME_BASE` in `scripts/validate_docker_base.py`.

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
     aggregate coverage          [Image (amd64/arm64)] → [Vulnerability Scan]
                                 build, smoke test, Scout + Snyk + pip-audit,
                                 vulnerability gate (Section 3.2)

The external Snyk integration reports its own PR status independently.
```

Pushes to `preview/*`, `release/*`, and `main` additionally build candidate
artifacts when the source tree contains the hardened Docker contract (a DHI
base, explicit UID/GID `65532:65532`, and `docker/entrypoint.py`). Each
`linux/amd64` and `linux/arm64` image is built once, smoke-tested, blocked by
the vulnerability gate (Section 3.2), accompanied by an SPDX SBOM,
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
| `.github/workflows/_promote-image.yml` | Called by automatic and manual promotion workflows | Trusted validation, pre-approval vulnerability re-scan, and exact-artifact Docker Hub publication implementation |
| `.github/workflows/security-rescan.yml` | Daily schedule + manual dispatch | Re-scan `main`, `release/*` and published images; sync one issue per vulnerability |
| `.github/workflows/_finalize-ga-release.yml` | Called after successful GA publication | Create the GA Git tag at the source SHA and open the develop merge-back PR |

Promotion locates the successful push CI run for the exact source SHA and
branch, re-scans its exact image archives with current vulnerability data and
`main`'s policy before any Environment approval is requested, downloads its immutable artifacts, verifies archive checksums,
revalidates release metadata using scripts checked out from `main`, and
confirms the source SHA belongs to the requested branch and a merged pull
request (direct pushes are rejected). If that pull request has no recorded
approval — for example, an administrator merged it by bypassing branch
protection — promotion continues but emits a workflow warning and a job-summary
entry naming who merged it, so the bypass is auditable; the required
Environment reviewer is then the only blocking human gate. Automatic promotion uses the exact CI run that triggered
it; manual recovery dispatches locate the matching run. Candidate CI and
release-script files must exactly match the trusted copies on `main`; pipeline
changes therefore land on `main` before release branches consume them. Only
then does the Environment-gated job receive Docker Hub credentials; it pushes
each platform image by digest with `skopeo copy --preserve-digests` (so the
registry digests equal the scanned digests; a plain `docker push` would
re-serialise the manifest, and pushing by digest leaves no internal staging
tags) and assembles the already-tested images without rebuilding. Promotion is serialized to prevent
tag races, rejects an existing immutable version tag discovered through the
Docker Hub tags API, and attests the published
multi-architecture manifest with provenance and both platform SBOMs using
`actions/attest`. The provenance attestation also records an Artifact Metadata
storage record (`artifact-metadata: write`), so each published image appears on
the organisation's Linked Artifacts page.

Before the Environment approval gate, automatic and manual promotions add a
**Release proposed for approval** table to the workflow run summary alongside
the vulnerability reports. It shows the verified candidate's exact raw version,
image repository, publication tags, moving tag, channel, approval environment,
source branch and commit. For example, an eligible `2.0.0-beta.6` candidate
shows its immutable version tag and the conditional `2.0.0-beta-latest`
pointer. Pointer eligibility reflects the current registry inventory, not a
publication result: it is rechecked after publication and attestations, and
ineligible candidates show the pointer as unchanged. GA shows its exact version
and `latest`; preview shows only its exact version tag. All existing
post-approval validation and publication safeguards remain in place.

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
| `2.0.0-beta-latest` (Docker Hub only) | `2.0.0-beta-latest` | Temporary MVP beta pointer |

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
   `X.Y.Z-beta.N`, completes provenance and both platform SBOM attestations,
   then updates `2.0.0-beta-latest` if this is the highest published 2.0.0 beta
   and the formal `2.0.0` version has not been published.
   It does not create or move GA `latest`.
4. Increment `N` in a new approved change for each subsequent beta.

Manual **Promote beta image** dispatch is available from `main` only for
recovery/backfill.
```

The developer does not set `2.0.0-beta-latest` in `pyproject.toml` or run another
workflow. Both automatic and manual beta promotions maintain it through the
same Environment-approved publication path. Docker Hub's complete published
tag inventory is the source of truth: compare exact `2.0.0-beta.N` versions
numerically, not by merge time, branch, or publication time.
An older backfill publishes its exact version but leaves the pointer unchanged;
preview, other release series, and GA releases never move it. Once Docker Hub
contains `2.0.0`, the pointer is frozen at its last MVP beta, superseded by the
formal release. It is never repointed to GA. The former `beta-latest` tag is no
longer maintained; this change does not delete existing registry tags.

The pointer copies the exact attested multi-architecture manifest by digest,
without rebuilding, and promotion verifies that its digest matches the version
tag. Registry inventory, alias publication, or verification errors fail the
workflow explicitly. Publication and alias updates are not atomic: a failed
promotion may leave an exact version published with the alias unchanged.
Duplicate-version protection still applies; this change introduces no separate
seed or repair workflow. The alias is first created on the next eligible beta
promotion, not retroactively for existing images.

Docker Hub tag-immutability rules must allow `2.0.0-beta-latest` (and GA `latest`)
to move while protecting exact version tags. Only the serialized promotion
pipeline should write these aliases; independent registry writers are outside
its concurrency protection. Release branches must incorporate updated trusted
main pipeline tooling and rebuild candidates when required by the existing
pipeline-file equality check.

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
