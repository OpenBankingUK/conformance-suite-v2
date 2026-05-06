# GitHub Settings: Code Quality

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Code quality
> **Last reviewed**: 6 May 2026

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
| Setup state | Active |

**Rationale**: Default setup automatically detects supported languages and configures CodeQL-based analysis without requiring a custom workflow file. Python has been detected and analysis is active.

---

## Languages

| Setting | Value |
|---------|-------|
| Detected languages | Python |

**Rationale**: Python is automatically detected from the committed source code. No manual language selection is required with default setup.

---

## Analysis schedule

| Setting | Value |
|---------|-------|
| Default branch | `main` |
| Next scheduled run | Scheduled (automatic) |

**Rationale**: Analysis runs automatically on every push to the default branch and on pull requests targeting it.

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

- [x] Python appears in the detected languages list
- [x] A scheduled analysis run is populated
- [ ] The workflow run URL is active and the first run completes successfully
- [ ] Code quality metrics begin appearing on merged PRs
