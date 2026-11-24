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
  - [Security Checks and Publication Configuration](IMAGE_SECURITY_APPROVALS.md)
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
| Required approving reviews | **1** human (Copilot review is advisory and does not count as an approval) |
| Dismiss stale pull request approvals when new commits are pushed | **Enabled** |
| Require review from Code Owners | **Enabled** |
| Require status checks to pass before merging | **Enabled**; remove the retired custom check during the [settings cutover](IMAGE_SECURITY_APPROVALS.md) |
| Required status checks | `Check`, `Docker Build`, `Image (linux/amd64)`, `Image (linux/arm64)`, and `code/snyk (Standards)`, plus CodeQL code scanning results (High or higher) |
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
| Require status checks to pass before merging | **Enabled**; remove the retired custom check during the [settings cutover](IMAGE_SECURITY_APPROVALS.md) |
| Required status checks | `Check`, `Docker Build`, `Image (linux/amd64)`, `Image (linux/arm64)`, and `code/snyk (Standards)`, plus CodeQL code scanning results (High or higher) |
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
   - Set required approving reviews to **1** (human)
   - Copilot review is advisory: it comments but never counts as an approving review
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

Snyk remains connected through the **Snyk portal** and reports the required
`code/snyk (Standards)` PR status independently of Actions. GitHub CodeQL
default setup and its existing High-or-higher code-scanning rule also remain.
Neither scanner is invoked or awaited again by the release workflows.

| Check | Trigger | Enforcement |
|---|---|---|
| External Snyk integration | PR | Required `code/snyk (Standards)` status under the existing Snyk policy |
| GitHub CodeQL | GitHub default setup | Required CodeQL results on main/develop and release branches |
| Secret scanning and Ruff security lint | `make check` in CI | Part of the required `Check` job |
| Image contract and evidence | Both platform builds and publication | Exact tested artifact, digest, platform and source identity |
| Docker Scout impact summary | `Image (linux/amd64)` on every hardened build | Informational job summary only; never blocks (see below) |

The blocking custom Docker Scout, Snyk Container and pip-audit pipeline, signed DHI
provenance/VEX assessment, persistent exception policy, promotion rescans and
scheduled vulnerability-issue sync are removed. There are no candidate-bound
security approvals or manual release-publishing approvals.

**Coverage trade-off:** Snyk/CodeQL PR checks do not provide equivalent
multi-platform OS/container scanning or ongoing surveillance of published
images. Existing vulnerability issues and reports remain historical evidence;
they are not automatically refreshed, closed, or marked fixed by this change.

**Trust model:** Build and smoke-test jobs remain unprivileged with respect to
publication. The trusted `workflow_run` publisher verifies a successful
same-repository push CI run, its exact workflow identity, source SHA/branch and
candidate artifacts. It executes only release tooling from `main`, never
candidate code with publishing credentials. Archive checksums, BuildKit
provenance/SBOM identity, DHI family/digest syntax and release metadata are
still validated; these are integrity checks, not vulnerability assessments.

Keep the hardened DHI runtime, locked binary-wheel installation, non-root
UID/GID `65532`, and per-platform smoke tests. Dependabot still
proposes dependency and DHI digest updates. Ordinary PR reviews, GitHub secret
scanning/push protection and Code Owners remain in force.

See the [configuration guide](IMAGE_SECURITY_APPROVALS.md) for removing the
retired `Vulnerability Scan` ruleset requirement and replacing preview's sole
custom requirement with the external Snyk status.

#### Docker Scout results on published images

Docker Hub's Scout view analyses only the shipped runtime image: its OS
packages (initially inherited from the pinned DHI runtime base) and the Python
packages installed in `/app/.venv`. Builder-stage tools, `uv`, workflow, CI and
dev-only dependencies do not ship. Runtime-stage instructions can add, remove,
or replace shipped packages. Unchanged shipped contents do not guarantee
unchanged findings over time: Scout advisories, package matching and VEX
assessments can evolve independently of the image digest. A DHI digest update
can also change the shipped base contents and findings.

The `Image (linux/amd64)` job adds a non-blocking **Docker Scout impact**
section to its job summary for every hardened build, including PRs. It compares
the candidate archive with the published `2.0.0-beta-latest`, reports whether
the runtime base digest differs from the one in the published image's
provenance, and lists the candidate's vulnerable packages. Check it before
publishing a beta to test a security change. The step uses the read-only Docker
Hub credentials already used for DHI pulls, runs a checksum-pinned Scout CLI
rather than repository code, never pushes, and is skipped with a notice when
the credentials are unavailable. Scout reports DHI findings that Docker's own
VEX statements mark as not affecting the base; see
[Docker Scout VEX statements](IMAGE_SECURITY_APPROVALS.md#docker-scout-vex-statements).

Choose the target branch for a security fix by what it changes:

- **Shipped image contents** (DHI digest, runtime Python dependency or
  application code) can target the active `release/X.Y.Z` branch first so the
  next beta carries them. Port the change to `main` separately. Dependabot
  raises DHI digest PRs for both branches but `uv` PRs only for `main`. Port lock
  changes with `uv lock --upgrade-package <package>==<version>`; never copy
  `uv.lock` between branches because their dependency sets differ.
- **Pipeline files** checked by promotion (`ci.yml`, `auto-promote.yml` and the
  release scripts listed in `_promote-image.yml`) must merge to `main` first,
  then be copied exactly to the release branch (Section 4.2).

> **Developer tooling**: All developers should install the **Snyk IDE extension** (VS Code or JetBrains) to catch security issues locally before raising a PR. See [Section 10](#10-onboarding-checklist-for-new-developers).

### 3.3 Required Repository Secrets

The following secrets must be configured in **Repository Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; no manual setup |
| `DOCKER_ORG_USERNAME` | Organisation Docker account used to pull Docker Hardened Images and publish tested candidates |
| `DOCKER_ORG_ACCESS_TOKEN` (repository secret) | **Read-only** organisation access token used by CI jobs to pull Docker Hardened Images from `dhi.io` |
| `DOCKER_ORG_ACCESS_TOKEN` (environment secret on `preview-release`, `beta-release`, `ga-release`) | **Write-capable** organisation access token scoped to push on `openbanking/conformance-suite-v2`; overrides the read-only repository secret only inside the trusted promotion job |

Before enabling promotion, a repository administrator must create the Docker
Hub repository `openbanking/conformance-suite-v2` (public) and configure two
organisation access tokens under the same secret name:

1. A **read-only** token as the repository-level `DOCKER_ORG_ACCESS_TOKEN`,
   used by CI runs from this repository to pull DHI base images.
2. A **write-capable** token (image push on `openbanking/conformance-suite-v2`
   only) as an **environment secret** named `DOCKER_ORG_ACCESS_TOKEN` on each
   of `preview-release`, `beta-release`, and `ga-release`.

The reusable promotion job declares `environment:`, so GitHub resolves the
environment secret in preference to the repository secret passed by the
caller. Environments must restrict deployments to `main` and have no required
reviewers or wait timers. Candidate builds never receive the write token or
publish images. `SNYK_TOKEN` is not required by any workflow.

**Dependabot pull requests** cannot read repository Actions secrets in their
CI workflow; they only receive secrets configured under **Settings → Secrets
and variables → Dependabot**. Because hardened Docker builds log in to
`dhi.io`, add `DOCKER_ORG_USERNAME` and the **read-only**
`DOCKER_ORG_ACCESS_TOKEN` as Dependabot secrets. Never store the write-capable token
there. Without the read-only registry credentials, Dependabot image builds
fail closed. `.github/dependabot.yml` updates `uv.lock`, Dockerfile base-image
digests and GitHub Actions. Because Dependabot reads its configuration only
from `main`, a second Docker entry sets `target-branch` to the active
`release/*` branch so base-image digest PRs also open there for manual review;
update that entry when a new release branch is cut. Both Docker entries group
the `dhi.io/python` builder and runtime references so Dependabot proposes the
pair together for review each day. A DHI candidate update is admitted only for
a matching pair of digest-pinned DHI Python 3.14 builder/runtime images:
both Debian 13 or both Alpine. No advance
allowlist update or security-risk approval is required for new candidate
digests. The validator's historical reference API remains separate from this
candidate build contract.

There are currently no repository-level variables required by CI: the
supported pytest suite is fully offline and does not target a live model bank
or Ozone environment.

The `preview-release`, `beta-release`, and `ga-release` GitHub Environments
scope publishing credentials, without human reviewers or wait timers.
They must restrict deployment branches to `main`; promotion workflows must
also be dispatched from `main`.

---

## 4. CI/CD Pipeline Design

### 4.1 Workflow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ Trigger: Pull Request or push (main, develop, preview/*,         │
│ release/*, hotfix/*)                                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
               ┌───────────────┴────────────────────┐
               ▼                                    ▼
           [Check]                           [Docker Build]
     make check with full                     build and smoke test
     tracked-file secret scan
     ruff + mypy                      [Image (amd64/arm64) artifacts]
     unit + component tests           build and smoke-test exact archives
     aggregate coverage                            │
                                                   ▼
                                    [Successful eligible push CI]
                                    verify exact run and candidate
                                                   │
                                                   ▼
                                    [Automatic publication]
                                    validate and publish exact artifacts
                                    without manual deployment approval

External Snyk and GitHub CodeQL report their PR checks independently.
```

Pushes to `preview/*`, `release/*`, and `main` additionally build candidate
artifacts when the source tree contains the hardened Docker contract (a DHI
base, explicit UID/GID `65532:65532`, and `docker/entrypoint.py`). Each
`linux/amd64` and `linux/arm64` image is built once, smoke-tested, accompanied
by an SPDX SBOM, and uploaded as a GitHub Actions artifact. The trusted
publisher consumes those exact archives after successful CI. Candidate jobs have
read-only repository permissions and never push an image. Every hardened
`linux/amd64` build, including PR builds, also writes an informational Docker
Scout impact summary (Section 3.2).

Candidate archives use BuildKit's OCI exporter with max-mode SLSA provenance
(BuildKit's default version; the verifier accepts v0.2 and v1)
and an SPDX SBOM attached to each runnable platform manifest. The verifier
checks the archive's content-addressed OCI blobs, platform, source revision,
and presence of provenance and SBOM statements whose subjects identify the
exact image. It preserves those statements without enforcing BuildKit's
internal build graph, provenance build arguments or base-image materials,
or the SBOM's version and package inventory. The source-revision label and
separate Dockerfile base-reference validation remain checked. This evidence
binds publication to the tested image; it does not validate the statements'
claims or make a vulnerability judgement. GitHub's publication attestations
remain separate and unchanged.
Before upload, CI adds a classic Docker `manifest.json` import view that points
to the same config and compressed layer blobs with the candidate's local tag.
It does not replace or rewrite any OCI member, provenance, SBOM, or descriptor.
The final combined archive is checksummed once and remains the artifact used
by publication.
The promotion manifest continues to identify the
runnable platform-manifest digests, not the surrounding attestation-index
digests. Local image import is checked against the archive's platform,
complete runtime Config and root-filesystem DiffIDs. Promotion
copies each complete OCI index with `skopeo --all --preserve-digests`, then
checks that the final multi-architecture index contains every original
platform and attestation descriptor and both tested runnable digests. It
does not rebuild, rewrite, or substitute the tested image manifests. The
promoter continues to accept older Docker archives and
unattested OCI archives produced by release branches that have not yet adopted
the new exporter, but those formats do not gain BuildKit child-image evidence.

This Docker-contract check is a deliberate bootstrap compatibility gate:
the application Dockerfile and runtime smoke test remain unchanged, so
installing the trusted promotion code does not require importing the MVP
Docker/application implementation. A `release/2.0.0` branch containing that
hardened Docker implementation automatically produces candidates with
versions such as `2.0.0-beta.N`; once the same implementation reaches `main`,
GA candidates are produced there too, but they are not published while GA
publication is paused (Section 5.2).

### 4.2 Workflow Files

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push to protected branches | Canonical checks, unchanged baseline Docker smoke test, and gated multi-architecture candidate artifacts |
| `.github/workflows/auto-promote.yml` | Successful CI completion for an eligible same-repository `release/*` or `preview/*` push | Verify the exact CI candidate and automatically start publication (GA from `main` paused, Section 5.2) |
| `.github/workflows/promote-preview.yml` | Manual dispatch from `main` | Recovery/backfill path for a `preview/*` candidate |
| `.github/workflows/promote-beta.yml` | Manual dispatch from `main` | Recovery/backfill path for a `release/X.Y.Z` beta candidate |
| `.github/workflows/promote-ga.yml` | Manual dispatch from `main` (workflow disabled while GA is paused) | Recovery/backfill path for a tagged `main` candidate |
| `.github/workflows/_promote-image.yml` | Called by automatic and manual promotion workflows | Validate the successful CI candidate and publish its exact artifacts |
| `.github/workflows/_finalize-release.yml` | Called after successful beta or GA publication | Create the `vX.Y.Z-beta.N`/`vX.Y.Z` Git tag at the source SHA, create the immutable GitHub Release, and (GA only) open the develop merge-back PR |

Promotion requires a successful trusted push CI run for the exact
source SHA and branch, downloads its immutable artifacts, verifies archive checksums,
revalidates release metadata using scripts checked out from `main`, and
confirms the source SHA belongs to the requested branch and a merged pull
request (direct pushes are rejected). If that pull request has no recorded
approval — for example, an administrator merged it by bypassing branch
protection — promotion continues but emits a workflow warning and a job-summary
entry naming who merged it, so the bypass is auditable; there is no additional
deployment reviewer. Automatic promotion uses the exact CI run that triggered
it; manual recovery dispatches locate the matching run. Candidate CI and
release-script files must exactly match the trusted copies on `main`; pipeline
changes therefore land on `main` before release branches consume them. Only
then does the trusted publisher use its environment-scoped Docker Hub credentials; it pushes
each platform image by digest with `skopeo copy --preserve-digests` (so the
registry runnable manifest digests equal the tested digests; attested OCI
indexes are copied with `--all` to retain their BuildKit evidence, while legacy
archives keep the existing single-manifest path). A plain `docker push` would
re-serialise the manifest, and pushing by digest leaves no internal staging
tags. The workflow assembles the already-tested images without rebuilding.
Promotion is serialized to prevent
tag races, rejects an existing immutable version tag discovered through the
Docker Hub tags API, and attests the published
multi-architecture manifest with provenance and both platform SBOMs using
`actions/attest`. The provenance attestation also records an Artifact Metadata
storage record (`artifact-metadata: write`), so each published image appears on
the organisation's Linked Artifacts page.

Before publication, automatic and manual promotions add a
**Release candidate** table to the workflow run summary.
It shows the verified candidate's exact raw version,
image repository, publication tags, moving tag, channel, publication environment,
source branch and commit. For example, an eligible `2.0.0-beta.6` candidate
shows its immutable version tag and the conditional `2.0.0-beta-latest`
pointer. Pointer eligibility reflects the current registry inventory, not a
publication result: it is rechecked after publication and attestations, and
ineligible candidates show the pointer as unchanged. GA shows its exact version
and `latest`; preview shows only its exact version tag. All existing
release validation and publication safeguards remain in place.

For each successful eligible push, `auto-promote.yml` confirms the CI run
uploaded a promotion manifest, reads only `pyproject.toml` from its source
commit, and checks branch/channel compatibility and existing Docker Hub tags. Runs
without a candidate artifact, with a mismatched channel, or for an already
published version are skipped with a notice. An eligible run starts the matching
promotion automatically without an Environment reviewer pause. After
a successful beta or GA publication, the shared finalizer creates the Git tag
and GitHub Release (Section 5.1). For GA it also opens the main-to-develop PR;
it never merges that PR automatically.

### 4.3 Concurrency Control

CI uses branch- or PR-scoped `concurrency` groups to cancel superseded checks:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

The four root promotion workflows (`auto-promote.yml`, `promote-beta.yml`,
`promote-ga.yml`, and `promote-preview.yml`) share a workflow-level
`image-promotion` group with `cancel-in-progress: false`. This lock covers
candidate validation, publication, attestations, moving-tag updates,
and release finalization. The reusable promotion workflow does not reacquire
the lock. A newer run never automatically cancels a running publication.

GitHub concurrency retains at most one running and one pending workflow per
group; a newer pending run replaces the previous pending run, so this is not
a FIFO queue. The automatic resolver also has a separate branch-scoped
cancellation group, but only runs after its root workflow is admitted.

---

## 5. Release Process

### 5.1 Tagging and Release Strategy

Publication starts automatically after successful CI for eligible `main`,
`release/**`, and `preview/**` pushes; it never rebuilds from the registry.
Normal PR reviews remain; there is no additional Environment deployment
approval before publication. Manual
dispatch of the `promote-*` workflows is reserved for promotion recovery before
publication or backfill.

**GitHub Releases record publications; they never trigger them.** A Release is
created only after the tested image, its attestations, and any moving tag
have been published, so a Release can never exist for an image that failed to
publish. The finalizer (`_finalize-release.yml`) then:

1. creates the annotated Git tag `v<version>` at the promoted source commit
   (or confirms an existing tag already points there, failing otherwise);
2. creates one GitHub Release for that tag: a **prerelease** for betas and
   the **latest** release for GA. Preview images get no tag or Release.

Release immutability is enabled on the repository (see
[`settings/GENERAL.md`](settings/GENERAL.md)), so newly published Releases and
their tags are protected by GitHub. Previously published Releases are not
retroactively made immutable. The finalizer creates a Release complete in one
call and leaves an existing Release unchanged. Its notes keep three sections
separate:

| Section | Source |
|---|---|
| Summary | The version's `## [<version>]` section of `CHANGELOG.md` at the released commit |
| Docker image | `docker pull` command, digest-pinned image reference, and moving-tag note (`latest` or `2.0.0-beta-latest`) |
| Pull requests | GitHub-generated notes since the previous release tag on the same channel (previous beta for betas, previous GA for GA) |

`CHANGELOG.md` is the canonical, concise, user-facing summary of notable
changes per release, not a commit log. Each version has a compare link at the
end of the file (Keep a Changelog), and the generated PR notes provide full
traceability. A GA version **must** have a `CHANGELOG.md` section. CI's `Check`
job fails any pull request to `main` or `release/**` that changes
`[project].version` to a GA version without one, and the finalizer refuses to
render GA notes without it. A beta without a section emits a CI warning, and
its Release uses the generated PR notes only. Pull requests that leave the
version unchanged prepare no release and are not checked.

If tag or Release creation fails after publication, re-run the failed
finalize job; dispatching `promote-*` is not a recovery path once the version
has been published. Every finalizer step is idempotent, an existing Release is
left unchanged, and the image is never republished. Tag protection must allow
`github-actions[bot]` to create `v*` tags.

**Tag formats:**

| Tag | Example | Type |
|---|---|---|
| `vX.Y.Z` | `v1.2.0` | Stable Git tag and GitHub Release |
| `vX.Y.Z-beta.N` | `v2.0.0-beta.7` | Beta Git tag and GitHub prerelease |
| `X.Y.Z-beta.N` in `pyproject.toml` | `2.0.0-beta.1` | Beta image |
| `2.0.0-beta-latest` (Docker Hub only) | `2.0.0-beta-latest` | Temporary MVP beta pointer |

Tags and Releases are created by the finalizer; do not create them by hand
except for the one-off backfill below.

#### One-off backfill: `2.0.0-beta.3` to `2.0.0-beta.6`

These images were published before the finalizer created beta tags and
Releases. Once this change is on `main`, a maintainer with `contents: write`
backfills them once, in version order, from an up-to-date checkout of `main`.
Commits are the `org.opencontainers.image.revision` labels of the published
images, and digests are their published manifest digests (verify with
`docker buildx imagetools inspect openbanking/conformance-suite-v2:<version>`).

```bash
set -euo pipefail
git fetch --tags origin
repo=OpenBankingUK/conformance-suite-v2
while read -r version sha digest; do
  tag="v$version"
  git tag -a "$tag" "$sha" -m "Release $tag"
  git push origin "refs/tags/$tag"
  git tag -l 'v*' > /tmp/tags.txt
  previous="$(uv run python -m scripts.release_notes previous-tag --tag "$tag" --tags-file /tmp/tags.txt)"
  gh api --method POST "repos/$repo/releases/generate-notes" \
    -f tag_name="$tag" -f target_commitish="$sha" -f previous_tag_name="$previous" \
    --jq .body > /tmp/generated.md
  git show "$sha:CHANGELOG.md" > /tmp/CHANGELOG.md
  uv run python -m scripts.release_notes render --version "$version" --digest "$digest" \
    --changelog /tmp/CHANGELOG.md --generated-notes /tmp/generated.md > /tmp/notes.md
  gh release create "$tag" --repo "$repo" --verify-tag --prerelease --latest=false \
    --title "$tag" --notes-file /tmp/notes.md
done <<'BACKFILL'
2.0.0-beta.3 01a6b644a0f0a4705695a5766c74b671e49bb924 sha256:78756bcf3b9a17cf305b98ae3c72544575a39e23723ca6ed1f46474cf4d1f7fe
2.0.0-beta.4 005633adfe0fc4d5513895048299c2add53b530b sha256:32c5a92d5285f5c8dde9d69e5514492cf5c100abc0cdfe4ff16aa17c01a10757
2.0.0-beta.5 398a1051198f61d0e3d684864fa12a19a7721014 sha256:090927066b23946e7e4c696e2f22ec81e586ef5ed9b60ee428be3f8630d2d080
2.0.0-beta.6 747409f8336d1d491e2c4265132d1faeabd11e5f sha256:cbdf840d899b7bcbad4dcc4b9af16523340c7d1765265ef191124d934680e617
BACKFILL
```

The backfilled notes omit the `2.0.0-beta-latest` line because the pointer has
since moved. `v2.0.0-beta.1` and `v2.0.0-beta.2` already have Releases.

---

### 5.2 Standard Release (Git Flow)

> **GA publication is paused while the 2.0.0 MVP is in beta.** `main` has no
> publication approval and its placeholder version is a valid GA version, so
> `auto-promote.yml` ignores `main` pushes and the **Promote GA image** workflow
> is disabled in repository Actions settings. To resume GA, restore `main` to
> the `auto-promote.yml` branch guards and their tests, merge that change, then
> re-enable **Promote GA image**. The steps below describe the resumed process.

```
1. Create release branch from develop:
   git checkout develop && git checkout -b release/1.2.0

2. Stabilise on release branch (version bump, changelog, final fixes)
   - The `## [X.Y.Z]` CHANGELOG.md section is mandatory; CI blocks the PR
     to main without it
   - CI runs automatically on every push

3. Open PR: release/1.2.0 → main
   - Full CI gates enforced (`make check`, Docker build and health check)
   - Requires the normal human PR review; Copilot review remains advisory

4. Merge into main (squash or merge commit). Successful `main` push CI
   produces the GA candidate, then automatic promotion starts.

5. The workflow validates and
   publishes the exact candidate artifacts to Docker Hub as `X.Y.Z` and `latest`,
   with provenance and SBOM attestations.

6. After publication, the finalizer creates `vX.Y.Z` at the promoted source
   commit if it is missing, creates the GitHub Release (marked latest), and
   opens the main-to-develop PR. Review and merge that PR; it is never merged
   automatically.

Manual **Promote GA image** dispatch is available from `main` only for
recovery/backfill.
```

### 5.3 Beta Releases

Beta releases allow pre-release images to be distributed before a final stable tag.

```
1. Set `[project].version` to `X.Y.Z-beta.N` on the matching
   `release/X.Y.Z` branch, add its `## [X.Y.Z-beta.N]` CHANGELOG.md section
   (CI warns if it is missing), and merge through an approved PR.
2. Wait for that exact branch SHA's push CI run to produce both candidate
   artifacts and the promotion manifest. Automatic promotion then starts.
3. The workflow publishes the exact
   artifacts as immutable
   `X.Y.Z-beta.N`, completes provenance and both platform SBOM attestations,
   then updates `2.0.0-beta-latest` if this is the highest published 2.0.0 beta
   and the formal `2.0.0` version has not been published.
   It does not create or move GA `latest`. The finalizer then creates the
   `vX.Y.Z-beta.N` tag and a GitHub prerelease (Section 5.1).
4. Increment `N` in a new approved change for each subsequent beta.

Manual **Promote beta image** dispatch is available from `main` only for
recovery/backfill.
```

The developer does not set `2.0.0-beta-latest` in `pyproject.toml` or run another
workflow. Both automatic and manual beta promotions maintain it through the
same automatic publication path. Docker Hub's complete published
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
seed or repair workflow. The alias is seeded from the `2.0.0-beta.4` manifest;
future eligible beta promotions maintain it.

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
   - PR against main: requires CI pass and the normal human review

3. After merge, successful `main` candidate CI automatically starts
   publication of the exact image to Docker Hub. The
   finalizer creates the `vX.Y.Z` tag at the promoted source commit and the
   GitHub Release. The hotfix PR must include its CHANGELOG.md section.

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
| Security fixes during a beta | Shipped-image fixes may target `release/X.Y.Z` first; pipeline files go to `main` first (Section 3.2) |

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
