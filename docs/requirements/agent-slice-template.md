# Agent implementation slice template

Use this template when turning an item from `requirements.yml` or a suite coverage ledger into implementation work for an AI agent.

## Requirement

- Requirement ID:
- Parent epic:
- Current status:
- Target status:
- Source artefacts:

## Outcome

One sentence describing the observable behaviour that should exist when the slice is complete.

## In scope

- 

## Out of scope

- 

## Affected surfaces

- Python modules:
- Suite manifests:
- Config examples:
- API/UI surfaces:
- Result/log/certification shape:
- Docs/changelog:

## Acceptance criteria

1. 
2. 
3. 

## Security and compliance notes

- Masking impact:
- Path/placeholder trust boundary impact:
- OAuth/FAPI/JWS/TLS impact:
- Certification integrity impact:

## Validation commands

```bash
# Focused tests while iterating
PYTEST_ARGS='tests/<target>.py::test_name' make test

# Local gate before handoff
make check
```

## Definition of Ready

- Requirement ID exists in `requirements.yml` or a suite coverage ledger.
- Scope is small enough for one PR.
- Acceptance criteria are observable.
- Test strategy is named.
- Security and docs impacts are known.
- Open decisions are either resolved or explicitly out of scope.

## Definition of Done

- Behaviour is implemented through existing public surfaces.
- Tests cover the new behaviour and relevant failure paths.
- Masking and certification eligibility semantics are preserved.
- Docs and changelog are updated when behaviour changes.
- `make check` or an agreed focused validation gate passes.
- Coverage ledger or requirement status is updated when applicable.

