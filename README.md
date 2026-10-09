# Functional Conformance Suite v2 beta.11

[![CI (main)](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/ci.yml?query=branch%3Amain)
[![Image promotion](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/auto-promote.yml/badge.svg)](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/auto-promote.yml)
[![Security re-scan](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/security-rescan.yml/badge.svg?branch=main)](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/security-rescan.yml)

**Beta only — not for certification.** FCS v2 `2.0.0-beta.12` is an MVP for
evaluating the new Open Banking UK Functional Conformance Suite and providing
feedback. It is **not approved for certification**; runs and reports from this
beta must not be submitted as certification evidence, even if a plan or result
mentions certification or appears eligible in the UI.

This applies to the published Docker image, a locally built image, and runs
from source. **Keep using the existing FCS v1 for certification.** Open Banking
UK will announce when a later FCS v2 release is formally approved; there is no
need to migrate your certification process in advance.

## Deployment and security highlights

| Highlight | Live status and details |
| --- | --- |
| Published images | [Docker Hub](https://hub.docker.com/r/openbanking/conformance-suite-v2/tags) and [GitHub releases](https://github.com/OpenBankingUK/conformance-suite-v2/releases). Use `2.0.0-beta-latest` for evaluation or an exact published tag for reproducibility. |
| Image deployment | [Automatic promotion runs](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/auto-promote.yml) show publication and release finalization. [Release policy](docs/CICD_STRATEGY.md#51-tagging-and-release-strategy) explains approvals and immutable-image promotion. |
| Vulnerabilities | [Open vulnerability issues](https://github.com/OpenBankingUK/conformance-suite-v2/issues?q=is%3Aissue%20is%3Aopen%20label%3Avulnerability) track scheduled scan findings; [Dependabot alerts](https://github.com/OpenBankingUK/conformance-suite-v2/security/dependabot) cover repository dependency alerts (access required). |
| Scan evidence | [Security re-scan runs](https://github.com/OpenBankingUK/conformance-suite-v2/actions/workflows/security-rescan.yml) provide reports for published images and default/release branches. See the [vulnerability gate and exceptions policy](docs/CICD_STRATEGY.md#32-vulnerability-scanning-and-the-pr-gate). |

Badges report workflow status: CI is scoped to `main`, image promotion spans
release channels, and security re-scans run daily or on demand. Promotion can
skip publication when no eligible candidate exists; inspect its run and the
registry/release links to confirm what was published. A green scan badge means
the workflow passed its policy, not that every image has zero vulnerabilities
or that this beta is approved for certification.

## Documentation

| Category | Start here | What you will find |
| --- | --- | --- |
| Installation | [Installation guide](docs/INSTALLATION_GUIDE.md) | Prerequisites, Docker or source setup, readiness and troubleshooting. [Docker deployment reference](docs/DOCKER_GUIDE.md) covers persistence, certificates and Compose. |
| Usage | [Usage guide](docs/USAGE_GUIDE.md) | Build/import a plan, supply credentials, run tests, complete PSU authorisation, interpret/download results and give feedback; advanced CLI and REST workflows. |
| Developer / decision logs | [Developer guide](docs/DEVELOPER_GUIDE.md) and [Decision log](docs/DECISION_LOG.md) | Onboarding, architecture, local checks, contributing changes and recorded design choices with evidence and tradeoffs. |

## Get started: local browser UI

With Docker installed and running, start the beta on your own computer:

```bash
docker run --pull=always --rm -p 127.0.0.1:8443:8443 openbanking/conformance-suite-v2:2.0.0-beta-latest
```

`2.0.0-beta-latest` tracks the newest published 2.0.0 beta. `--pull=always`
refreshes the image at launch, not an already running container.

Open `https://127.0.0.1:8443/`. The container generates a local self-signed
HTTPS certificate, so your browser will show a certificate warning. The port
binding keeps the UI local to your computer.

Choose **Create new test plan with builder**, follow the prompts, and supply the
credentials requested for your scope using **Paste or upload** and **Paste PEM
text**. Review the plan and choose **Launch run**; some tests require runtime
inputs or PSU authorisation. Save results before stopping with Ctrl+C: this
disposable command does not retain sessions, results or logs across runs.

Continue with the [browser walkthrough](docs/USAGE_GUIDE.md#browser-builder-workflow).
For durable storage or source setup, use the [Installation guide](docs/INSTALLATION_GUIDE.md).

## Give beta feedback

Choose **Give beta feedback** on any browser page to prepare a diagnostic ZIP
and email text for `standardsteam@openbanking.org.uk`. Nothing is uploaded or
sent automatically. Review every file and manually attach the ZIP to your
email; masking does not remove all potentially sensitive data.
See [feedback instructions and retention limits](docs/USAGE_GUIDE.md#give-beta-feedback).

## Alternative: run from source

Follow [source installation](docs/INSTALLATION_GUIDE.md#alternative-run-from-source)
to run the same UI with Python and uv. For code changes, start with the
[Developer guide](docs/DEVELOPER_GUIDE.md).

## Browser builder workflow

The [Usage guide](docs/USAGE_GUIDE.md#browser-builder-workflow) walks through
specification and scope selection, discovery, credentials, business data,
review, import/export and launch.

## Advanced reference: CLI plan execution

Use a canonical schemaVersion `1.0` test plan:

```bash
uv run python main.py --test-plan path/to/test-plan.json
```

See [CLI reference](docs/USAGE_GUIDE.md#advanced-reference-cli-plan-execution)
for plan structure and supported configuration.

### PSU authorisation and pipeline runs

See [manual and auto-approve modes](docs/USAGE_GUIDE.md#psu-authorisation-and-pipeline-runs)
for registered callbacks, listener configuration and sandbox requirements.

### OpenAPI document updates

See [update selection and supported versions](docs/USAGE_GUIDE.md#openapi-document-updates).
Read/Write CLI/REST plans must select an `openApiDocumentUpdate`.

## Browser and REST launch

See [REST creation, status and evidence routes](docs/USAGE_GUIDE.md#browser-and-rest-launch).
The API is intended for local programmatic use, not public exposure.

## Bundled catalogues

Read/Write `3.1.11`, `4.0.0` and `4.0.1` cover AIS, PIS, CBPII and VRP;
Dynamic Client Registration `3.4` has a separate direct-endpoint workflow.
See [catalogue reference](docs/USAGE_GUIDE.md#bundled-catalogues),
[endpoint requirements](docs/READ_WRITE_ENDPOINT_REQUIREMENTS.md),
[legacy benchmark mapping](docs/FCS_LEGACY_BENCHMARK_MAPPING.md) and
[DCR parity contract](docs/DCR_3_4_PARITY_CONTRACT.md).

## Outputs and exit codes

See [results, execution logs, exit codes and cancellation](docs/USAGE_GUIDE.md#outputs-and-exit-codes).
Use structured results for automation, not incidental console text.

## Internal certification report validation (not for beta submissions)

The [internal validator](docs/DEVELOPER_GUIDE.md#certification-validation) is
for OBL reviewers. Its output does not approve this beta for certification.

### Phase 1 assurance boundary

Local report validation checks consistency against independently supplied
criteria; it does not authenticate participant-controlled evidence or make a
formal certification decision. See [local report assurance](docs/DECISION_LOG.md#dl-003-local-report-assurance).
