# GitHub Settings: Actions General

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Actions → General
> **Last reviewed**: 30 April 2026

---

## Actions permissions

| Setting | Value |
|---------|-------|
| Policy | **Allow enterprise, and select non-enterprise, actions and reusable workflows** |
| Allow actions created by GitHub | ✅ Checked |
| Allow actions by Marketplace verified creators | ❌ Unchecked |

**Rationale**: The `selected` policy restricts which external actions can run in CI workflows. Enterprise-scoped actions are allowed by default. GitHub-authored actions (e.g. `actions/checkout`, `actions/setup-python`) are permitted as they are first-party and maintained by GitHub. Marketplace verified creators are not yet enabled — the allowlist is kept minimal and can be expanded as needs arise.

---

## Allowed actions and reusable workflows

| Pattern |
|---------|
| `astral-sh/setup-uv` |
| `docker/*` |
| `snyk/actions/*` |

**Rationale**: Only the actions required for the CI pipeline are allowlisted:

- **`astral-sh/setup-uv`** — Required for installing `uv` in CI to manage Python and dependencies.
- **`docker/*`** — Required for building, tagging, and pushing the conformance suite Docker container image.
- **`snyk/actions/*`** — Required for dependency and container security scanning as part of the CI security-scan job.

The principle is to remain closed by default and open incrementally.

---

## SHA pinning

| Setting | Value |
|---------|-------|
| Require full-length commit SHA | ✅ Required |

**Rationale**: Pinning actions to a full-length commit SHA prevents tag-based supply chain attacks where a malicious commit is force-pushed to a tag. This is a critical security control for a financial services conformance tool. All workflow files must reference actions by SHA rather than mutable tags.

---

## Artifact and log retention

| Setting | Value |
|---------|-------|
| Retention period | **90 days** |
| Organisation maximum | 90 days |

**Rationale**: Set to the organisation maximum. Conformance test artifacts and CI logs should be retained for the full allowed period to support audit trails and debugging of historical test runs. The 90-day limit is inherited from the organisation policy.

---

## Cache

| Setting | Value |
|---------|-------|
| Cache retention | **7 days** |
| Enterprise maximum | 7 days |
| Cache size eviction limit | **10 GB** |
| Enterprise maximum | 10 GB |

**Rationale**: Both cache settings are at the enterprise-enforced maximum. The 7-day retention and 10 GB size limit are sufficient for caching `uv` dependencies and Docker layer caches across CI runs. These values cannot be increased beyond the enterprise ceiling.

---

## Fork pull request approval

| Setting | Value |
|---------|-------|
| Approval policy | **Require approval for all external contributors** |

**Rationale**: The most restrictive option. All users who are not members or owners of the repository, and not members of the OpenBankingUK organisation, require explicit approval before their pull request workflows run. This prevents untrusted code from executing in CI and consuming Actions minutes or accessing secrets. Essential for a public-facing repository in a regulated financial services context.

---

## Workflow permissions

| Setting | Value |
|---------|-------|
| Default GITHUB_TOKEN permissions | **Read** (repository contents and packages only) |
| Allow Actions to create and approve PRs | ❌ Unchecked |

**Rationale**: Follows the principle of least privilege. Workflows receive read-only access by default; any workflow that requires write permissions must explicitly declare them via the `permissions` key in the workflow YAML. Disabling PR creation and approval by Actions prevents automated workflows from self-approving changes, which would undermine the required code review process.

---

## Post-setup checklist

Once CI workflows are committed, verify:

- [ ] Workflows can resolve and use `actions/checkout`, `actions/setup-python`, and other GitHub-authored actions
- [ ] `docker/*` actions resolve correctly in the container build job
- [ ] `snyk/actions/*` actions resolve correctly in the security scan job
- [ ] Actions pinned by SHA execute without permission errors
- [ ] Fork PRs from external contributors are held for approval before workflows run
- [ ] Workflow GITHUB_TOKEN has read-only access unless explicitly escalated in YAML
