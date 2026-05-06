# GitHub Settings: Advanced Security

> **Repository**: `OpenBankingUK/conformance-suite-v2`
> **Page**: Settings → Advanced Security
> **Last reviewed**: 30 April 2026

---

## Secret Protection

| Setting | Value |
|---------|-------|
| Secret Protection | **Enabled** |

**Rationale**: Core security feature for a financial services repository. Scans for leaked secrets (API keys, tokens, credentials) in all pushes and repository history. GitHub always sends alerts to partners for detected secrets in public repositories regardless of this setting.

> Secret Protection is billed per 90-day active committer across private and internal repositories.

---

### Validity checks — Enabled

| Setting | Value |
|---------|-------|
| Validity checks | Enabled |

Automatically verifies if a detected secret is still valid by sending it to the relevant partner. Provides immediate signal on whether a leaked credential requires urgent rotation.

---

### Extended metadata — Disabled (Preview)

| Setting | Value |
|---------|-------|
| Extended metadata | Disabled |

**Rationale**: Preview feature. Automatically checks for extended metadata about a secret by sending it to the relevant partner. Left disabled until the feature exits preview and is assessed for suitability in a regulated context.

---

### Non-provider patterns — Enabled

| Setting | Value |
|---------|-------|
| Non-provider patterns | Enabled |

**Rationale**: Scans for patterns that don't correspond to a specific provider (e.g. generic connection strings, private keys). Broadens detection coverage beyond known provider tokens. Acceptable noise-to-signal ratio for a security-critical repository.

---

### Scan for generic passwords — Enabled

| Setting | Value |
|---------|-------|
| Scan for generic passwords | Enabled |

**Rationale**: Uses Copilot-powered AI to detect hardcoded passwords that don't match known provider patterns. Essential for catching ad-hoc credentials in configuration files and test data.

---

### Push protection — Enabled

| Setting | Value |
|---------|-------|
| Push protection | Enabled |

**Rationale**: Blocks commits containing supported secrets before they enter the repository. Prevents secrets from ever appearing in git history, which is significantly cheaper than post-commit remediation (secret rotation, history rewriting).

---

### Push protection bypass

| Setting | Value |
|---------|-------|
| Who can bypass push protection | Specific roles or teams |

#### Bypass list

| Actor | Type |
|-------|------|
| OB SPS Admin | Team |
| admin | Repository Role |

**Rationale**: Restricts push protection bypass to a controlled set of actors rather than allowing anyone with write access to bypass. The bypass list includes:

- **OB SPS Admin** (Team): The security/platform services admin team whose members may need to bypass in exceptional circumstances (e.g. committing test fixtures containing example secrets).
- **Repository admin** (Role): The repository administrator (who is not a member of the OB SPS Admin team) needs independent bypass capability for emergency scenarios.

Everyone else must submit a bypass request for review.

---

### Prevent direct alert dismissals (Secret scanning) — Disabled

| Setting | Value |
|---------|-------|
| Prevent direct alert dismissals | Disabled |

**Rationale**: Not currently enforced. The bypass list for push protection already restricts who can push secrets. Alert dismissal is managed through review culture rather than workflow enforcement at this stage. May be revisited as the team grows.

---

### Custom patterns

| Setting | Value |
|---------|-------|
| Custom patterns | None defined |

**Rationale**: No organisation-specific secret patterns have been identified yet. Will be configured when the application code introduces proprietary token formats (e.g. participant API keys issued by the conformance tool itself).

---

## Code Security

| Setting | Value |
|---------|-------|
| Code Security | Enabled |

> Code Security features are free for public repositories and billed per 90-day active committer for private/internal repositories.

---

### CodeQL analysis — Active

| Setting | Value |
|---------|-------|
| CodeQL analysis | Active (via default setup) |

**Rationale**: CodeQL default setup is enabled and has automatically detected Python. Analysis runs on pushes to the default branch and on pull requests targeting it.

---

### Other tools

Third-party code scanning tools can be added via GitHub Actions workflows. No additional tools are configured at this time. Snyk is integrated separately as a GitHub status check via the Snyk portal.

---

### Copilot Autofix — On

| Setting | Value |
|---------|-------|
| Copilot Autofix | On |

**Rationale**: Automatically suggests fixes for CodeQL alerts using AI. Reduces mean time to resolution for code scanning findings. Now effective with CodeQL default setup active.

---

### Prevent direct alert dismissals (Code scanning) — Off

| Setting | Value |
|---------|-------|
| Prevent direct alert dismissals | Off |

**Rationale**: Same reasoning as the Secret scanning equivalent. Alert dismissal governance is handled through PR review process (2 required approvals including Copilot code review). May be revisited as the codebase matures.

---

### Protection rules — Check runs failure threshold

| Setting | Value |
|---------|-------|
| Security alert severity level | High or higher |
| Standard alert severity level | Only errors |

**Rationale**:

- **Security alerts**: Code scanning check runs fail on `High` or `Critical` severity security findings. This aligns with the Snyk policy (high/critical block merge) and ensures genuinely dangerous vulnerabilities cannot be merged without explicit override via branch rulesets.
- **Standard alerts**: Only `error`-level findings (not warnings or notes) cause check run failure. Prevents noise from stylistic or informational findings blocking PRs while still catching genuine code quality errors.

> These thresholds apply to code scanning check runs. A branch ruleset must be configured to enforce merge blocking based on these check results.

---

## Private vulnerability reporting — Enabled

| Setting | Value |
|---------|-------|
| Private vulnerability reporting | Enabled |

**Rationale**: Allows the community (including Open Banking UK participants) to privately report potential security vulnerabilities to maintainers. Essential for a public repository used by financial services participants — provides a secure disclosure channel without requiring reporters to file public issues.

---

## Dependency graph — Enabled

| Setting | Value |
|---------|-------|
| Dependency graph | Enabled |

**Rationale**: Prerequisite for Dependabot alerts and security updates. Parses dependency manifests (`pyproject.toml`, `uv.lock`) to understand the repository's dependency tree.

---

### Automatic dependency submission — Disabled

| Setting | Value |
|---------|-------|
| Automatic dependency submission | Disabled |

**Rationale**: Automatically detects and reports build-time dependencies for select ecosystems. Disabled because `uv.lock` provides a complete, deterministic dependency graph that the standard dependency graph already parses correctly. Automatic submission would add noise without improving coverage.

---

## Dependabot

---

### Dependabot alerts — Enabled

| Setting | Value |
|---------|-------|
| Dependabot alerts | Enabled |

**Rationale**: Receives alerts for known vulnerabilities in dependencies. Core security hygiene for a project that ships as a Docker container to financial services participants. Alert notifications are configured in user notification settings.

---

### Dependabot rules

| Setting | Value |
|---------|-------|
| Dependabot rules | 1 rule enabled |

**Active rules**:

| Rule | Type | Description |
|------|------|-------------|
| Dismiss low-impact alerts for development-scoped dependencies | Preset | Auto-dismisses low-severity alerts on dev-only dependencies that don't ship in the production container |

**Rationale**: Development dependencies (test frameworks, linters, type checkers) are not present in the production Docker image. Low-impact vulnerabilities in these packages pose negligible risk and would otherwise create alert fatigue. The "Dismiss package malware alerts" preset is correctly **not** enabled — malware in any dependency (including dev) is always actionable.

---

### Dependabot malware alerts — Enabled

| Setting | Value |
|---------|-------|
| Dependabot malware alerts | Enabled |

**Rationale**: Malware detection in dependencies is critical regardless of whether the dependency is runtime or development-scoped. A compromised dev dependency could exfiltrate secrets from CI or developer machines.

---

### Prevent direct alert dismissals (Dependabot) — Disabled

| Setting | Value |
|---------|-------|
| Prevent direct alert dismissals | Disabled |

**Rationale**: Consistent with the approach for Secret scanning and Code scanning alert dismissals. Governance is handled through PR review rather than workflow enforcement.

---

### Dependabot security updates — Enabled

| Setting | Value |
|---------|-------|
| Dependabot security updates | Enabled |

**Rationale**: Automatically opens PRs to resolve open Dependabot alerts with available patches. Reduces mean time to remediation for known vulnerabilities. PRs are subject to the same CI checks (`lint`, `test`, `security-scan`, `docker-build`, `e2e`) and review requirements as any other PR.

---

### Grouped security updates — Enabled

| Setting | Value |
|---------|-------|
| Grouped security updates | Enabled |

**Rationale**: Groups all available security updates into one PR per package manager and directory. Reduces PR noise while still ensuring all vulnerabilities are addressed. Can be overridden by group rules in `dependabot.yml` if more granular control is needed later.

---

### Dependabot version updates — Disabled

| Setting | Value |
|---------|-------|
| Dependabot version updates | Disabled |

**Rationale**: Version updates (non-security) are intentionally not automated. Dependency upgrades should be deliberate and tested — especially for a conformance testing tool where behaviour reproducibility matters. Version bumps are handled manually during planned maintenance windows or via explicit `uv lock --upgrade` operations. A `dependabot.yml` file would be required to enable this.