"""Execution context for manifest v1 sequential step carry-forward."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from conformance.headers import FrozenHeaders, freeze_headers
from conformance.json_types import JsonObject, JsonValue


class PlaceholderResolutionError(ValueError):
    """Raised when a ``${...}`` placeholder cannot be resolved from the context."""


class MissingPredecessorResponseError(PlaceholderResolutionError):
    """Raised when a placeholder references a step whose request did not produce a response.

    This narrower subclass lets the executor distinguish a *true* resolution
    failure (malformed path, missing field, non-primitive value) from the
    "prerequisite step failed before producing a response" case. The latter
    should surface as a ``SKIPPED`` step result per the PRD, not as a
    ``FAILED`` step.
    """


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
        url: URL recorded at the time the step was captured. This is usually
            the resolved URL when placeholder resolution succeeded, or the
            original unresolved template when resolution failed. Step executors
            may store a masked URL when the value is available to later
            placeholders and could otherwise expose credentials.
    """

    method: str
    url: str


@dataclass(frozen=True)
class ResponseRecord:
    """Captured HTTP response details stored in execution context.

    Attributes:
        status_code: HTTP status code returned.
        headers: Immutable response headers with case-insensitive lookup.
        body: Deep copy of the parsed JSON object response body, wrapped in a
            shallow read-only proxy. Top-level keys cannot be added or removed;
            nested containers inside the body are not frozen.
    """

    status_code: int
    headers: FrozenHeaders = field(default_factory=FrozenHeaders)
    body: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))

    def __init__(self, *, status_code: int, body: JsonObject, headers: Mapping[str, str] | None = None) -> None:
        """Deep-copy response data to isolate the stored record from mutation.

        A deep copy is taken so that mutations to the original ``body`` argument
        after construction do not affect the stored record.  The copy is then
        wrapped in a ``MappingProxyType`` to prevent top-level key mutations.
        Response headers are also copied into an immutable case-insensitive
        mapping so later assertion and placeholder code can resolve names
        without depending on source header casing.

        Args:
            status_code: HTTP status code returned.
            body: Parsed JSON object response body.
            headers: Optional response headers to capture.
        """
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "headers", freeze_headers(headers))
        object.__setattr__(self, "body", MappingProxyType(copy.deepcopy(body)))


@dataclass(frozen=True)
class RuntimeConfig:
    """Safe participant config values exposed to manifest placeholders.

    Only deliberately non-secret, non-path values belong here. This keeps
    ``${config.*}`` placeholders narrow enough for bundled manifests without
    allowing arbitrary traversal of participant configuration such as TLS
    paths, private-key material, or future credential fields. Client secrets,
    private keys, and certificate material must never appear here.

    Attributes:
        discovery_url: OpenID discovery URL from validated participant config.
        environment: Human-readable environment label from validated
            participant config.
        oauth_resource_base_url: HTTPS protected-resource base URL for
            ``${config.oauth.resourceBaseUrl}`` placeholder resolution before
            manifest-owned Open Banking API paths. Absent when the participant
            config omits the optional ``oauth.resourceBaseUrl`` field.
        oauth_client_id: OAuth client identifier for
            ``${config.oauth.clientId}`` placeholder resolution. Absent when
            the participant config omits an ``oauth`` section.
        oauth_redirect_uri: HTTPS redirect URI registered with the
            authorisation server, for ``${config.oauth.redirectUri}``
            placeholder resolution. Absent when the participant config omits
            an ``oauth`` section.
    """

    discovery_url: str
    environment: str
    oauth_resource_base_url: str | None = None
    oauth_client_id: str | None = None
    oauth_redirect_uri: str | None = None


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable execution context accumulating step records.

    Provides carry-forward data so later manifest steps can resolve
    ``${steps.<id>...}`` placeholders against earlier responses and a small
    allow-list of ``${config.*}`` placeholders from safe runtime config.

    Attributes:
        steps: Immutable mapping from step id to captured request/response record.
        config: Optional runtime config values allowed in ``${config.*}``
            placeholders.
    """

    steps: Mapping[str, StepRecord] = field(default_factory=lambda: MappingProxyType({}))
    config: RuntimeConfig | None = None

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
    return ExecutionContext(steps=new_steps, config=context.config)


_TRUNCATION_CONTEXT_CHARS = 20
"""Maximum characters of trailing context to show after a malformed placeholder token."""


def _truncate_around_malformed(template: str) -> str:
    """Return a short context window starting at the last unterminated ``${`` token.

    Avoids exposing the template *prefix* — which may contain sensitive URL
    query parameters such as ``client_secret`` or bearer tokens — in error
    messages that propagate to result files. Only the ``${`` opener and a
    short trailing window are returned, which is enough to identify which
    placeholder is malformed without leaking anything preceding it.

    When the template contains multiple ``${`` openers, the trailing one is
    the unterminated token (any earlier well-formed ``${...}`` has already
    been matched by the resolver pattern), so ``rfind`` deliberately
    selects the rightmost — and also the least likely to be surrounded by
    secret-bearing prefix context.

    Args:
        template: Template containing at least one unterminated ``${``.

    Returns:
        A string starting with ``${`` followed by up to
        :data:`_TRUNCATION_CONTEXT_CHARS` characters from the template, with
        a trailing ellipsis marker when the suffix is truncated. Returns
        ``"..."`` if no ``${`` token is found.
    """
    idx = template.rfind("${")
    if idx == -1:
        return "..."
    end = min(len(template), idx + 2 + _TRUNCATION_CONTEXT_CHARS)
    snippet = template[idx:end]
    suffix = "..." if end < len(template) else ""
    return f"{snippet}{suffix}"


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
    ``config.(discoveryUrl|environment)``
    ``config.oauth.(clientId|redirectUri)``

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
            ``steps.openid-discovery.response.body.jwks_uri`` or
            ``config.discoveryUrl``).
        context: Execution context to resolve against.

    Returns:
        The resolved primitive value coerced to a string.

    Raises:
        PlaceholderResolutionError: If the path is invalid, the step is
            missing, or the resolved value is not a primitive.
    """
    segments = dot_path.split(".")
    if segments[0] == "config":
        return _resolve_config_path(segments, context, dot_path)
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
            raise MissingPredecessorResponseError(f"Step '{step_id}' has no response (request may have failed)")
        return _resolve_response_path(record.response, field_name, segments[4:], dot_path)

    raise PlaceholderResolutionError(f"Invalid placeholder path segment '{direction}': ${{{dot_path}}}")


_ALLOWED_CONFIG_PLACEHOLDERS = (
    "${config.discoveryUrl}",
    "${config.environment}",
    "${config.oauth.clientId}",
    "${config.oauth.redirectUri}",
    "${config.oauth.resourceBaseUrl}",
)
"""Exhaustive allow-list of ``${config.*}`` placeholder paths exposed to manifest steps."""


def _resolve_config_path(segments: list[str], context: ExecutionContext, dot_path: str) -> str:
    """Resolve an allow-listed runtime config placeholder.

    The allow-list is deliberately narrow: only non-secret, non-path values are
    permitted.  Client secrets, private keys, TLS paths, and certificate
    material must never be added here.

    Args:
        segments: Dot-path segments split from the placeholder expression.
        context: Execution context carrying optional runtime config values.
        dot_path: Full original dot-path for error messages.

    Returns:
        The resolved config value.

    Raises:
        PlaceholderResolutionError: If no runtime config was supplied, the
            requested config field is not on the allow-list, or the OAuth
            config sub-section is absent when an ``oauth.*`` field is requested.
    """
    _allowed_str = ", ".join(_ALLOWED_CONFIG_PLACEHOLDERS)
    is_simple_field = len(segments) == 2 and segments[1] in {"discoveryUrl", "environment"}
    is_oauth_field = (
        len(segments) == 3 and segments[1] == "oauth" and segments[2] in {"clientId", "redirectUri", "resourceBaseUrl"}
    )
    if not (is_simple_field or is_oauth_field):
        raise PlaceholderResolutionError(f"Unsupported config placeholder: ${{{dot_path}}} (allowed: {_allowed_str})")
    if context.config is None:
        raise PlaceholderResolutionError(f"Runtime config is not available for placeholder: ${{{dot_path}}}")

    if is_simple_field:
        if segments[1] == "discoveryUrl":
            return context.config.discovery_url
        return context.config.environment

    # is_oauth_field — segments[2] is "clientId", "redirectUri", or "resourceBaseUrl"
    sub_field = segments[2]
    if sub_field == "clientId":
        if context.config.oauth_client_id is None:
            raise PlaceholderResolutionError(f"OAuth config is not available for placeholder: ${{{dot_path}}}")
        return context.config.oauth_client_id
    if sub_field == "resourceBaseUrl":
        if context.config.oauth_resource_base_url is None:
            if context.config.oauth_client_id is not None or context.config.oauth_redirect_uri is not None:
                raise PlaceholderResolutionError(
                    f"oauth.resourceBaseUrl is not available for placeholder: ${{{dot_path}}}"
                )
            raise PlaceholderResolutionError(f"OAuth config is not available for placeholder: ${{{dot_path}}}")
        return context.config.oauth_resource_base_url
    # sub_field == "redirectUri"
    if context.config.oauth_redirect_uri is None:
        raise PlaceholderResolutionError(f"OAuth config is not available for placeholder: ${{{dot_path}}}")
    return context.config.oauth_redirect_uri


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


def _resolve_list_segment(items: list[JsonValue], segment: str, dot_path: str) -> JsonValue:
    """Resolve one dot-path segment against a JSON array.

    Args:
        items: JSON array currently being traversed.
        segment: Dot-path segment expected to be a non-negative integer index.
        dot_path: Full original dot-path for error messages.

    Returns:
        The JSON value stored at the indexed position.

    Raises:
        PlaceholderResolutionError: If the segment is not a non-negative
            integer or the index is outside the array bounds.
    """
    if not segment.isdigit():
        raise PlaceholderResolutionError(f"Cannot traverse array with non-numeric segment '{segment}': ${{{dot_path}}}")

    index = int(segment)
    if index >= len(items):
        raise PlaceholderResolutionError(f"Array index {index} out of bounds: ${{{dot_path}}}")

    return items[index]


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
        if isinstance(current, Mapping):
            if segment not in current:
                raise PlaceholderResolutionError(f"Path segment '{segment}' not found: ${{{dot_path}}}")
            current = current[segment]
            continue

        if isinstance(current, list):
            current = _resolve_list_segment(current, segment, dot_path)
            continue

        raise PlaceholderResolutionError(f"Cannot traverse non-object at '{segment}': ${{{dot_path}}}")

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
