# GitHub Settings: Code Quality

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Code quality
> **Last reviewed**: 30 April 2026

---

## Code Quality

| Setting | Value |
|---------|-------|
| Code Quality | **Enabled** |

**Rationale**: GitHub's Code Quality feature provides quality metrics and ratings for merged code, surfacing trends and regressions. Enabled proactively so that analysis begins automatically once source code is committed and CodeQL default setup activates.

---

## Default setup

| Setting | Value |
|---------|-------|
| Default setup | **Enabled** |
| Setup state | Not yet active (no source code committed) |

**Rationale**: Default setup automatically detects supported languages and configures CodeQL-based analysis without requiring a custom workflow file. Once the initial Python application code lands, the feature will activate without further manual configuration.

---

## Languages

| Setting | Value |
|---------|-------|
| Detected languages | None |

**Rationale**: No source code has been committed to the repository yet. Python will be automatically detected once the initial codebase is pushed. No manual language selection is required with default setup.

---

## Analysis schedule

| Setting | Value |
|---------|-------|
| Default branch | `main` |
| Next scheduled run | None (pending first code commit) |

**Rationale**: Analysis will be scheduled automatically once default setup detects committed source code. Runs on every push to the default branch and on pull requests targeting it.

---

## Runner configuration

| Setting | Value |
|---------|-------|
| Runner type | **Standard** (GitHub-hosted) |
| Runner label | None (default) |
| Only labeled runners | `false` |
| macOS runner available | `true` |

**Rationale**: Standard GitHub-hosted runners are sufficient for Python CodeQL analysis. No need for self-hosted or specially labeled runners. Python analysis is not resource-intensive and completes well within the default runner timeout.

---

## Recent activity monitoring

| Setting | Value |
|---------|-------|
| Recent activity | **Enabled** |

**Rationale**: Surfaces quality metrics and trends for recently merged pull requests. Provides visibility into whether code quality is improving or regressing over time. Valuable for a conformance testing tool where code correctness is critical.

---

## Post-setup checklist

Once initial Python source code is committed, verify the following without manual intervention:

- [ ] Python appears in the detected languages list
- [ ] A scheduled analysis run is populated
- [ ] The workflow run URL is active and the first run completes successfully
- [ ] Code quality metrics begin appearing on merged PRs
