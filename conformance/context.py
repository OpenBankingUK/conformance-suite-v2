"""Execution context for manifest v1 sequential step carry-forward."""

from __future__ import annotations

import copy
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
        url: URL recorded at the time the step was captured. This is the
            resolved URL when placeholder resolution succeeded, or the
            original unresolved template when resolution failed.
    """

    method: str
    url: str


@dataclass(frozen=True)
class ResponseRecord:
    """Captured HTTP response details stored in execution context.

    Attributes:
        status_code: HTTP status code returned.
        body: Deep copy of the parsed JSON object response body, wrapped in a
            shallow read-only proxy. Top-level keys cannot be added or removed;
            nested containers inside the body are not frozen.
    """

    status_code: int
    body: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))

    def __init__(self, *, status_code: int, body: JsonObject) -> None:
        """Deep-copy and wrap the body dict to isolate it from external mutation.

        A deep copy is taken so that mutations to the original ``body`` argument
        after construction do not affect the stored record.  The copy is then
        wrapped in a ``MappingProxyType`` to prevent top-level key mutations.
        Nested containers remain mutable if a caller obtains a direct reference.

        Args:
            status_code: HTTP status code returned.
            body: Parsed JSON object response body.
        """
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "body", MappingProxyType(copy.deepcopy(body)))


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable execution context accumulating step records.

    Provides carry-forward data so later manifest steps can resolve
    ``${steps.<id>...}`` placeholders against earlier responses.

    Attributes:
        steps: Immutable mapping from step id to captured request/response record.
    """

    steps: Mapping[str, StepRecord] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Wrap ``steps`` in a read-only proxy to enforce immutability.

        Copies the incoming mapping to break aliasing with the caller's
        original dict, then wraps in ``MappingProxyType`` so top-level
        mutations are rejected at runtime.
        """
        object.__setattr__(self, "steps", MappingProxyType(dict(self.steps)))


_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")
"""Regex matching ``${...}`` tokens for resolution."""

_MALFORMED_PLACEHOLDER_PATTERN = re.compile(r"\$\{[^}]*$", re.MULTILINE)
"""Regex detecting an unterminated ``${`` that has no closing ``}``."""


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
    return ExecutionContext(steps=new_steps)


_TRUNCATION_CONTEXT_CHARS = 20
"""Maximum characters to show either side of a malformed placeholder token."""


def _truncate_around_malformed(template: str) -> str:
    """Return a short context window around the first unterminated ``${`` token.

    Avoids exposing the full template — which may contain sensitive URL
    query parameters — in error messages that propagate to result files.

    Args:
        template: Template containing at least one unterminated ``${``.

    Returns:
        A string showing up to :data:`_TRUNCATION_CONTEXT_CHARS` characters
        before the ``${`` and the remainder of the template after it (also
        capped), with ellipsis markers when truncated.
    """
    idx = template.rfind("${")
    if idx == -1:
        return "..."
    start = max(0, idx - _TRUNCATION_CONTEXT_CHARS)
    end = min(len(template), idx + _TRUNCATION_CONTEXT_CHARS)
    snippet = template[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(template) else ""
    return f"{prefix}{snippet}{suffix}"


def _validate_placeholder_syntax(template: str) -> None:
    """Raise if the template contains malformed ``${...}`` placeholder syntax.

    Checks for two classes of malformed token that ``_PLACEHOLDER_PATTERN``
    silently skips:

    * **Empty placeholder** — ``${}`` contains no path expression and would
      never be resolvable.
    * **Unterminated placeholder** — ``${...`` has no closing ``}`` and would
      be silently passed through, leaking the raw token into URL validation
      or HTTP execution with confusing downstream errors.

    This validation is intentionally called only when the template contains
    ``${``, so it runs after the fast-path early exit in
    :func:`resolve_placeholders`.

    Args:
        template: Template string that has already been confirmed to contain
            at least one ``${`` occurrence.

    Raises:
        PlaceholderResolutionError: If an empty or unterminated placeholder
            token is detected.
    """
    if "${}" in template:
        raise PlaceholderResolutionError("Empty placeholder '${}' is not valid — provide a dot-path expression")
    if _MALFORMED_PLACEHOLDER_PATTERN.search(template):
        context_window = _truncate_around_malformed(template)
        msg = f"Unterminated placeholder in template (missing closing '}}'): {context_window}"
        raise PlaceholderResolutionError(msg)


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
        PlaceholderResolutionError: If any placeholder token is malformed
            (empty or unterminated) or cannot be resolved (missing step id,
            missing path segment, or non-primitive value).
    """
    if "${" not in template:
        return template

    _validate_placeholder_syntax(template)

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


def resolve_in_structure(value: JsonValue, context: ExecutionContext) -> JsonValue:
    """Recursively resolve ``${...}`` placeholders in all string leaves of a JSON structure.

    Walks dicts and lists depth-first and applies :func:`resolve_placeholders`
    to every string leaf. Non-string leaves (numbers, booleans, null) are
    returned unchanged.

    Args:
        value: JSON value (possibly nested) containing placeholder strings.
        context: Execution context providing step records for resolution.

    Returns:
        A new JSON structure with all string-leaf placeholders resolved.

    Raises:
        PlaceholderResolutionError: If any string leaf contains an unresolvable
            or malformed placeholder.
    """
    if isinstance(value, str):
        return resolve_placeholders(value, context)
    if isinstance(value, dict):
        return {key: resolve_in_structure(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_in_structure(child, context) for child in value]
    # Scalar: int, float, bool, None — pass through unchanged
    return value
