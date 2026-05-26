import pytest

from conformance.context import (
    ExecutionContext,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    StepRecord,
    record_step,
    resolve_placeholders,
)


def _discovery_context() -> ExecutionContext:
    """Build a context with a typical OpenID discovery step recorded."""
    return ExecutionContext(
        steps={
            "openid-discovery": StepRecord(
                request=RequestRecord(
                    method="GET",
                    url="https://auth.example.com/.well-known/openid-configuration",
                ),
                response=ResponseRecord(
                    status_code=200,
                    body={
                        "issuer": "https://auth.example.com",
                        "jwks_uri": "https://auth.example.com/jwks",
                        "token_endpoint": "https://auth.example.com/token",
                        "nested": {"deep": {"value": "found"}},
                    },
                ),
            ),
        }
    )


@pytest.mark.unit
class TestRecordStep:
    def test_records_step_into_new_context(self) -> None:
        ctx = ExecutionContext()
        request = RequestRecord(method="GET", url="https://example.com/api")
        response = ResponseRecord(status_code=200, body={"key": "value"})

        new_ctx = record_step(ctx, "step-1", request, response)

        assert "step-1" in new_ctx.steps
        assert new_ctx.steps["step-1"].request.url == "https://example.com/api"
        assert new_ctx.steps["step-1"].response is not None
        assert new_ctx.steps["step-1"].response.body == {"key": "value"}
        # Original context unchanged
        assert "step-1" not in ctx.steps

    def test_records_step_without_response(self) -> None:
        ctx = ExecutionContext()
        request = RequestRecord(method="GET", url="https://example.com/api")

        new_ctx = record_step(ctx, "step-1", request, None)

        assert new_ctx.steps["step-1"].response is None

    def test_preserves_existing_steps(self) -> None:
        ctx = ExecutionContext(
            steps={
                "existing": StepRecord(
                    request=RequestRecord(method="GET", url="https://example.com/a"),
                    response=ResponseRecord(status_code=200, body={}),
                )
            }
        )
        request = RequestRecord(method="GET", url="https://example.com/b")
        response = ResponseRecord(status_code=201, body={"new": True})

        new_ctx = record_step(ctx, "new-step", request, response)

        assert "existing" in new_ctx.steps
        assert "new-step" in new_ctx.steps

    def test_steps_isolated_from_caller_mutation(self) -> None:
        mutable_dict: dict[str, StepRecord] = {
            "step-a": StepRecord(
                request=RequestRecord(method="GET", url="https://example.com/a"),
                response=ResponseRecord(status_code=200, body={}),
            )
        }
        ctx = ExecutionContext(steps=mutable_dict)

        # Mutate the original dict after construction
        mutable_dict["injected"] = StepRecord(
            request=RequestRecord(method="GET", url="https://evil.com"),
            response=None,
        )

        # Context must be unaffected
        assert "injected" not in ctx.steps
        assert list(ctx.steps.keys()) == ["step-a"]

    def test_steps_rejects_in_place_mutation(self) -> None:
        ctx = ExecutionContext(
            steps={
                "step-a": StepRecord(
                    request=RequestRecord(method="GET", url="https://example.com/a"),
                    response=ResponseRecord(status_code=200, body={}),
                )
            }
        )

        with pytest.raises(TypeError):
            ctx.steps["new"] = StepRecord(  # type: ignore[index]
                request=RequestRecord(method="GET", url="https://evil.com"),
                response=None,
            )


@pytest.mark.unit
class TestResolvePlaceholdersHappyPaths:
    def test_resolves_response_body_field(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("${steps.openid-discovery.response.body.jwks_uri}", ctx)
        assert result == "https://auth.example.com/jwks"

    def test_resolves_nested_body_path(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("${steps.openid-discovery.response.body.nested.deep.value}", ctx)
        assert result == "found"

    def test_resolves_response_status_code(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("${steps.openid-discovery.response.status_code}", ctx)
        assert result == "200"

    def test_resolves_request_url(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("${steps.openid-discovery.request.url}", ctx)
        assert result == "https://auth.example.com/.well-known/openid-configuration"

    def test_resolves_request_method(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("${steps.openid-discovery.request.method}", ctx)
        assert result == "GET"

    def test_returns_template_unchanged_when_no_placeholders(self) -> None:
        ctx = _discovery_context()
        result = resolve_placeholders("https://example.com/plain", ctx)
        assert result == "https://example.com/plain"

    def test_resolves_multiple_placeholders_in_one_template(self) -> None:
        ctx = _discovery_context()
        template = "${steps.openid-discovery.response.body.issuer}/custom?from=${steps.openid-discovery.request.method}"
        result = resolve_placeholders(template, ctx)
        assert result == "https://auth.example.com/custom?from=GET"

    def test_resolves_boolean_body_value(self) -> None:
        ctx = ExecutionContext(
            steps={
                "s1": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=ResponseRecord(status_code=200, body={"flag": True}),
                )
            }
        )
        result = resolve_placeholders("${steps.s1.response.body.flag}", ctx)
        assert result == "True"

    def test_resolves_numeric_body_value(self) -> None:
        ctx = ExecutionContext(
            steps={
                "s1": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=ResponseRecord(status_code=200, body={"count": 42}),
                )
            }
        )
        result = resolve_placeholders("${steps.s1.response.body.count}", ctx)
        assert result == "42"

    def test_resolves_null_body_value(self) -> None:
        ctx = ExecutionContext(
            steps={
                "s1": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=ResponseRecord(status_code=200, body={"empty": None}),
                )
            }
        )
        result = resolve_placeholders("${steps.s1.response.body.empty}", ctx)
        assert result == "null"


@pytest.mark.unit
class TestResolvePlaceholdersErrors:
    def test_missing_step_id(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Step 'unknown' not found"):
            resolve_placeholders("${steps.unknown.response.body.x}", ctx)

    def test_missing_body_path_segment(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Path segment 'nonexistent' not found"):
            resolve_placeholders("${steps.openid-discovery.response.body.nonexistent}", ctx)

    def test_non_primitive_resolution_object(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="not a primitive.*object"):
            resolve_placeholders("${steps.openid-discovery.response.body.nested}", ctx)

    def test_non_primitive_resolution_array(self) -> None:
        ctx = ExecutionContext(
            steps={
                "s1": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=ResponseRecord(status_code=200, body={"items": [1, 2, 3]}),
                )
            }
        )
        with pytest.raises(PlaceholderResolutionError, match="not a primitive.*array"):
            resolve_placeholders("${steps.s1.response.body.items}", ctx)

    def test_traverse_non_object_intermediate(self) -> None:
        ctx = ExecutionContext(
            steps={
                "s1": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=ResponseRecord(status_code=200, body={"leaf": "text"}),
                )
            }
        )
        with pytest.raises(PlaceholderResolutionError, match="Cannot traverse non-object"):
            resolve_placeholders("${steps.s1.response.body.leaf.deeper}", ctx)

    def test_invalid_path_too_short(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Invalid placeholder path"):
            resolve_placeholders("${steps.openid-discovery}", ctx)

    def test_invalid_direction_segment(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Invalid placeholder path segment 'other'"):
            resolve_placeholders("${steps.openid-discovery.other.body.x}", ctx)

    def test_step_with_no_response(self) -> None:
        ctx = ExecutionContext(
            steps={
                "failed": StepRecord(
                    request=RequestRecord(method="GET", url="https://x.com"),
                    response=None,
                )
            }
        )
        with pytest.raises(PlaceholderResolutionError, match="has no response"):
            resolve_placeholders("${steps.failed.response.body.x}", ctx)

    def test_invalid_request_field(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Cannot resolve request path"):
            resolve_placeholders("${steps.openid-discovery.request.body.x}", ctx)


@pytest.mark.unit
class TestResolvePlaceholdersMalformedSyntax:
    """Malformed ``${...}`` token syntax must be detected before resolution."""

    def test_empty_placeholder_raises(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Empty placeholder"):
            resolve_placeholders("${}", ctx)

    def test_empty_placeholder_mixed_with_valid_raises(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Empty placeholder"):
            resolve_placeholders("${steps.openid-discovery.request.method}${}", ctx)

    def test_unterminated_placeholder_raises(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Unterminated placeholder"):
            resolve_placeholders("https://example.com/${steps.openid-discovery.request.url", ctx)

    def test_unterminated_placeholder_at_start_raises(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Unterminated placeholder"):
            resolve_placeholders("${steps.openid-discovery.request.method", ctx)
