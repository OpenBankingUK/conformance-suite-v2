import pytest

from conformance.context import (
    ExecutionContext,
    MissingPredecessorResponseError,
    PlaceholderResolutionError,
    RequestRecord,
    ResponseRecord,
    RuntimeConfig,
    StepRecord,
    record_step,
    resolve_placeholders,
)
from conformance.json_types import JsonValue


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


def _runtime_config_context() -> ExecutionContext:
    """Build a context carrying safe participant config values."""
    return ExecutionContext(
        config=RuntimeConfig(
            discovery_url="https://config.example.com/.well-known/openid-configuration",
            environment="sandbox",
        )
    )


def _oauth_context() -> ExecutionContext:
    """Build a context carrying OAuth non-secret config values alongside base fields."""
    return ExecutionContext(
        config=RuntimeConfig(
            discovery_url="https://config.example.com/.well-known/openid-configuration",
            environment="sandbox",
            oauth_resource_base_url="https://rs.example.com",
            oauth_client_id="my-client-id",
            oauth_redirect_uri="https://app.example.com/callback",
            oauth_authorization_endpoint="https://auth.example.com/authorize",
        )
    )


@pytest.mark.unit
class TestRecordStep:
    """Verify execution-context step capture remains immutable."""

    def test_records_step_into_new_context(self) -> None:
        """Recording a step stores the request and response in a new context."""
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
        """Recording a transport failure leaves the response slot empty."""
        ctx = ExecutionContext()
        request = RequestRecord(method="GET", url="https://example.com/api")

        new_ctx = record_step(ctx, "step-1", request, None)

        assert new_ctx.steps["step-1"].response is None

    def test_preserves_existing_steps(self) -> None:
        """Adding a new step must not drop previously recorded steps."""
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

    def test_preserves_runtime_config(self) -> None:
        """Recording steps must preserve safe runtime config placeholders."""
        ctx = _runtime_config_context()
        request = RequestRecord(method="GET", url="https://example.com/api")

        new_ctx = record_step(ctx, "step-1", request, None)

        assert new_ctx.config == ctx.config
        assert resolve_placeholders("${config.environment}", new_ctx) == "sandbox"

    def test_steps_isolated_from_caller_mutation(self) -> None:
        """ExecutionContext must copy the incoming steps mapping."""
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
        """ExecutionContext.steps must reject top-level mutation attempts."""
        ctx = ExecutionContext(
            steps={
                "step-a": StepRecord(
                    request=RequestRecord(method="GET", url="https://example.com/a"),
                    response=ResponseRecord(status_code=200, body={}),
                )
            }
        )

        with pytest.raises(TypeError):
            ctx.steps["new"] = StepRecord(  # type: ignore[index]  # Intentional runtime immutability probe.
                request=RequestRecord(method="GET", url="https://evil.com"),
                response=None,
            )


@pytest.mark.unit
class TestResponseRecordHeaders:
    """Verify captured response headers are isolated and case-insensitive."""

    def test_copies_headers_from_mutable_source(self) -> None:
        """Mutating the source header dict must not affect the stored record."""
        headers = {"X-Trace-ID": "trace-123"}

        record = ResponseRecord(status_code=200, headers=headers, body={"ok": True})
        headers["X-Trace-ID"] = "mutated"

        assert record.headers["x-trace-id"] == "trace-123"

    def test_headers_are_case_insensitive(self) -> None:
        """Header lookup must ignore case to match HTTP semantics."""
        record = ResponseRecord(
            status_code=200,
            headers={"Content-Type": "application/json", "X-Trace-ID": "trace-123"},
            body={"ok": True},
        )

        assert record.headers["content-type"] == "application/json"
        assert record.headers["CONTENT-TYPE"] == "application/json"
        assert "x-trace-id" in record.headers

    def test_headers_reject_in_place_mutation(self) -> None:
        """Stored response headers must be immutable."""
        record = ResponseRecord(status_code=200, headers={"X-Trace-ID": "trace-123"}, body={})

        with pytest.raises(TypeError):
            record.headers["x-new"] = "value"  # type: ignore[index]  # Intentional immutability probe.


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

    def test_resolves_array_index_body_path(self) -> None:
        """A numeric segment should index into a JSON array intermediate."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={
                            "Data": {
                                "Account": [
                                    {"AccountId": "account-123"},
                                    {"AccountId": "account-456"},
                                ]
                            }
                        },
                    ),
                )
            }
        )

        result = resolve_placeholders(
            "${steps.accounts-list.response.body.Data.Account.0.AccountId}",
            ctx,
        )

        assert result == "account-123"

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

    def test_resolves_config_discovery_url(self) -> None:
        ctx = _runtime_config_context()
        result = resolve_placeholders("${config.discoveryUrl}", ctx)
        assert result == "https://config.example.com/.well-known/openid-configuration"

    def test_resolves_config_environment(self) -> None:
        ctx = _runtime_config_context()
        result = resolve_placeholders("env=${config.environment}", ctx)
        assert result == "env=sandbox"

    def test_resolves_config_oauth_client_id(self) -> None:
        ctx = _oauth_context()
        result = resolve_placeholders("client_id=${config.oauth.clientId}", ctx)
        assert result == "client_id=my-client-id"

    def test_resolves_config_oauth_redirect_uri(self) -> None:
        ctx = _oauth_context()
        result = resolve_placeholders("${config.oauth.redirectUri}", ctx)
        assert result == "https://app.example.com/callback"

    def test_resolves_config_oauth_authorization_endpoint(self) -> None:
        ctx = _oauth_context()
        result = resolve_placeholders("${config.oauth.authorizationEndpoint}", ctx)
        assert result == "https://auth.example.com/authorize"

    def test_resolves_config_oauth_resource_base_url(self) -> None:
        ctx = _oauth_context()
        result = resolve_placeholders("${config.oauth.resourceBaseUrl}", ctx)
        assert result == "https://rs.example.com"

    def test_resolves_oauth_alongside_other_placeholders(self) -> None:
        ctx = _oauth_context()
        template = (
            "client=${config.oauth.clientId}&redirect=${config.oauth.redirectUri}"
            "&auth=${config.oauth.authorizationEndpoint}&resource=${config.oauth.resourceBaseUrl}"
        )
        result = resolve_placeholders(template, ctx)
        assert result == (
            "client=my-client-id&redirect=https://app.example.com/callback"
            "&auth=https://auth.example.com/authorize&resource=https://rs.example.com"
        )


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

    def test_array_index_out_of_bounds(self) -> None:
        """Array placeholder indices must fail clearly when the item is missing."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [{"AccountId": "account-123"}]}},
                    ),
                )
            }
        )

        with pytest.raises(PlaceholderResolutionError, match="Array index 1 out of bounds"):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.1.AccountId}", ctx)

    def test_array_index_rejects_non_numeric_segment(self) -> None:
        """Array traversal must not allow object-style field names as indices."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [{"AccountId": "account-123"}]}},
                    ),
                )
            }
        )

        with pytest.raises(
            PlaceholderResolutionError,
            match="Cannot traverse array with non-numeric segment 'AccountId'",
        ):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.AccountId}", ctx)

    def test_array_index_rejects_negative_segment(self) -> None:
        """Negative array indices are not part of the placeholder grammar."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [{"AccountId": "account-123"}]}},
                    ),
                )
            }
        )

        with pytest.raises(PlaceholderResolutionError, match="Cannot traverse array with non-numeric segment '-1'"):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.-1.AccountId}", ctx)

    def test_array_index_rejects_digit_like_non_decimal_segment(self) -> None:
        """Digit-like Unicode segments must still fail as invalid array indices."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [{"AccountId": "account-123"}]}},
                    ),
                )
            }
        )

        with pytest.raises(PlaceholderResolutionError, match="Cannot traverse array with non-numeric segment '²'"):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.².AccountId}", ctx)

    def test_array_index_final_object_is_rejected(self) -> None:
        """An indexed path that ends on an object must still fail as non-primitive."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [{"AccountId": "account-123"}]}},
                    ),
                )
            }
        )

        with pytest.raises(PlaceholderResolutionError, match="not a primitive.*object"):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.0}", ctx)

    def test_array_index_final_array_is_rejected(self) -> None:
        """An indexed path that ends on an array must still fail as non-primitive."""
        ctx = ExecutionContext(
            steps={
                "accounts-list": StepRecord(
                    request=RequestRecord(method="GET", url="https://rs.example.com/accounts"),
                    response=ResponseRecord(
                        status_code=200,
                        body={"Data": {"Account": [["account-123"]]}},
                    ),
                )
            }
        )

        with pytest.raises(PlaceholderResolutionError, match="not a primitive.*array"):
            resolve_placeholders("${steps.accounts-list.response.body.Data.Account.0}", ctx)

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
        # The narrower subclass lets the executor branch to a SKIPPED outcome.
        with pytest.raises(MissingPredecessorResponseError, match="has no response"):
            resolve_placeholders("${steps.failed.response.body.x}", ctx)
        # And the subclass relationship is preserved for callers catching the base.
        assert issubclass(MissingPredecessorResponseError, PlaceholderResolutionError)

    def test_invalid_request_field(self) -> None:
        ctx = _discovery_context()
        with pytest.raises(PlaceholderResolutionError, match="Cannot resolve request path"):
            resolve_placeholders("${steps.openid-discovery.request.body.x}", ctx)

    def test_unknown_config_placeholder_is_rejected(self) -> None:
        ctx = _runtime_config_context()
        with pytest.raises(PlaceholderResolutionError, match="Unsupported config placeholder"):
            resolve_placeholders("${config.tls.clientPrivateKeyPath}", ctx)

    def test_unknown_oauth_sub_field_is_rejected(self) -> None:
        """Fields not on the oauth allow-list must fail with 'Unsupported config placeholder'."""
        ctx = _oauth_context()
        with pytest.raises(PlaceholderResolutionError, match="Unsupported config placeholder"):
            resolve_placeholders("${config.oauth.clientSecret}", ctx)

    def test_fapi_signing_placeholder_is_rejected(self) -> None:
        """Dedicated signing config must stay outside the runtime placeholder allow-list."""
        ctx = _oauth_context()
        with pytest.raises(PlaceholderResolutionError, match="Unsupported config placeholder"):
            resolve_placeholders("${config.fapiSigning.kid}", ctx)

    def test_oauth_client_id_unavailable_when_oauth_config_absent(self) -> None:
        """${config.oauth.clientId} must fail when RuntimeConfig has no oauth fields."""
        ctx = _runtime_config_context()
        with pytest.raises(PlaceholderResolutionError, match="OAuth config is not available"):
            resolve_placeholders("${config.oauth.clientId}", ctx)

    def test_oauth_redirect_uri_unavailable_when_oauth_config_absent(self) -> None:
        """${config.oauth.redirectUri} must fail when RuntimeConfig has no oauth fields."""
        ctx = _runtime_config_context()
        with pytest.raises(PlaceholderResolutionError, match="OAuth config is not available"):
            resolve_placeholders("${config.oauth.redirectUri}", ctx)

    def test_oauth_resource_base_url_unavailable_when_oauth_config_absent(self) -> None:
        """${config.oauth.resourceBaseUrl} must fail when RuntimeConfig has no oauth fields."""
        ctx = _runtime_config_context()
        with pytest.raises(PlaceholderResolutionError, match="OAuth config is not available"):
            resolve_placeholders("${config.oauth.resourceBaseUrl}", ctx)

    def test_oauth_authorization_endpoint_unavailable_when_oauth_config_absent(self) -> None:
        """${config.oauth.authorizationEndpoint} must fail when RuntimeConfig has no oauth fields."""
        ctx = _runtime_config_context()
        with pytest.raises(PlaceholderResolutionError, match="OAuth config is not available"):
            resolve_placeholders("${config.oauth.authorizationEndpoint}", ctx)

    def test_oauth_resource_base_url_unavailable_when_field_omitted(self) -> None:
        """${config.oauth.resourceBaseUrl} must identify the omitted optional field."""
        ctx = ExecutionContext(
            config=RuntimeConfig(
                discovery_url="https://config.example.com/.well-known/openid-configuration",
                environment="sandbox",
                oauth_client_id="my-client-id",
                oauth_redirect_uri="https://app.example.com/callback",
            )
        )
        with pytest.raises(PlaceholderResolutionError, match="oauth.resourceBaseUrl is not available"):
            resolve_placeholders("${config.oauth.resourceBaseUrl}", ctx)

    def test_oauth_authorization_endpoint_unavailable_when_field_omitted(self) -> None:
        """${config.oauth.authorizationEndpoint} must identify the omitted optional field."""
        ctx = ExecutionContext(
            config=RuntimeConfig(
                discovery_url="https://config.example.com/.well-known/openid-configuration",
                environment="sandbox",
                oauth_client_id="my-client-id",
                oauth_redirect_uri="https://app.example.com/callback",
            )
        )
        with pytest.raises(PlaceholderResolutionError, match="oauth.authorizationEndpoint is not available"):
            resolve_placeholders("${config.oauth.authorizationEndpoint}", ctx)

    def test_config_placeholder_requires_runtime_config(self) -> None:
        ctx = ExecutionContext()
        with pytest.raises(PlaceholderResolutionError, match="Runtime config is not available"):
            resolve_placeholders("${config.discoveryUrl}", ctx)


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

    def test_unterminated_placeholder_does_not_leak_full_template(self) -> None:
        """Error message must not contain the full template when it is long."""
        ctx = _discovery_context()
        sensitive_prefix = "https://bank.example.com/authorize?client_secret=SUPER_SECRET_TOKEN_12345&"
        template = f"{sensitive_prefix}redirect_uri=${{steps.openid-discovery.request.url"
        with pytest.raises(PlaceholderResolutionError, match="Unterminated placeholder") as exc_info:
            resolve_placeholders(template, ctx)
        error_msg = str(exc_info.value)
        assert "SUPER_SECRET_TOKEN_12345" not in error_msg

    def test_unterminated_placeholder_error_excludes_prefix_entirely(self) -> None:
        """Error context must never include any characters preceding ``${``.

        Defence in depth: even short prefix fragments (e.g. partial token
        tails or query-parameter names) must not appear in error output.
        Only the ``${`` opener and trailing identifier should be shown.
        """
        ctx = _discovery_context()
        # A short prefix whose final characters would have been captured by
        # the previous prefix-window implementation.
        sensitive_prefix = "?token=abcdef0123456789"
        template = f"{sensitive_prefix}${{steps.openid-discovery.request.url"
        with pytest.raises(PlaceholderResolutionError, match="Unterminated placeholder") as exc_info:
            resolve_placeholders(template, ctx)
        error_msg = str(exc_info.value)
        # No character from the prefix may appear in the error context.
        for fragment in ("token=", "abcdef", "0123456789", "?token"):
            assert fragment not in error_msg
        # The error must still identify the malformed token meaningfully.
        assert "${steps.openid" in error_msg


@pytest.mark.unit
class TestResolveInStructure:
    def test_resolves_string_leaf(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        result = resolve_in_structure("${steps.openid-discovery.response.body.issuer}", ctx)
        assert result == "https://auth.example.com"

    def test_resolves_nested_dict(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        structure: JsonValue = {
            "endpoint": "${steps.openid-discovery.response.body.token_endpoint}",
            "static": "literal",
        }
        result = resolve_in_structure(structure, ctx)
        assert result == {
            "endpoint": "https://auth.example.com/token",
            "static": "literal",
        }

    def test_resolves_config_placeholder_in_nested_dict(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _runtime_config_context()
        structure: JsonValue = {"target": "${config.discoveryUrl}", "environment": "${config.environment}"}
        result = resolve_in_structure(structure, ctx)
        assert result == {
            "target": "https://config.example.com/.well-known/openid-configuration",
            "environment": "sandbox",
        }

    def test_resolves_nested_list(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        structure: JsonValue = ["${steps.openid-discovery.response.body.issuer}", "static"]
        result = resolve_in_structure(structure, ctx)
        assert result == ["https://auth.example.com", "static"]

    def test_preserves_non_string_leaves(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        structure: JsonValue = {
            "count": 42,
            "active": True,
            "ratio": 3.14,
            "nothing": None,
        }
        result = resolve_in_structure(structure, ctx)
        assert result == {"count": 42, "active": True, "ratio": 3.14, "nothing": None}

    def test_deeply_nested_structure(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        structure: JsonValue = {
            "outer": {
                "inner": [
                    {"url": "${steps.openid-discovery.response.body.jwks_uri}"},
                ]
            }
        }
        result = resolve_in_structure(structure, ctx)
        assert result == {"outer": {"inner": [{"url": "https://auth.example.com/jwks"}]}}

    def test_raises_on_unresolvable_placeholder(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        structure: JsonValue = {"ref": "${steps.openid-discovery.response.body.nonexistent}"}
        with pytest.raises(PlaceholderResolutionError, match="nonexistent"):
            resolve_in_structure(structure, ctx)

    def test_scalar_passthrough(self) -> None:
        from conformance.context import resolve_in_structure

        ctx = _discovery_context()
        assert resolve_in_structure(42, ctx) == 42
        assert resolve_in_structure(True, ctx) is True
        assert resolve_in_structure(None, ctx) is None
