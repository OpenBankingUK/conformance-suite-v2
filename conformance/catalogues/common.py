"""Shared catalogue metadata for Open Banking Read/Write request handling."""

from __future__ import annotations

from dataclasses import replace

from conformance.catalogue import (
    CatalogueRequestHeader,
    CatalogueRequestStep,
    CatalogueTestCase,
)

OPEN_BANKING_GENERATED_REQUEST_HEADERS: tuple[CatalogueRequestHeader, ...] = (
    CatalogueRequestHeader(name="x-fapi-interaction-id", generated_value="uuid4"),
)
"""Outbound Open Banking Read/Write headers generated for every API request."""

IDEMPOTENCY_KEY_HEADER = CatalogueRequestHeader(name="x-idempotency-key", generated_value="uuid4")
"""Generated idempotency-key header mapping for Open Banking write operations."""


def open_banking_request_headers_for(*, require_idempotency: bool = False) -> tuple[CatalogueRequestHeader, ...]:
    """Return generated outbound Open Banking headers for a request.

    Args:
        require_idempotency: Whether to generate ``x-idempotency-key`` for this
            request.

    Returns:
        Generated Open Banking headers, plus idempotency for write operations.
    """
    headers = list(OPEN_BANKING_GENERATED_REQUEST_HEADERS)
    if require_idempotency:
        headers.append(IDEMPOTENCY_KEY_HEADER)
    return tuple(headers)


def with_open_banking_request_metadata(test_case: CatalogueTestCase) -> CatalogueTestCase:
    """Return a catalogue case decorated with shared request-header metadata.

    Args:
        test_case: Catalogue case whose request steps should accept
            specification-level Open Banking request headers.

    Returns:
        A copy of ``test_case`` with generated outbound header mappings attached
        to each request step.
    """
    return replace(
        test_case,
        request_steps=tuple(
            _with_request_headers(
                step,
                open_banking_request_headers_for(require_idempotency=step.method in {"POST", "PUT", "PATCH"}),
            )
            for step in test_case.request_steps
        ),
    )


def _with_request_headers(
    request_step: CatalogueRequestStep,
    request_headers: tuple[CatalogueRequestHeader, ...],
) -> CatalogueRequestStep:
    """Return a request step with additional request-header mappings.

    Args:
        request_step: Existing catalogue request step.
        request_headers: Header mappings to append when absent.

    Returns:
        Copy of ``request_step`` with de-duplicated header mappings.
    """
    seen_header_names = {header.name.lower() for header in request_step.headers}
    merged_headers = (
        *request_step.headers,
        *(header for header in request_headers if header.name.lower() not in seen_header_names),
    )
    return replace(request_step, headers=merged_headers)
