"""Reusable JSON type aliases for parsed conformance data."""

type JsonScalar = str | int | float | bool | None
"""Scalar values allowed by JSON."""

type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
"""Recursive JSON value accepted from config files and HTTP responses."""

type JsonObject = dict[str, JsonValue]
"""JSON object with string keys and recursively typed JSON values."""
