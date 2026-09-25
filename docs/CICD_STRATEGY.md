# CI/CD Strategy & Repository Controls

## Document Control

| Field | Value |
|---|---|
| Project | Open Banking UK Conformance Test Tool |
| Scope | Repository governance, branching strategy, pipeline design |
| Status | Approved |
| Date | April 2026 (branching and release model revised — see [Section 1](#1-branching-strategy-stable-main-with-development-preview-and-release-branches)) |

---

## Table of Contents

- [CI/CD Strategy \& Repository Controls](#cicd-strategy--repository-controls)
  - [Document Control](#document-control)
  - [Table of Contents](#table-of-contents)
  - [1. Branching Strategy: Stable Main with Development, Preview, and Release Branches](#1-branching-strategy-stable-main-with-development-preview-and-release-branches)
    - [1.1 Permanent Branches](#11-permanent-branches)
    - [1.2 Transient Branches](#12-transient-branches)
    - [1.3 Naming Conventions](#13-naming-conventions)
    - [1.4 Branching Diagram](#14-branching-diagram)
    - [1.5 Feature Preview Policy](#15-feature-preview-policy)
    - [1.6 Moving Work Between Planned Releases](#16-moving-work-between-planned-releases)
  - [2. Branch Protection Rules](#2-branch-protection-rules)
    - [2.1 `main` Branch](#21-main-branch)
    - [2.2 `develop`, `preview/**`, and `release/**` Branches](#22-develop-preview-and-release-branches)
    - [2.3 GitHub Environments](#23-github-environments)
    - [2.4 Configuring GitHub Copilot as a Required Reviewer](#24-configuring-github-copilot-as-a-required-reviewer)
  - [3. Repository Security Controls](#3-repository-security-controls)
    - [3.1 GitHub Security Features](#31-github-security-features)
    - [3.2 Snyk Integration](#32-snyk-integration)
    - [3.3 Required Repository Secrets](#33-required-repository-secrets)
  - [4. CI/CD Pipeline Design](#4-cicd-pipeline-design)
    - [4.1 Workflow Overview](#41-workflow-overview)
    - [4.2 Workflow Files](#42-workflow-files)
    - [4.3 Candidate Image Quality Gate](#43-candidate-image-quality-gate)
    - [4.4 Concurrency Control](#44-concurrency-control)
  - [5. Release Process](#5-release-process)
    - [5.1 Version and Image Tag Source of Truth](#51-version-and-image-tag-source-of-truth)
    - [5.2 Human and Automated Responsibilities](#52-human-and-automated-responsibilities)
    - [5.3 Feature Preview Releases](#53-feature-preview-releases)
    - [5.4 Beta, RC, and GA Releases](#54-beta-rc-and-ga-releases)
    - [5.5 Release Exception Process](#55-release-exception-process)
    - [5.6 Hotfix Process](#56-hotfix-process)
  - [6. Build Status Badge](#6-build-status-badge)
  - [7. Dependency Management Controls](#7-dependency-management-controls)
  - [8. Code Ownership](#8-code-ownership)
  - [9. Audit \& Compliance](#9-audit--compliance)
  - [10. Onboarding Checklist for New Developers](#10-onboarding-checklist-for-new-developers)
  - [11. Docker Hardened Images](#11-docker-hardened-images)
  - [12. Pull Request Template](#12-pull-request-template)
  - [13. Agent Implementation Notes](#13-agent-implementation-notes)

---

## 1. Branching Strategy: Stable Main with Development, Preview, and Release Branches

The project only ever supports **one GA version at a time**. `main` therefore
represents the latest stable release, not the normal development head. Ordinary
feature development integrates through `develop`; participant-facing trial
images are produced from explicit `preview/**` or `release/**` branches; and
stable tags are promoted only after the release branch is accepted for GA.

This model separates four different questions that must not be conflated:

- Has the code been accepted for ongoing development? (`develop`)
- Is one isolated feature ready for a participant trial? (`preview/**`)
- Is a known upcoming release being stabilised? (`release/**`)
- Has a GA version been approved as the supported release? (`main`)

### 1.1 Permanent Branches

| Branch | Purpose | Direct Push Allowed |
|---|---|---|
| `main` | Stable-only branch. Contains GA release history and emergency hotfixes for the currently supported version. Participant-facing stable tags and `latest` are derived from approved GA releases. | **No** |
| `develop` | Normal integration branch for accepted development work. Feature and bugfix PRs target this branch. No participant-facing images are published from `develop`. | **No** |

### 1.2 Transient Branches

| Prefix | Branch From | Merges Into | Purpose |
|---|---|---|---|
| `feature/` | `develop` | `develop`, `preview/<feature>`, or `release/<semver>` | New features and enhancements |
| `bugfix/` | `develop` | `develop`, `preview/<feature>`, or `release/<semver>` | Non-critical bug fixes |
| `preview/` | Latest stable tag, for example `v2.1.0` | `develop` only if accepted | Isolated feature trial branch for a participant-facing preview that may never ship |
| `release/` | `develop` (cut when release scope is selected) | `main` on GA; back-merge or cherry-pick to `develop` as needed | Stages beta, RC, and GA candidates for a specific release |
| `hotfix/` | `main` | `main` | Critical fixes to the currently released version |

### 1.3 Naming Conventions

```
feature/<issue-number>-<short-description>
bugfix/<issue-number>-<short-description>
preview/<feature-name>                         # e.g. preview/new-cert-flow
release/<semver>                               # e.g. release/1.2.0
hotfix/<issue-number>-<short-description>
```

Examples:
```
feature/42-add-token-endpoint-tests
bugfix/91-fix-result-json-encoding
preview/new-cert-flow
release/1.2.0
hotfix/107-fix-auth-header-parsing
```

### 1.4 Branching Diagram

```
main       ───●───────────────●────────────────────────●──
              v2.1.0          ▲                        v2.2.0
                              │ merge approved GA release
develop    ───●──●──●──●──●───┴────●──●──●──────────────
              │     │      │        ▲
feature/a ────┘     │      │        │ accepted preview merges/cherry-picks here
feature/b ──────────┘      │        │
                           │
preview/new-cert-flow ─────●──●── publish 2.1.1.dev1/.dev2
                           │
release/2.2.0 ─────────────┴──●──●── publish 2.2.0-beta.1, 2.2.0-rc.1, 2.2.0

hotfix/107 ─── branches from main, merges back to main, then is merged/cherry-picked to develop and any active release branches
```

Every `feature/`/`bugfix/` PR targets `develop` unless it is explicitly scoped
to a preview, release, or hotfix branch. `main` is not used for normal
development.

### 1.5 Feature Preview Policy

Feature previews are participant-facing trial builds for one isolated feature
or behaviour change. They are normal and supported, but they are not release
betas. A preview may be accepted, revised, abandoned, or replaced without
consuming the next patch version number.

Preview branches are created from the latest stable tag so the trial image is
clean and does not include unrelated `develop` changes:

```bash
git checkout v2.1.0
git checkout -b preview/new-cert-flow
```

Preview image tags use PEP 440 development releases for the next possible
patch version. If the latest stable release is `2.1.0`, separate experimental
feature previews can use:

```text
2.1.1.dev1
2.1.1.dev2
```

These tags mean "experimental builds after `2.1.0`, before any formal `2.1.1`
release". They are not post-releases and do not imply that the preview feature
will be accepted into `2.1.1`. The feature identity is carried by the preview
branch, PR, release notes, and trial instructions. Formal release prereleases
use PEP 440 beta and RC forms such as `2.1.1b1` and `2.1.1rc1`.

If a preview is accepted, merge or cherry-pick the feature into `develop` and
then include it in a later `release/**` branch. If it is rejected, archive or
delete the preview branch; immutable preview image tags may remain in the
registry for auditability.

### 1.6 Moving Work Between Planned Releases

When two or more releases are being staged concurrently:

1. Once a change is merged to `develop`, cherry-pick it into whichever
   `release/x.y.z` branch it's currently planned for.
2. If priorities change before that release ships, **revert the cherry-pick**
   on the original release branch and **cherry-pick it into the new target
   release branch** instead. The commit on `develop` never moves — only which
   release branch carries it changes.
3. Once a release branch is tagged and merged into `main`, its contents are
   final. Moving a *shipped* change to a different release is no longer a
   re-staging exercise — it becomes a `hotfix/` against the newly released
   version, or scope for the next release.

This keeps `develop` as the development source of truth while preserving
`main` as the stable release record.

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
| Recommended required status checks | `Check`, candidate image quality gate, and Snyk status checks |
| Require branches to be up to date before merging | **Enabled** |
| Require conversation resolution before merging | **Enabled** |
| Require linear history | **Enabled** (merge squash or rebase only) |
| Allow administrators to bypass | **Enabled** — repository admins may override in exceptional circumstances; see [Section 5.5](#55-release-exception-process) |
| Restrict who can push to matching branches | Standards Team leads only |

### 2.2 `develop`, `preview/**`, and `release/**` Branches

| Rule | Setting |
|---|---|
| Require a pull request before merging | **Enabled** |
| Required approving reviews | **2** (1 Copilot + 1 human) |
| Require review from Code Owners | **Enabled** |
| CI runs automatically on push | **Enabled** |
| Required status checks | `Check`, candidate image quality gate, and Snyk status checks |
| Require branches to be up to date before merging | **Enabled** |
| Require conversation resolution before merging | **Enabled** |
| Require linear history | **Enabled** |
| Allow force pushes | **Disabled** |
| Restrict who can push to matching branches | Standards Team leads and release maintainers only |

Any number of `preview/**` and `release/**` branches may be open at once; each
is protected identically and independently. `develop` is protected because it
is the source for release branches.

### 2.3 GitHub Environments

Participant-facing image publication is controlled by GitHub Environments.
Environment approval happens only after a candidate image has already passed
the full automated quality gate; the approval authorises promotion of that
exact checked digest.

| Environment | Used For | Human Action |
|---|---|---|
| `preview-release` | Publishing `.devN` image tags from `preview/**` branches | Approver reviews the candidate manifest, trial purpose, version/tag, and checks, then clicks **Approve** |
| `beta-release` | Publishing `bN` and `rcN` image tags from `release/**` branches | Approver confirms the prerelease is intended for participant testing, then clicks **Approve** |
| `ga-release` | Publishing stable `X.Y.Z`, `X.Y`, `X`, and `latest` tags | Approver confirms GA readiness, matching tag/release notes, and support handover, then clicks **Approve** |

The approval screen must never be used to choose or type a release tag. The
tag is committed and reviewed before the workflow reaches the Environment
gate.

### 2.4 Configuring GitHub Copilot as a Required Reviewer

GitHub Copilot code review is enabled as follows:

1. **GitHub Repository Settings → Copilot → Code review**
   - Enable "Automatic review requests" for pull requests targeting `main`,
     `develop`, `preview/**`, and `release/**`
2. In the **branch protection rules** for protected branches:
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

Snyk is the primary security scanning platform. Repository dependency and code
checks run through the Snyk GitHub integration. Candidate container images used
for preview, beta, RC, and GA publication must also be scanned as part of the
GitHub Actions quality gate.

| Scan Type | Trigger | Blocks Merge |
|---|---|---|
| Snyk Open Source (dependencies) | Every PR | `high` + `critical` severity |
| Snyk Code (SAST) | Every PR | `high` + `critical` severity |
| Snyk Container (candidate image) | Publishable preview/release candidate workflows | `high` + `critical` severity |

The status check posted by Snyk's GitHub integration is separate from the
candidate image workflow. PRs must not be merged when either reports a high or
critical vulnerability. To enforce this mechanically, add the exact status
contexts to the repository rulesets.

If the team encounters a security issue they are uncertain how to resolve, the Security team should be consulted. Code containing known `high` or `critical` vulnerabilities must not be merged.

> **Developer tooling**: All developers should install the **Snyk IDE extension** (VS Code or JetBrains) to catch security issues locally before raising a PR. See [Section 10](#10-onboarding-checklist-for-new-developers).

### 3.3 Required Repository Secrets

The following secrets must be configured in **Repository Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; no manual setup |
| `SNYK_TOKEN` | Required for authenticated candidate image scans in GitHub Actions. Configure as an organisation-managed service credential before enabling participant-facing preview or release publication. |

There are currently no repository-level variables required by CI: the
supported pytest suite is fully offline and does not target a live model bank
or Ozone environment.

---

## 4. CI/CD Pipeline Design

### 4.1 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ Trigger: Pull Request or push (develop, preview/*, release/*,    │
│          hotfix/*, main)                                         │
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

Publishable preview/release candidate:
  build candidate image by commit SHA
  run full checks against that exact image digest
  scan final runtime image with Snyk
  generate SBOM/provenance
  write candidate manifest
  wait for Environment approval
  promote the checked digest to the public participant-facing tag

The external Snyk integration reports dependency/code PR status independently.
```

### 4.2 Workflow Files

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to protected branches | Canonical `make check` gate plus parallel Docker build and health check |
| `.github/workflows/image-candidate.yml` | Pushes or approved dispatches for `preview/**` and `release/**` branches | Builds immutable candidate images, runs the full image quality gate, and records the checked digest |
| `.github/workflows/image-promote.yml` | Successful candidate workflow plus Environment approval | Promotes the already-checked digest to the reviewed participant-facing tag without rebuilding |

### 4.3 Candidate Image Quality Gate

Every participant-facing image must pass the same quality gate before it can be
published, regardless of whether it is a feature preview, beta, RC, or GA
release. The workflow must promote only the digest that passed the gate; it
must not rebuild during publication.

The candidate gate includes:

1. Build the final runtime image for every platform that will be published.
2. Run `make check` or consume the successful `Check` result for the same
   commit.
3. Run the container smoke test.
4. Run the hardened runtime smoke test using the documented security profile:
   read-only root filesystem, no Linux capabilities, no privilege escalation,
   writable `/data`, tmpfs `/tmp`, and optional read-only `/certs`.
5. Run Snyk container scanning against the final runtime image and fail on any
   `high` or `critical` finding. There is no beta/preview allowlist.
6. Generate SBOM and provenance attestations.
7. Validate the branch, channel, source-controlled image tag, project version
   compatibility, changelog requirements, and tag immutability.
8. Store a candidate manifest containing the commit SHA, source branch,
   intended image tag, image digest, workflow run ID, SBOM/provenance
   references, and scan summary.

Publication is a digest promotion:

```text
candidate digest that passed checks
  + Environment approval
  + tag does not already exist
  = participant-facing image tag
```

Publication fails if any part of the checked manifest is missing, stale,
failing, or mismatched.

### 4.4 Concurrency Control

All workflows use `concurrency` groups to cancel in-progress runs when new commits are pushed to the same branch or PR. This avoids queue pile-up from rapid successive commits.

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

---

## 5. Release Process

### 5.1 Version and Image Tag Source of Truth

The participant-facing Docker image tag is set in source control before a PR
is reviewed. A human must not type a tag into the workflow approval screen.

Use `[project].version` in `pyproject.toml` as the single source of truth for
participant-facing image tags:

```toml
[project]
version = "2.1.1.dev1"
```

```toml
[project]
version = "2.1.1b1"
```

```toml
[project]
version = "2.1.1rc1"
```

```toml
[project]
version = "2.1.1"
```

The workflow validates this value with `packaging.version.Version`, preserves
the raw TOML string for the OCI image tag and report labels, and rejects a
branch/channel mismatch. Stable GA publication also requires a matching Git
tag, for example `v2.1.1`.

**Tag formats:**

| Tag | Example | Type |
|---|---|---|
| `X.Y.Z.devN` | `2.1.1.dev1` | Feature preview image |
| `X.Y.ZbN` | `2.1.1b1` | Beta image |
| `X.Y.ZrcN` | `2.1.1rc1` | Release candidate image |
| `X.Y.Z` | `2.1.1` | Stable image |
| `vX.Y.Z` | `v2.1.1` | Stable Git tag |

Immutable version tags must never be overwritten. Moving convenience aliases
such as `latest` are permitted only for GA releases.

---

### 5.2 Human and Automated Responsibilities

Humans decide intent and authorise publication. Automation enforces quality,
repeatability, and immutability.

| Step | Automated or Manual | Exact Human Action |
|---|---|---|
| Create feature, preview, release, or hotfix branch | Manual | Developer creates the branch from the correct base |
| Set intended participant-facing image tag | Manual via PR | Author edits `[project].version` in `pyproject.toml`; formal release PRs also update the changelog |
| Review intended tag and scope | Manual via PR | Reviewers inspect the diff and approve or request changes |
| Build candidate image | Automated | None |
| Run tests, smoke tests, hardening checks, Snyk scan, SBOM, and provenance | Automated | None unless a failure must be fixed |
| Record candidate digest and manifest | Automated | None |
| Decide whether to publish | Manual Environment gate | Approver reviews the candidate manifest and clicks **Approve** |
| Promote checked digest to participant-facing tag | Automated after approval | None |
| Create or update GitHub release/prerelease notes | Manual or semi-automated | Maintainer writes or approves notes |
| Notify trial users | Manual | Maintainer sends the exact image tag or digest and trial instructions |

The Environment approval means:

> I have reviewed the candidate, checks, version, intended image tag, and
> release or trial purpose. I authorise publishing this exact checked image
> digest to a participant-facing tag.

### 5.3 Feature Preview Releases

Feature previews allow one feature to be trialled cleanly without assigning it
the next formal patch release number.

1. A maintainer creates `preview/<feature-name>` from the latest stable tag,
   for example `v2.1.0`.
2. The author opens PRs into `preview/<feature-name>` containing only the trial
   feature, project version bump, and any trial documentation.
3. `pyproject.toml` names the intended preview image tag through
   `[project].version`, for example `2.1.1.dev1`.
4. CI builds the candidate image and runs the full quality gate.
5. A release approver reviews the `preview-release` Environment gate and clicks
   **Approve**.
6. The workflow promotes the checked digest to the immutable preview tag.
7. The maintainer sends trial users the exact image tag or digest.

Subsequent preview iterations increment only the preview iteration:

```text
2.1.1.dev1
2.1.1.dev2
```

If the feature is accepted, merge or cherry-pick it into `develop`. If it is
rejected, close the branch without consuming a formal release number.

### 5.4 Beta, RC, and GA Releases

Release branches are used only once maintainers have selected the scope for a
known upcoming release.

1. A maintainer creates `release/X.Y.Z` from `develop` when release scope is
   selected.
2. Release preparation PRs into `release/X.Y.Z` update the project version,
   changelog, and any stabilisation fixes.
3. Beta and RC candidate images publish from the release branch after the full
   quality gate and `beta-release` approval:

   ```text
   2.1.1b1
   2.1.1b2
   2.1.1rc1
   ```

4. GA publication requires:
   - `pyproject.toml` project version `2.1.1`;
   - matching Git tag `v2.1.1`;
   - full quality gate success for the candidate digest;
   - `ga-release` Environment approval.
5. The promotion workflow publishes the checked digest as `2.1.1` and may also
   publish stable aliases `2.1`, `2`, and `latest`.
6. The release branch is merged into `main` after GA acceptance and merged or
   cherry-picked back into `develop` as needed.

### 5.5 Release Exception Process

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

### 5.6 Hotfix Process

```
1. Branch from main:
   git checkout main && git checkout -b hotfix/107-fix-auth-header

2. Fix, commit, push
   - PR against main: requires CI pass + 2 approvals

3. Add project-version and changelog updates for the hotfix version.

4. Candidate image workflow builds and checks the hotfix image.

5. GA approver approves publication. The workflow promotes the checked digest
   to the hotfix version tag and stable aliases.

6. Merge the hotfix into main.

7. Merge or cherry-pick the hotfix into develop and any active release branch
   so future releases also carry the fix:
   git checkout release/1.2.0 && git cherry-pick <hotfix-commit>
```

---

## 6. Build Status Badge

The `main` branch CI status badge is embedded in [README.md](../README.md):

```markdown
![CI](https://github.com/OpenBankingUK/ob-conformance-tool/actions/workflows/ci.yml/badge.svg?branch=main)
```

The badge reflects the latest CI run on the stable branch. A red badge means
the current GA branch is not healthy — this should be treated as a P1 issue and
resolved immediately.

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
- Branch protection rules also protect `develop` and every `preview/**` branch
- All merges to protected branches require at least one human sign-off, providing a human accountability chain for every production or trial change
- Pull request and review history is immutable on GitHub
- Any admin bypass of branch protection rules must be documented in the PR (see [Section 5.5](#55-release-exception-process))
- Candidate manifests, SBOMs, provenance attestations, Snyk scan summaries, and Environment approvals form the release audit trail for participant-facing images

---

## 10. Onboarding Checklist for New Developers

Before a new team member can contribute, the following must be completed by a repository admin:

- [ ] Add to the `OpenBankingUK/ob-sps-developers` GitHub team
- [ ] Confirm GitHub Copilot licence is assigned
- [ ] Install the **Snyk IDE extension** for local security scanning (available for [VS Code](https://marketplace.visualstudio.com/items?itemName=snyk-security.snyk-vulnerability-scanner) and JetBrains IDEs) — recommended by the Security team
- [ ] Clone the repository and run `uv sync --frozen --no-install-project` to install dependencies
- [ ] Read this document, [REQUIREMENTS.md](REQUIREMENTS.md), and [TESTING_STRATEGY.md](TESTING_STRATEGY.md)
- [ ] Complete a first PR against `develop` to verify the pipeline works end-to-end
- [ ] Read [Section 5](#5-release-process) before preparing a preview, beta, RC, or GA publication PR

---

## 11. Docker Hardened Images

The project uses **Docker Hardened Images (DHI)** as the base image (`dhi.io/python`, Debian 13 variant). Pulling any `dhi.io` image requires authenticating to the `dhi.io` registry with a Docker account, so **DHI does affect the GitHub Actions pipeline**: every workflow job that builds the container image logs in to `dhi.io` first, using a read-only, organisation-owned Docker credential (`DOCKER_ORG_USERNAME` / `DOCKER_ORG_ACCESS_TOKEN` secrets) — never a personal Docker account. This applies to normal candidate builds and to the weekly Dependabot digest-update PRs described below.

The Dockerfile must use a multi-stage build: all package installation and build steps happen in a build stage (the DHI `-dev` variant, which has a shell and package manager); the final runtime stage uses the distroless DHI variant, which has no shell or package manager. The application runs as a non-root user (UID/GID 65532) and must bind to port 1025 or above.

Both `FROM` lines in the Dockerfile are pinned to an exact digest (`image:tag@sha256:...`), not a floating tag, for reproducibility. `.github/dependabot.yml` configures weekly Docker-ecosystem update PRs that bump these digests when Docker publishes a new DHI build; each such PR runs the full check suite, the hardened runtime smoke test, and the vulnerability scan — the same gate as any other candidate — before it can be merged.

---

## 12. Pull Request Template

A lightweight PR template is provided at `.github/PULL_REQUEST_TEMPLATE.md`
and is applied automatically to all new pull requests. It is intentionally
minimal — just enough to prompt the author on the key points without adding
friction:

```markdown
## What does this PR do?

<!-- Summarise the change and why it was made. Include the issue number if applicable. -->

## Checklist

- [ ] Tests added or updated
- [ ] `CHANGELOG.md` updated (feat / fix / hotfix / security changes only)
- [ ] No hardcoded secrets or credentials
- [ ] `uv.lock` regenerated if `pyproject.toml` changed
- [ ] `[project].version` updated if this PR prepares a preview, beta, RC, or GA image
```

The template is a prompt, not a gate. Authors should complete what is relevant and skip sections that do not apply.

---

## 13. Agent Implementation Notes

When an AI agent implements or updates the release automation, it must preserve
the policy in this document:

1. Keep `main` stable-only. Do not design workflows that publish ordinary
   `develop` commits as participant-facing images.
2. Build candidate images once, run the complete quality gate against that
   exact digest, and promote by digest only. Do not rebuild during promotion.
3. Treat `[project].version` in `pyproject.toml` as the image-tag source of
   truth. Do not accept workflow-dispatch tag input for participant-facing
   publication.
4. Enforce branch/channel compatibility:
   - `preview/**` may publish only `X.Y.Z.devN`.
   - `release/**` may publish only `X.Y.ZbN`, `X.Y.ZrcN`, or `X.Y.Z`.
   - `main` receives GA release history only.
5. Enforce immutable version tags. Fail rather than overwrite an existing
   preview, beta, RC, or stable image tag.
6. Require `SNYK_TOKEN` for candidate image scans and fail on scan/auth
   failures. There is no high/critical allowlist for preview or beta images.
7. Attach or preserve SBOM and provenance for the promoted digest.
8. Require the matching GitHub Environment approval before participant-facing
   promotion:
   - `preview-release` for previews;
   - `beta-release` for beta and RC;
   - `ga-release` for stable releases.
9. Keep Docker Hub disabled until GA distribution policy explicitly enables it.
   Preview and beta publication target GHCR unless this document is revised.
10. Update this strategy document and the changelog whenever the workflow
    policy changes.
