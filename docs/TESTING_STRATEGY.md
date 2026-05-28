# Testing Strategy

## Table of Contents

- [Testing Strategy](#testing-strategy)
  - [Table of Contents](#table-of-contents)
  - [Framework Recommendation](#framework-recommendation)
    - [Recommendation: pytest ecosystem](#recommendation-pytest-ecosystem)
  - [Test Categories](#test-categories)
    - [1. Unit \& Application Tests](#1-unit--application-tests)
    - [2. Integration Tests](#2-integration-tests)
    - [3. End-to-End Tests (Model Bank Endpoint Testing)](#3-end-to-end-tests-model-bank-endpoint-testing)
    - [4. Result File Assertion Pattern](#4-result-file-assertion-pattern)
  - [Test Markers](#test-markers)
  - [Code Quality Tools](#code-quality-tools)
  - [Coverage Targets](#coverage-targets)

---

## Framework Recommendation

### Recommendation: pytest ecosystem

**pytest** with **pytest-django** is the recommended choice. It is the de facto standard for Django testing, surpasses Django's built-in `TestCase` runner in flexibility, and critically supports the CLI-runnable / result-file pattern required by this project.

---

## Test Categories

### 1. Unit & Application Tests

Test isolated business logic, Django views, models, and form/serialiser validation.

| Package | Role |
|---|---|
| `pytest` | Primary test runner |
| `pytest-django` | Django settings, DB fixtures, test client |
| `pytest-cov` | Coverage reporting (XML + terminal) |
| `pytest-xdist` | Parallel execution (`-n auto`) |
| `factory_boy` | Realistic, repeatable test data factories |
| `faker` | Fake data generation (via factory_boy) |

### 2. Integration Tests

Test HTTP request/response cycles against the running Django app, including HTMX interactions.

| Package | Role |
|---|---|
| `pytest-django` | `client` and `rf` fixtures, `live_server` |
| `httpx` | ASGI test transport for async-compatible requests |
| `respx` | Mock external HTTP calls deterministically |

**Live-network Ozone integration tests** live under `tests/integration/` and carry the `ozone` marker (distinct from the existing `integration` marker, which is reserved for offline tests that need the Django test client or DB). They are gated on tier-specific environment variables via `tests/_ozone.py::requires_ozone(tier)` — for example, tier 1 (OpenID discovery against the real Ozone host) requires `OZONE_DISCOVERY_URL`. Tests skip cleanly with a self-documenting reason when env vars are absent and never silently pass. `make test` (and therefore `make check`) excludes the `ozone` marker so default runs stay offline while still exercising the offline Django integration tests; `make integration` runs the live-network tier when env vars are set. In CI, the `Ozone Integration` workflow (`.github/workflows/ozone-integration.yml`) runs the tier on every PR (surfacing pytest output on the PR check page), nightly on a schedule, and on manual `workflow_dispatch`, sourcing `OZONE_DISCOVERY_URL` from the repository-level `vars.OZONE_DISCOVERY_URL` variable. The workflow is non-blocking for PR merges; fork PRs without variable access skip with a notice rather than fail. When tier 2 introduces mTLS material, the workflow will move to a scoped GitHub Environment to add audit/branch-scoping guarantees. See [`ai/plans/2026-05-28-ozone-integration-tiers.md`](../ai/plans/2026-05-28-ozone-integration-tiers.md) for the full tier definition.

### 3. End-to-End Tests (Model Bank Endpoint Testing)

The application is run as a Docker container with a config file pointing to a model bank. The container executes, writes a structured result file, and exits. The E2E test suite:

1. Builds and starts the Docker container via `testcontainers-python`
2. Passes a config pointing to the target model bank
3. Waits for container exit
4. Reads and asserts on the result file

E2E tests run on **all PRs to `develop` and `main`**, and on pushes to `release/**` branches. Running E2E on every feature and bugfix merge catches integration issues early, rather than surfacing them at release time.

| Package | Role |
|---|---|
| `testcontainers` | Programmatically start/stop Docker containers in tests |
| `pytest-asyncio` | Async test support if container orchestration is async |
| `pytest-json-report` | Structured JSON test output for CI artifact assertion |

### 4. Result File Assertion Pattern

The application produces a result file (e.g. `results.json`) on each run. The test pipeline can assert on it in two ways:

**In-process** (preferred for unit/integration):
```python
# tests/e2e/test_result_output.py
import json
import pytest
from pathlib import Path

@pytest.mark.e2e
def test_conformance_results(run_conformance_tool):
    result_path = run_conformance_tool(config="config/model-bank-test.yaml")
    report = json.loads(Path(result_path).read_text())

    assert report["summary"]["total"] > 0
    assert report["summary"]["failed"] == 0, f"Failures: {report['failures']}"
```

**In CI** (post-container run):
```bash
uv run pytest tests/e2e/ -p no:django -m e2e --json-report --json-report-file=e2e-results.json
```

---

## Test Markers

Define custom markers in `pyproject.toml` to separate test tiers:

```toml
[tool.pytest.ini_options]
markers = [
    "unit: fast, isolated unit tests",
    "integration: tests requiring the Django test client or DB",
    "e2e: full end-to-end tests requiring Docker and a model bank",
]
```

Run selectively:
```bash
# Unit only (fast, no DB)
uv run pytest -m unit

# All except E2E (CI default)
uv run pytest -m "not e2e"

# E2E only (requires model bank config — see .github/workflows/e2e.yml)
uv run pytest -p no:django -m e2e
```

---

## Code Quality Tools

| Tool | Role | Config |
|---|---|---|
| `ruff` | Linting + import sorting (replaces flake8, isort, pyupgrade) | `pyproject.toml` |
| `mypy` | Static type checking | `pyproject.toml` |
| `ruff format` | Formatting (replaces black) | `pyproject.toml` |

---

## Coverage Targets

| Context | Minimum Coverage |
|---|---|
| Unit + integration (`not e2e`) | 80% |
| Critical security paths | 100% (enforced via `# pragma: no cover` policy) |

Coverage is enforced via `fail_under = 80` in `[tool.coverage.report]` in `pyproject.toml`. Reports are uploaded as CI artifacts.
