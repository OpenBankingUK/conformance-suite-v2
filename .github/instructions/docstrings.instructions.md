---
applyTo: "**/*.py"
---

# Docstring Completeness Rule

Every Python function and method in this repository — public **and** private (`_`-prefixed) — must have a **full Google-style docstring** when:

- It has **parameters** → include an `Args:` section documenting each parameter.
- It has a **non-`None` return type** → include a `Returns:` section.
- It **raises exceptions directly** → include a `Raises:` section.

A one-line summary alone is **not sufficient** for functions with parameters or return values.

## Example

```python
def _required_string(raw_config: dict[str, JsonValue], key: str, *, location: str) -> str:
    """Extract a required non-empty string from a JSON object.

    Args:
        raw_config: The parent JSON object to extract from.
        key: The key to look up in the object.
        location: Dot-path location string used in error messages.

    Returns:
        The stripped, non-empty string value.

    Raises:
        ManifestError: If the value is missing or not a non-empty string.
    """
```

## Enforcement

This rule is mechanically enforced in CI by `pydoclint` (configured in `pyproject.toml`). The tool checks that every parameter in the signature appears in `Args:`, and that functions with non-`None` returns have a `Returns:` section.

`interrogate` enforces docstring **presence** (100% threshold). `pydoclint` enforces docstring **structure**.

Both run as part of `make lint`.
