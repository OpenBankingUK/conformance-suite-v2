# Developer Guide

## Prerequisites

- Python 3.14+ (managed via `.python-version`)
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for container builds)
- GNU Make

## Getting Started

```bash
# Clone the repo
git clone <repo-url>
cd conformance-suite-v2

# Install all dependencies (including dev tools)
# uv includes the [dependency-groups] dev group by default.
# Production builds (Dockerfile) use --no-dev to exclude dev tooling.
# --no-install-project: this is a container-deployed app, not a distributable package.
uv sync --frozen --no-install-project

# Activate the pre-commit hook (one-time setup per clone)
git config core.hooksPath .githooks
```

## Running the Application

```bash
make dev         # Django dev server (auto-reload, debug error pages)
make serve       # Uvicorn locally (mirrors production, no reload)
make docker      # Build and run the Docker container
```

| Command | Server | Auto-reload | Use case |
|---------|--------|-------------|----------|
| `make dev` | Django `runserver` | Yes | Day-to-day development |
| `make serve` | Uvicorn | No | Test production behaviour locally |
| `make docker` | Uvicorn (container) | No | Full production-like environment (requires `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`) |

`make dev` and `make serve` work with zero configuration. `make docker` requires environment variables (see [Environment Variables](#environment-variables)).

All targets serve on `http://localhost:8000`.

## Local Checks

Run `make check` before pushing. This mirrors the CI pipeline and runs:

1. **Secret scanning** — detects leaked credentials, tokens, API keys
2. **Linting** — ruff lint + format checks
3. **Type checking** — mypy in strict mode
4. **Tests** — pytest (unit + integration, excluding E2E)

```bash
make check       # Full check (secrets → lint → test)
make secrets     # Secret scanning only
make lint        # Ruff + mypy only
make test        # Tests only
make help        # Show all targets
```

No environment variables are needed for `make check` — `settings.py` provides a safe `django-insecure-` fallback when `DJANGO_SECRET_KEY` is absent, allowing tooling (mypy, pytest) to boot Django without external configuration.

## Pre-Commit Hook

The `.githooks/pre-commit` hook runs automatically on every `git commit`. It scans **staged files only** for secrets using `detect-secrets`.

If a secret is detected:
- The commit is **blocked**
- You'll see which file and line triggered the detection
- Fix the code (move the value to an environment variable or `.env`)

To bypass in an emergency (e.g. confirmed false positive):
```bash
git commit --no-verify
```

### Setup

The hook is activated by pointing git at the `.githooks/` directory:

```bash
git config core.hooksPath .githooks
```

This is a per-clone setting. Every developer must run this once after cloning.

## Secret Scanning

We use [detect-secrets](https://github.com/Yelp/detect-secrets) to prevent credentials from being committed.

### How It Works

- **`.secrets.baseline`** — tracks scanner configuration and audited findings. Committed to the repo so the team shares the same state.
- **`detect-secrets-hook`** — the checking command. Compares staged files against the baseline and fails if new unaudited secrets are found.

### Handling False Positives

If `detect-secrets` flags something that isn't a real secret:

```bash
# Regenerate the baseline (picks up the new finding)
uv run detect-secrets scan --exclude-files '\.env$' --exclude-files 'uv\.lock$' > .secrets.baseline

# Audit — interactively mark findings as true/false positives
uv run detect-secrets audit .secrets.baseline

# Commit the updated baseline
git add .secrets.baseline
git commit -m "chore: update secrets baseline"
```

Alternatively, add an inline comment to suppress a specific line:
```python
KNOWN_PUBLIC_VALUE = "not-a-secret"  # pragma: allowlist secret
```

### What Gets Scanned

The scanner checks for: AWS keys, Azure storage keys, GitHub/GitLab tokens, JWTs, high-entropy strings, private keys, Stripe/Twilio/SendGrid keys, hardcoded passwords (via keyword detection), and more.

Excluded from scanning: `.env` files, `uv.lock`.

## CI Pipeline

The GitHub Actions CI workflow (`.github/workflows/ci.yml`) runs the same checks on every PR and push to protected branches:

| Job | What it does |
|-----|--------------|
| **Lint & Type Check** | `ruff check`, `ruff format --check`, `mypy` |
| **Unit & Integration Tests** | `pytest` with coverage reporting |
| **Docker Build & Smoke Test** | Builds the container image and verifies it starts and passes a health check |

CI uses a hardcoded dummy `DJANGO_SECRET_KEY` — this is intentional and not a security concern (it's never used in production).

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DJANGO_SECRET_KEY` | Production only | Django cryptographic signing key. Falls back to a safe `django-insecure-` value for local tooling. |
| `DJANGO_DEBUG` | No | Set to `"true"` for debug mode (default: `"false"`) |
| `DJANGO_ALLOWED_HOSTS` | Production only | Comma-separated allowed hosts. Enforced when `DJANGO_SECRET_KEY` is explicitly set and `DEBUG` is off. |
| `CONFORMANCE_TOOL_VERSION` | No | Optional tool version stamped into generated reports. Docker builds can set this through `--build-arg CONFORMANCE_TOOL_VERSION=<version>`; source runs fall back to `pyproject.toml`. |

### How environment variables are managed per context

| Context | Who provides env vars | Notes |
|---------|----------------------|-------|
| `make check` (lint/test) | No env vars needed | `settings.py` uses a safe `django-insecure-` fallback for `SECRET_KEY`; production guards are skipped |
| `make dev` | Makefile sets `DJANGO_DEBUG=true` | Django ignores `ALLOWED_HOSTS` when `DEBUG=True` |
| `make serve` | Makefile sets `DJANGO_ALLOWED_HOSTS` | Allows `localhost` and `127.0.0.1` for local Uvicorn |
| `make docker` | Caller must provide both vars | Simulates production — fails fast if misconfigured |
| Production | Orchestrator (K8s, ECS, Compose) | Must set a real `SECRET_KEY` and `ALLOWED_HOSTS` |

**Design principle:** `settings.py` is declarative — it requires correct configuration and fails loudly when misconfigured. The Makefile provides developer ergonomics per context. An unconfigured production deployment will reject all requests (not silently degrade).

**For local Docker** (production simulation):
```bash
DJANGO_SECRET_KEY="my-local-key" DJANGO_ALLOWED_HOSTS="localhost" make docker  # pragma: allowlist secret
```

## Certification Report Validation

The OBL-side certification validator is an internal CLI that checks a participant-submitted report against the manifest used for the run and an externally supplied approved-release policy. It deliberately recomputes mandatory coverage from the manifest instead of trusting the participant-side `certificationEligibility` block.

```bash
uv run python -m conformance.certification_cli out/test-results.json \
  --manifest config/manifest-v1-openid-jwks-example.json \
  --approved-releases config/approved-fcs-releases-example.json
```

Use `--summary-output path/to/summary.txt` to write the Confluence-ready summary to disk instead of stdout. Exit codes are `0` for a valid report, `1` for validation failures, `2` for invalid inputs, and `3` when the summary output cannot be written.

The approved-release policy shape is intentionally small:

```json
{
  "schemaVersion": "v1",
  "approvedToolVersions": ["OBL-APPROVED-RELEASE-VERSION"]
}
```

`schemaVersion` must currently be `v1`; `approvedToolVersions` is an exact-match list compared against `tool.version` in the submitted report. `config/approved-fcs-releases-example.json` is parseable but non-authoritative and uses a placeholder value so it cannot accidentally certify a development build.

Generated reports carry the metadata consumed by the validator as top-level fields:

```json
{
  "metadata": {"reportVersion": "1.0"},
  "tool": {"version": "0.1.0"}
}
```

The validator treats mandatory steps with `passed` or `warn` status as acceptable. Mandatory `failed`, `skipped`, or missing steps are blocking, as is a `tool.version` absent from the approved-release policy.

Participant config may include optional `approvedReleasePolicyPath` to populate the generated report's advisory `certificationEligibility.approvedRelease` block. CLI config resolves the path relative to the config file directory; API and browser-submitted config resolves it relative to the process working directory. In both cases the path must remain inside that root and point to an existing JSON file. Policy absence and unapproved `tool.version` values both make the participant-side `certificationEligibility.eligible` value `false`, with reasons retained in the report for audit/debugging. This participant self-assessment is useful feedback, but it is not trusted for certification decisions; OBL-side validation still supplies its own policy and recomputes the result.

## Config-Driven Suite Resolution

Participant configuration can select a bundled manifest catalog entry through an optional `testSuite` object. Existing configs without `testSuite` remain valid model-bank smoke-check configs, and unknown config keys continue to be rejected.

```json
{
  "environment": "ozone-model-bank",
  "discoveryUrl": "https://auth1.obie.uk.ozoneapi.io/.well-known/openid-configuration",
  "timeoutSeconds": 10,
  "approvedReleasePolicyPath": "approved-fcs-releases-example.json",
  "testSuite": {
    "standard": "ob-read-write",
    "specVersion": "v4.0",
    "profile": "fapi1-advanced",
    "suite": "discovery-jwks"
  },
  "tls": {
    "certificatePathRoot": "./certs"
  },
  "resultOutputPath": "./out/test-results.json",
  "executionLogPath": "./out/execution-log.ndjson"
}
```

The first supported catalog keys are intentionally narrow:

| `standard` | `specVersion` | `profile` | `suite` | Scope |
| --- | --- | --- | --- | --- |
| `ob-read-write` | `v3.1.11` | `fapi1-advanced` | `discovery-jwks` | Smoke-level OpenID discovery and JWKS checks. |
| `ob-read-write` | `v4.0` | `fapi1-advanced` | `discovery-jwks` | Smoke-level OpenID discovery and JWKS checks. |

These entries live in the application package under `conformance/suites/` so Docker and API execution do not depend on the caller's working directory. The example manifests under `config/manifest-*-example.json` remain authoring examples and validator inputs, not catalog internals.

Bundled suite manifests are v1 manifests. Mandatory steps are declared in the manifest JSON itself, not hardcoded in Python. The current `discovery-jwks` entries use `${config.discoveryUrl}` for the first request and `${steps.openid-discovery.response.body.jwks_uri}` for the JWKS follow-up, which keeps version/suite selection in participant config while leaving runtime values in the single config file.

Manifest access to config is deliberately allow-listed. The only supported config placeholders are `${config.discoveryUrl}` and `${config.environment}`, accepted in the same URL/header/body leaves as existing step placeholders. Parser validation accepts those config placeholders without weakening strict forward-reference checks for `${steps.<id>...}` placeholders. TLS paths, private-key material, future credential fields, request objects, and arbitrary nested config traversal are intentionally not exposed through placeholders.

CLI precedence is:

1. `--manifest manifest.json` executes the explicit manifest, even when config also contains `testSuite`.
2. With no `--manifest`, `config.testSuite` resolves and executes the bundled catalog manifest.
3. With neither `--manifest` nor `testSuite`, the legacy model-bank smoke check runs.

`--deselect` is valid for cases 1 and 2 only, because those cases have a v1 test plan. It exits with code `2` when used against a plain smoke check or with an unknown step id.

REST API precedence matches the CLI. `POST /api/runs/` uses an inline `manifest` when present, otherwise resolves `config.testSuite`, otherwise falls back to the smoke check. `deselectStepIds` is accepted for inline or suite-resolved manifests and rejected for smoke checks. Suite resolution and invalid deselection failures use the existing HTTP 400 convention; the single-active-run guard still returns 409 for run conflicts.

The browser plan builder at `/plan/` supports two authoring modes. Paste config plus manifest JSON to preview an explicit manifest, or leave the manifest textarea blank and provide a config with `testSuite` to preview the resolved bundled suite. The preview shows the suite label/version metadata for config-resolved plans and launches through the same shared run lifecycle as the REST API. Browser posts remain Django-form mediated and CSRF-protected.

Suite-resolved runs add safe suite metadata to participant-visible result JSON and the `run-started` execution-log payload: `standard`, `specVersion`, `profile`, `suite`, `catalogId`, and `manifestResource`. Smoke checks and explicit-manifest runs keep their existing shape unless suite metadata is known.

This feature creates the catalog and config-selection rail only. The bundled `discovery-jwks` entries are not full Open Banking Read/Write v3.1.11 or v4.0 certification suites; full Read/Write coverage should be added later as manifest-authoring work once the target Ozone v4 scope is confirmed.

## Structured Execution Log

Every CLI and REST API run produces a structured **execution log** in [NDJSON](https://github.com/ndjson/ndjson-spec) format alongside the result file. One JSON object per line, streamable, tail-friendly, and partial-read safe — a truncated file is still parseable up to the last complete line.

**Where it goes:**

- CLI: `executionLogPath` in the model-bank config (default `out/execution-log.ndjson`, anchored at the same output base as `resultOutputPath`). Atomic write via `NamedTemporaryFile` + `os.replace` — a crash mid-flush leaves the prior log (or nothing) on disk, never a half-written file. Exit code `3` on write failure, same as the result file.
- REST API: `GET /api/runs/<id>/log/` returns `application/x-ndjson`. Loopback-guarded like every other API endpoint. Safe to poll on an in-flight run — the response is a snapshot of the buffer at request time.

**Event taxonomy** (`type` field, closed set):

| Type | Emitted when |
| --- | --- |
| `run-started` | First event of every run. |
| `run-completed` | Last event of a normal run; `payload.summary` carries the aggregate counts. Not emitted when the engine crashes — see `application-error`. |
| `step-started` / `step-completed` | Per-step bracket. |
| `request-sent` | Before each outbound HTTP request, with the masked request evidence. |
| `response-received` | After each HTTP response (status code + URL only; bodies are captured in the result file, not duplicated in the log). |
| `assertion-evaluated` | One event per declared assertion (re-read from `details.assertions`; never re-evaluated). |
| `placeholder-error` | Manifest placeholder could not be resolved (`payload.location` is `url` / `headers` / `body`, or `reason: "missing-predecessor-response"` for skipped steps). |
| `application-error` | Transport failure or other engine-side exception. Step-scoped occurrences (with a `step_id`) are non-terminal — the run continues and `run-completed` is still emitted. A top-level occurrence (no `step_id`) is terminal: the engine re-raises immediately afterwards and `run-completed` will not appear. |

**Masking:** every payload flows through `conformance.masking` before being buffered. Sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`, `X-FAPI-Financial-Id`, …) and credential-bearing JSON/form keys (`access_token`, `id_token`, `client_secret`, `code`, `client_assertion`, `password`, `private_key`, …) are replaced with the literal `"***"`. Match is case-insensitive; key casing is preserved verbatim. Replacement length is constant by design — original lengths are not preserved to avoid leaking entropy.

**Developer mode (`CONFORMANCE_DEVELOPER_MODE=true`):**

- Disables masking so the buffered events round-trip unchanged.
- Logs a prominent `WARN` line at startup: this is the **only** in-process protection — never set this in release builds, never set this on shared infrastructure, never set this when running against real Open Banking participant data.
- Intended for local engineering debugging of the engine itself, not for participants diagnosing their own implementations.

## Test Plans and Step Deselection

Every v1 manifest run executes against a **test plan** — an ordered, immutable list of plan entries that pairs each manifest `step.id` with a `selected: bool`. Plans are first-class engine values (`conformance.test_plan.TestPlan`); the runner never inspects raw manifests for selection. (v0 manifests have no plan support and always run every step.)

**Default plan.** Calling `TestPlan.default_plan_from_manifest(manifest)` returns a plan that selects every step the manifest declares as either `mandatory: true` *or* not `optional: true`. In other words, mandatory and "ordinary" steps are pre-selected; only steps explicitly tagged `"optional": true` are pre-deselected. This satisfies PRD Participant Story #4 (*"mandatory tests pre-populated by default, so I don't have to manually configure every run"*) and OBL Standards Story #3 (*"mandatory tests defined in configuration per spec version and standard, not hardcoded"*).

**Deselection.** `plan.with_deselection([step_ids])` returns a new immutable plan with the named ids flipped to `selected=False`. Unknown ids raise `ValueError` at call time — invalid deselections never reach the executor. Deselected steps **do not run** and **do not produce a `StepResult`**; "deselected" is not the same as the `skipped` outcome (which means a prerequisite failed). A single `step-deselected` execution-log event is emitted per deselected entry before the first `step-started`, so a log consumer can derive the plan-vs-manifest delta without scanning the full run.

**CLI:**

```sh
python -m conformance.cli config.json \
    --manifest manifest.json \
    --deselect step-id-a \
    --deselect step-id-b
```

`--deselect` is repeatable, requires either `--manifest` or a config-selected `testSuite`, and exits with code `2` for invalid combinations or an unknown id.

**REST API:**

```jsonc
POST /api/runs/
{
  "config":   { ... },
  "manifest": { "schemaVersion": "v1", ... },   // optional when config.testSuite selects a bundled suite
  "deselectStepIds": ["step-id-a", "step-id-b"]   // optional
}
```

`deselectStepIds` must be an array of strings, requires either an inline `manifest` or config-selected `testSuite`, and is rejected with HTTP 400 if any id is unknown.

**Browser plan builder UI:**

Run the local server and open `http://localhost:8000/plan/`:

```bash
make dev
```

The page accepts model-bank config JSON and optional v1 manifest JSON in text areas, validates them through the same Django form boundary used for preview and launch, and renders a selectable step table. Leave the manifest JSON blank to resolve the suite selected by `config.testSuite`; paste manifest JSON to override the catalog for authoring/testing. Mandatory and non-optional steps are selected by default; steps marked `"optional": true` start deselected. Deselecting a mandatory step remains possible, but the preview marks the certification impact and the resulting run is not eligible for certification.

Launching from the browser creates the same single active run as `POST /api/runs/` and redirects to `/runs/<run_id>/`, where the page shows status, timestamps, errors, result summaries, plan summaries, certification eligibility, and browser-accessible links to masked JSON/NDJSON outputs. The loopback-guarded REST API still exposes the same masked result and log for automation. The UI is intentionally scoped to v1 manifests because v0 manifests do not carry selectable plan semantics.

Manual `psu-authorization` steps can be previewed in the browser plan builder but cannot be launched from the UI yet. CLI and REST API runs still support manual PSU flows; the browser launch path is deferred until there is a one-time raw authorization URL handoff that does not persist the unmasked URL in result JSON or execution logs.

**Result file additions.** When a plan is supplied (CLI/API/UI explicit manifest mode and config-selected suite mode), the result JSON gains a top-level `plan` block:

```json
"plan": {
  "totalSteps": 5,
  "selectedSteps": 4,
  "deselectedSteps": 1,
  "mandatorySelected": 2,
  "mandatoryDeselected": 1
}
```

`certificationEligibility` gains two related fields: `mandatoryDeselected` (count) and `mandatoryDeselectedStepIds` (ordered list). Whenever `mandatoryDeselected > 0` the run is **not eligible** and the dedicated reason `"Mandatory steps were deselected from the plan"` takes precedence over every other failure reason — a mandatory step that never ran cannot demonstrate coverage, regardless of why.

## PSU Authorization Callback Coordination

The PRD's PSU Authorisation flow (manual mode, Phase 1) requires the engine to receive the ASPSP's browser redirect after the participant approves an authorization request. This is supported by three pieces of plumbing — a public callback endpoint, an in-memory auth-session store, and a pair of loopback-guarded register/poll endpoints. Token exchange of the captured `code` and executor-side wiring (a manifest step type that awaits an auth code) are explicit follow-up work.

**Endpoint contract:**

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/runs/<run_id>/auth-sessions/` | Loopback-guarded | Register an expected auth session; returns the opaque `state` to embed in the authorization URL. |
| `GET`  | `/api/runs/<run_id>/auth-sessions/<state>/` | Loopback-guarded | Poll the session; returns `status` and (when `captured`) the authorization `code`. |
| `GET`  | `/callback/` | **Public** (no loopback guard) | Receives the ASPSP redirect (`?state=...&code=...` or `?state=...&error=...`). |

**State generation.** Prefer the server-generated value — `POST /api/runs/<id>/auth-sessions/` with an empty body returns a URL-safe token generated from 32 bytes of entropy via `secrets.token_urlsafe(32)`. Callers MAY supply their own `state` (`{"state": "..."}`) when they need to thread an externally-generated correlator through the flow, but the value must be at least 32 characters long; shorter values are rejected with HTTP 400. When supplying `state`, callers MUST ensure it is cryptographically unguessable (length alone is not an entropy guarantee) because the public `/callback/` endpoint relies on `state` unpredictability for security.

**One-shot semantics.** A session transitions exactly once from `awaiting` to a terminal state (`captured` or `error`). A second hit on `/callback/` with the same `state` is rejected (returning the generic 400 failure page) and does not overwrite the captured value. There is no persistent nonce table — replay protection comes from the in-process one-shot rule and the per-run cap of 8 simultaneous sessions.

**Why `/callback/` is not loopback-guarded.** A browser redirect from the ASPSP must be able to reach the endpoint. Even when the FCS runs locally the navigation arrives via the host's network stack, and coupling the endpoint to `REMOTE_ADDR` would break any future reverse-proxy or portal deployment without adding real security. The security model relies on three independent properties: `state` unguessability (server-generated states use `secrets.token_urlsafe(32)`; caller-supplied states must be at least 32 characters long), one-shot consumption, and run-scoped binding (the loopback-guarded read API requires the parent `run_id` to retrieve the captured `code`). The callback never echoes the `state` value or any ASPSP-supplied free text into the response body.

**Execution-log events.** Two new event types join the taxonomy:

- `auth-session-registered` — emitted by `POST /api/runs/<id>/auth-sessions/` after successful registration. Payload: `{"state": "...", "status": "awaiting"}`. The `state` value is non-sensitive (it leaves the process inside the authorization URL anyway) and the event proves the FCS expected this state.
- `auth-callback-received` — emitted by `/callback/` after successful capture. Payload: `{"state": "...", "code": "***"}` for the success path, `{"state": "...", "error": "...", "error_description": "..."}` for the ASPSP-reported-error path (`error_description` is omitted when the ASPSP did not supply one). Payload keys use snake_case to match the rest of the execution-log event taxonomy (e.g. `request-sent` uses `method`/`url`). The raw `code` is added to the masking allow-list in `conformance.masking` so it is masked by default and is not persisted to the NDJSON log in clear text unless `CONFORMANCE_DEVELOPER_MODE=true` disables masking for local debugging. Unknown-state hits do not produce a log event — there is no run context to attach to, and emitting one would partially defeat the "does not disclose which states exist" property.

**Lifecycle.** Auth sessions are dropped when their parent run reaches a terminal state (`completed` or `failed`). Awaiting sessions for a finished run are not retained.

See [`FCS Rebuild - PRD v3 [DRAFT].md`](FCS%20Rebuild%20-%20PRD%20v3%20%5BDRAFT%5D.md) for the broader PSU authorization context and the Phase 1 vs. Phase 2 split.
