"""Execution context for manifest v1 sequential step carry-forward."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from conformance.json_types import JsonObject, JsonValue


class PlaceholderResolutionError(ValueError):
    """Raised when a ``${...}`` placeholder cannot be resolved from the context."""


@dataclass(frozen=True)
class StepRecord:
    """Captured request/response pair for one executed manifest step.

    Attributes:
        request: The HTTP request as issued (method, url).
        response: The HTTP response captured (status_code, body),
            or ``None`` if no response was received.
    """

    request: RequestRecord
    response: ResponseRecord | None = None


@dataclass(frozen=True)
class RequestRecord:
    """Captured HTTP request details stored in execution context.

    Attributes:
        method: HTTP method used.
        url: Resolved URL that was fetched.
    """

    method: str
    url: str


@dataclass(frozen=True)
class ResponseRecord:
    """Captured HTTP response details stored in execution context.

    Attributes:
        status_code: HTTP status code returned.
        body: Parsed JSON object response body (immutable proxy).
    """

    status_code: int
    body: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))

    def __init__(self, *, status_code: int, body: JsonObject) -> None:
        """Wrap the body dict in a read-only proxy to enforce immutability.

        Args:
            status_code: HTTP status code returned.
            body: Parsed JSON object response body.
        """
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "body", MappingProxyType(body))


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable execution context accumulating step records.

    Provides carry-forward data so later manifest steps can resolve
    ``${steps.<id>...}`` placeholders against earlier responses.

    Attributes:
        steps: Immutable mapping from step id to captured request/response record.
    """

    steps: Mapping[str, StepRecord] = field(default_factory=lambda: MappingProxyType({}))


_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")
"""Regex matching ``${...}`` tokens for resolution."""


def record_step(
    context: ExecutionContext,
    step_id: str,
    request: RequestRecord,
    response: ResponseRecord | None,
) -> ExecutionContext:
    """Return a new context with the given step recorded.

    Args:
        context: Current execution context (unchanged).
        step_id: Identifier for the step being recorded.
        request: Captured request details.
        response: Captured response details, or ``None`` on transport failure.

    Returns:
        A new execution context containing all previous steps plus the new one.
    """
    new_steps = dict(context.steps)
    new_steps[step_id] = StepRecord(request=request, response=response)
    return ExecutionContext(steps=MappingProxyType(new_steps))


def resolve_placeholders(template: str, context: ExecutionContext) -> str:
    """Replace all ``${...}`` placeholders in a template string.

    Supported dot-path grammar:
    ``steps.<id>.request.(method|url)``
    ``steps.<id>.response.(status_code|body.<dot.path>)``

    Args:
        template: String potentially containing ``${...}`` placeholders.
        context: Execution context providing step records for resolution.

    Returns:
        The template with all placeholders replaced by resolved values.

    Raises:
        PlaceholderResolutionError: If any placeholder cannot be resolved
            (missing step id, missing path segment, or non-primitive value).
    """
    if "${" not in template:
        return template

    def _replace(match: re.Match[str]) -> str:
        """Resolve a single placeholder match.

        Args:
            match: Regex match containing the dot-path expression.

        Returns:
            The resolved string value.

        Raises:
            PlaceholderResolutionError: If resolution fails.
        """
        dot_path = match.group(1)
        return _resolve_dot_path(dot_path, context)

    return _PLACEHOLDER_PATTERN.sub(_replace, template)


def _resolve_dot_path(dot_path: str, context: ExecutionContext) -> str:
    """Resolve a single dot-path expression against the execution context.

    Args:
        dot_path: The expression inside ``${...}`` (e.g.
            ``steps.openid-discovery.response.body.jwks_uri``).
        context: Execution context to resolve against.

    Returns:
        The resolved primitive value coerced to a string.

    Raises:
        PlaceholderResolutionError: If the path is invalid, the step is
            missing, or the resolved value is not a primitive.
    """
    segments = dot_path.split(".")
    if len(segments) < 4 or segments[0] != "steps":
        raise PlaceholderResolutionError(f"Invalid placeholder path: ${{{dot_path}}}")

    step_id = segments[1]
    direction = segments[2]  # "request" or "response"
    field_name = segments[3]

    if step_id not in context.steps:
        raise PlaceholderResolutionError(f"Step '{step_id}' not found in execution context")

    record = context.steps[step_id]

    if direction == "request":
        return _resolve_request_path(record.request, field_name, segments[4:], dot_path)
    if direction == "response":
        if record.response is None:
            raise PlaceholderResolutionError(f"Step '{step_id}' has no response (request may have failed)")
        return _resolve_response_path(record.response, field_name, segments[4:], dot_path)

    raise PlaceholderResolutionError(f"Invalid placeholder path segment '{direction}': ${{{dot_path}}}")


def _resolve_request_path(request: RequestRecord, field_name: str, remaining: list[str], dot_path: str) -> str:
    """Resolve a dot-path against a captured request record.

    Args:
        request: The captured HTTP request record.
        field_name: First field segment after ``request.`` (e.g. ``method``).
        remaining: Any remaining dot-path segments after ``field_name``.
        dot_path: Full original dot-path for error messages.

    Returns:
        The resolved string value.

    Raises:
        PlaceholderResolutionError: If the field is unrecognised or has
            unexpected sub-segments.
    """
    if field_name == "method" and not remaining:
        return request.method
    if field_name == "url" and not remaining:
        return request.url
    raise PlaceholderResolutionError(f"Cannot resolve request path: ${{{dot_path}}}")


def _resolve_response_path(response: ResponseRecord, field_name: str, remaining: list[str], dot_path: str) -> str:
    """Resolve a dot-path against a captured response record.

    Args:
        response: The captured HTTP response record.
        field_name: First field segment after ``response.`` (e.g. ``body``).
        remaining: Any remaining dot-path segments after ``field_name``.
        dot_path: Full original dot-path for error messages.

    Returns:
        The resolved primitive value coerced to a string.

    Raises:
        PlaceholderResolutionError: If the field is unrecognised, a sub-path
            segment is missing, or the resolved value is non-primitive.
    """
    if field_name == "status_code" and not remaining:
        return str(response.status_code)
    if field_name == "body":
        return _resolve_body_path(response.body, remaining, dot_path)
    raise PlaceholderResolutionError(f"Cannot resolve response path: ${{{dot_path}}}")


def _resolve_body_path(body: Mapping[str, JsonValue], segments: list[str], dot_path: str) -> str:
    """Walk a JSON body using dot-path segments to extract a primitive value.

    Args:
        body: Parsed JSON object response body to traverse.
        segments: Remaining dot-path segments to navigate.
        dot_path: Full original dot-path for error messages.

    Returns:
        The resolved primitive value coerced to a string.

    Raises:
        PlaceholderResolutionError: If a segment is missing, the traversal
            encounters a non-object intermediate, or the leaf is non-primitive.
    """
    current: JsonValue | Mapping[str, JsonValue] = body
    for segment in segments:
        if not isinstance(current, Mapping):
            raise PlaceholderResolutionError(f"Cannot traverse non-object at '{segment}': ${{{dot_path}}}")
        if segment not in current:
            raise PlaceholderResolutionError(f"Path segment '{segment}' not found: ${{{dot_path}}}")
        current = current[segment]

    # If no segments, we'd be resolving the entire body (non-primitive)
    if isinstance(current, (Mapping, list)):
        kind = "object" if isinstance(current, Mapping) else "array"
        raise PlaceholderResolutionError(f"Resolved value is not a primitive (got {kind}): ${{{dot_path}}}")

    if current is None:
        return "null"
    return str(current)
