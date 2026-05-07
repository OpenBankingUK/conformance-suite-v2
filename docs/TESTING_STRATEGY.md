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

# E2E only (requires model bank config)
uv run pytest -p no:django -m e2e --model-bank-url=https://model-bank.example.com
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

Coverage is enforced in CI via `--cov-fail-under=80`. Reports are uploaded as CI artifacts.
