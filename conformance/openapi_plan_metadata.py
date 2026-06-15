"""Derive plan-builder tree metadata from bundled OpenAPI docs and manifest analysis.

This module maps manifest steps onto bundled Open Banking standards operations,
then builds a hierarchical tree used by the plan-builder UI. Grouping combines
OpenAPI resource tags with endpoint and permission-variant metadata so
participants can reason about selection impact at multiple levels.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from typing import TYPE_CHECKING, Literal

from conformance.manifest import FormBody, Manifest, ManifestStep, PsuAuthorizationStep
from conformance.suite_catalog import SuiteMetadata
from conformance.test_plan import TestPlan

if TYPE_CHECKING:
    from conformance.api.plan_builder import PlanAuthBundle, PlanStepRow

type TreeNodeType = Literal[
    "standard",
    "specVersion",
    "api",
    "resourceGroup",
    "endpoint",
    "variant",
    "authBundle",
    "step",
]
"""Tree node type label used to drive tree rendering in the plan-builder template."""


@dataclass(frozen=True)
class OpenApiOperation:
    """Single operation entry from a bundled OpenAPI standards document.

    Attributes:
        method: HTTP method in uppercase (GET, POST, etc.)
        path: OpenAPI path string including {Param} placeholders.
        tags: Operation tags used for resource group derivation.
        summary: Human-readable operation summary.
        operation_id: OpenAPI operationId value.
    """

    method: str
    path: str
    tags: tuple[str, ...]
    summary: str
    operation_id: str


@dataclass(frozen=True)
class OperationMatch:
    """Result of matching a manifest step to an OpenAPI operation.

    Attributes:
        step_id: Manifest step identifier.
        operation: Matched OpenAPI operation, or None when the step is non-resource.
        group_label: Human-readable group label. For resource steps this is the
            OpenAPI tag (resource group). For non-resource steps this is a
            generated group name.
        endpoint_label: Label for the endpoint node. For resource steps: "METHOD /path".
            For non-resource steps: same as group_label.
        variant_label: Optional permission-set or content variant label. Derived
            from auth bundle required OB permissions when available.
    """

    step_id: str
    operation: OpenApiOperation | None
    group_label: str
    endpoint_label: str
    variant_label: str


@dataclass(frozen=True)
class StepTreeNode:
    """Node in the visual plan-builder selection tree.

    Attributes:
        id: Stable node identifier derived from non-secret suite metadata and path components.
        label: Human-readable label for rendering.
        node_type: Tree level type.
        children: Immediate child nodes (empty for leaf step nodes).
        descendant_step_ids: All leaf step IDs reachable from this node.
        step_rows_at_node: PlanStepRow objects for steps directly at this leaf node
            (non-empty only for "step" nodes).
        total_count: Count of all descendant leaf steps.
        selected_count: Count of currently selected descendant leaf steps.
        mandatory_count: Count of mandatory descendant steps.
        optional_count: Count of optional descendant steps.
        cert_impact_count: Count of descendant steps whose deselection impacts certification.
        auth_bundle_id: Auth bundle id for authBundle-type nodes, or None.
    """

    id: str
    label: str
    node_type: TreeNodeType
    children: tuple[StepTreeNode, ...]
    descendant_step_ids: tuple[str, ...]
    step_rows_at_node: tuple[PlanStepRow, ...]
    total_count: int
    selected_count: int
    mandatory_count: int
    optional_count: int
    cert_impact_count: int
    auth_bundle_id: str | None


_OPERATION_METHODS = {"get", "post", "put", "patch", "delete"}
"""HTTP methods read from OpenAPI operation objects for tree metadata matching."""


_RESOURCE_BASE_PATTERN = re.compile(
    r"^\s*(?:\{\{resourceBaseUrl\}\}|\{resourceBaseUrl\}|\$\{config\.oauth\.resourceBaseUrl\})/?"
)
"""Prefix pattern for manifest resource base URL placeholders that should not affect path matching."""


_DOUBLE_BRACE_PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
"""Manifest placeholder pattern converted to OpenAPI-style ``{Param}`` segments."""


_DYNAMIC_SEGMENT_PATTERN = re.compile(r"^\$\{[^}]+\}$")
"""Segment pattern matching runtime placeholders used in manifest URLs."""


_OB_PATH_PREFIX_PATTERN = re.compile(r"^/open-banking/v[0-9.]+/[^/]+")
"""Open Banking server prefix removed from manifest URLs before OpenAPI path matching."""


def load_openapi_operations(suite_metadata: SuiteMetadata | None) -> dict[tuple[str, str], OpenApiOperation]:
    """Load bundled OpenAPI operations for a resolved suite metadata record.

    Args:
        suite_metadata: Catalog metadata for the selected suite, or ``None`` for
            custom/non-catalog manifests.

    Returns:
        Mapping keyed by ``(METHOD, path)`` for OpenAPI operations relevant to the
        selected suite standard/version. Returns an empty mapping when no bundled
        OpenAPI document applies.
    """
    if suite_metadata is None:
        return {}
    if suite_metadata.standard != "ob-read-write":
        return {}

    package_suffix = _openapi_spec_version_to_package(suite_metadata.spec_version)
    if package_suffix is None:
        return {}

    package_name = f"conformance.standards.ob_read_write.{package_suffix}"
    try:
        raw_document = resources.files(package_name).joinpath("account-info-openapi.json").read_text(encoding="utf-8")
    except ModuleNotFoundError, FileNotFoundError:
        return {}

    parsed_document = json.loads(raw_document)
    if not isinstance(parsed_document, dict):
        return {}

    raw_paths = parsed_document.get("paths")
    if not isinstance(raw_paths, dict):
        return {}

    index: dict[tuple[str, str], OpenApiOperation] = {}
    for raw_path, raw_path_item in raw_paths.items():
        if not isinstance(raw_path, str) or not isinstance(raw_path_item, dict):
            continue
        for raw_method, raw_operation in raw_path_item.items():
            if not isinstance(raw_method, str) or raw_method.lower() not in _OPERATION_METHODS:
                continue
            if not isinstance(raw_operation, dict):
                continue
            method = raw_method.upper()
            tags_value = raw_operation.get("tags")
            tags = tuple(tag for tag in tags_value if isinstance(tag, str)) if isinstance(tags_value, list) else ()
            summary_value = raw_operation.get("summary")
            operation_id_value = raw_operation.get("operationId")
            index[(method, raw_path)] = OpenApiOperation(
                method=method,
                path=raw_path,
                tags=tags,
                summary=summary_value if isinstance(summary_value, str) else "",
                operation_id=operation_id_value if isinstance(operation_id_value, str) else "",
            )
    return index


def normalize_manifest_url(url: str) -> str:
    """Normalize a manifest request URL into an OpenAPI-comparable path.

    Args:
        url: Raw manifest request URL containing placeholders and optional query
            strings.

    Returns:
        Normalized path string suitable for matching against OpenAPI path keys.
    """
    without_query = url.split("?", 1)[0]
    without_scheme = re.sub(r"^https?://[^/]+", "", without_query)
    without_base_placeholder = _RESOURCE_BASE_PATTERN.sub("", without_scheme)
    replaced_placeholders = _DOUBLE_BRACE_PLACEHOLDER_PATTERN.sub(r"{\1}", without_base_placeholder)
    if not replaced_placeholders.startswith("/"):
        replaced_placeholders = f"/{replaced_placeholders}"
    normalized = re.sub(r"/+", "/", replaced_placeholders).rstrip("/")
    if not normalized:
        normalized = "/"
    stripped_server_prefix = _OB_PATH_PREFIX_PATTERN.sub("", normalized)
    if stripped_server_prefix:
        return stripped_server_prefix if stripped_server_prefix.startswith("/") else f"/{stripped_server_prefix}"
    return normalized


def match_steps_to_operations(
    manifest: Manifest,
    openapi_index: dict[tuple[str, str], OpenApiOperation],
    auth_bundles: tuple[PlanAuthBundle, ...],
) -> tuple[OperationMatch, ...]:
    """Map manifest steps to OpenAPI operation metadata and display labels.

    Args:
        manifest: Parsed manifest whose steps are rendered in the plan tree.
        openapi_index: OpenAPI operation lookup keyed by ``(METHOD, path)``.
        auth_bundles: Selected-plan auth bundle inventory used to infer variant labels.

    Returns:
        Operation matches in manifest step order.
    """
    bundle_by_step_id = _bundle_by_consuming_step_id(auth_bundles)
    matches: list[OperationMatch] = []

    for step in manifest.steps:
        if isinstance(step, PsuAuthorizationStep):
            matches.append(
                OperationMatch(
                    step_id=step.id,
                    operation=None,
                    group_label="PSU authorisation",
                    endpoint_label="PSU authorisation",
                    variant_label="",
                )
            )
            continue

        if _is_token_exchange_step(step):
            matches.append(
                OperationMatch(
                    step_id=step.id,
                    operation=None,
                    group_label="Token exchange",
                    endpoint_label="Token exchange",
                    variant_label="",
                )
            )
            continue

        request_url = step.request.url
        if step.request.method == "POST" and "account-access-consents" in request_url.lower():
            matches.append(
                OperationMatch(
                    step_id=step.id,
                    operation=None,
                    group_label="Consent setup",
                    endpoint_label="Consent setup",
                    variant_label="",
                )
            )
            continue

        if ".well-known" in request_url.lower():
            matches.append(
                OperationMatch(
                    step_id=step.id,
                    operation=None,
                    group_label="Setup and discovery",
                    endpoint_label="Setup and discovery",
                    variant_label="",
                )
            )
            continue

        normalized_path = normalize_manifest_url(request_url)
        operation = _match_openapi_operation(step.request.method, normalized_path, openapi_index)
        if operation is None:
            matches.append(
                OperationMatch(
                    step_id=step.id,
                    operation=None,
                    group_label="Other steps",
                    endpoint_label=f"{step.request.method} {normalized_path}",
                    variant_label="",
                )
            )
            continue

        tag = operation.tags[0] if operation.tags else _path_fallback_group_label(operation.path)
        group_label = _tag_to_resource_group_label(tag)
        auth_bundle = bundle_by_step_id.get(step.id)
        variant_label = (
            _permissions_to_variant_label(auth_bundle.required_ob_permissions) if auth_bundle else "Default permissions"
        )
        matches.append(
            OperationMatch(
                step_id=step.id,
                operation=operation,
                group_label=group_label,
                endpoint_label=f"{operation.method} {operation.path}",
                variant_label=variant_label,
            )
        )

    return tuple(matches)


def build_plan_tree(
    *,
    manifest: Manifest,
    suite_metadata: SuiteMetadata | None,
    selected_plan: TestPlan,
    rows: tuple[PlanStepRow, ...],
    auth_bundles: tuple[PlanAuthBundle, ...],
) -> tuple[StepTreeNode, ...]:
    """Build tree nodes for grouped plan-builder step selection rendering.

    Args:
        manifest: Parsed manifest used to source step names and order.
        suite_metadata: Optional suite metadata used for stable node-id salting.
        selected_plan: Selected plan state used as a fallback selected signal.
        rows: Step presenter rows in manifest order.
        auth_bundles: Selected auth bundles used to attach auth-bundle metadata.

    Returns:
        Top-level resource-group tree nodes.
    """
    openapi_index = load_openapi_operations(suite_metadata)
    matches = match_steps_to_operations(manifest, openapi_index, auth_bundles)

    rows_by_id = {row.id: row for row in rows}
    steps_by_id = {step.id: step for step in manifest.steps}
    selected_ids = set(selected_plan.selected_step_ids())
    bundle_by_step_id = _bundle_by_consuming_step_id(auth_bundles)
    suite_catalog_id = "" if suite_metadata is None else suite_metadata.catalog_id

    grouped: dict[str, dict[str, dict[tuple[str, str | None], list[StepTreeNode]]]] = {}
    for match in matches:
        step = steps_by_id.get(match.step_id)
        if step is None:
            continue
        row = rows_by_id.get(match.step_id)
        if row is None:
            from conformance.api.plan_builder import PlanStepRow

            fallback_selected = match.step_id in selected_ids
            row = PlanStepRow(
                id=step.id,
                name=step.name,
                kind="psu-authorization" if isinstance(step, PsuAuthorizationStep) else "http",
                group=step.group,
                phase=step.phase,
                mandatory=step.mandatory,
                optional=step.optional,
                default_selected=fallback_selected,
                selected_after_form=fallback_selected,
                certification_required=step.mandatory,
                deselection_impacts_certification=step.mandatory,
                certification_blocked_by_deselection=step.mandatory and not fallback_selected,
            )

        step_node = StepTreeNode(
            id=step.id,
            label=step.name,
            node_type="step",
            children=(),
            descendant_step_ids=(step.id,),
            step_rows_at_node=(row,),
            total_count=1,
            selected_count=1 if row.selected_after_form else 0,
            mandatory_count=1 if row.mandatory else 0,
            optional_count=1 if row.optional else 0,
            cert_impact_count=1 if row.deselection_impacts_certification else 0,
            auth_bundle_id=None,
        )

        group_bucket = grouped.setdefault(match.group_label, {})
        endpoint_bucket = group_bucket.setdefault(match.endpoint_label, {})
        step_bundle = bundle_by_step_id.get(step.id)
        bundle_id = step_bundle.id if step_bundle is not None else None
        variant_bucket_key = (match.variant_label, bundle_id)
        endpoint_bucket.setdefault(variant_bucket_key, []).append(step_node)

    group_nodes: list[StepTreeNode] = []
    for group_label, endpoint_map in grouped.items():
        endpoint_nodes: list[StepTreeNode] = []
        for endpoint_label, variant_map in endpoint_map.items():
            endpoint_children: list[StepTreeNode] = []
            collapse_variant_level = len(variant_map) == 1 and next(iter(variant_map.keys()))[0] == ""
            for (variant_label, bundle_id), step_nodes_list in variant_map.items():
                step_children = tuple(step_nodes_list)
                if collapse_variant_level:
                    endpoint_children.extend(step_children)
                    continue
                variant_type: TreeNodeType = "authBundle" if bundle_id is not None else "variant"
                endpoint_children.append(
                    _build_parent_node(
                        node_id=_stable_node_id(suite_catalog_id, group_label, endpoint_label, variant_label),
                        label=variant_label or "Default",
                        node_type=variant_type,
                        children=step_children,
                        auth_bundle_id=bundle_id,
                    )
                )

            endpoint_nodes.append(
                _build_parent_node(
                    node_id=_stable_node_id(suite_catalog_id, group_label, endpoint_label, ""),
                    label=endpoint_label,
                    node_type="endpoint",
                    children=tuple(endpoint_children),
                    auth_bundle_id=None,
                )
            )

        group_nodes.append(
            _build_parent_node(
                node_id=_stable_node_id(suite_catalog_id, group_label, "", ""),
                label=group_label,
                node_type="resourceGroup",
                children=tuple(endpoint_nodes),
                auth_bundle_id=None,
            )
        )

    return tuple(group_nodes)


def _tag_to_resource_group_label(tag: str) -> str:
    """Convert an OpenAPI operation tag into a readable resource-group label.

    Args:
        tag: Raw OpenAPI tag string.

    Returns:
        Humanized resource-group label.
    """
    collapsed = " ".join(tag.replace("_", " ").replace("-", " ").split())
    return collapsed if collapsed else "Resources"


def _permissions_to_variant_label(permissions: tuple[str, ...]) -> str:
    """Derive a plan-tree variant label from OB consent permissions.

    Args:
        permissions: Required Open Banking consent permissions.

    Returns:
        Short variant label suitable for endpoint subtree grouping.
    """
    unique_permissions = tuple(dict.fromkeys(permission for permission in permissions if permission))
    if not unique_permissions:
        return "Default permissions"
    if all(permission.startswith("Read") and permission.endswith("Basic") for permission in unique_permissions):
        return "Basic read"
    if all(permission.startswith("Read") and permission.endswith("Detail") for permission in unique_permissions):
        return "Detail read"
    return " + ".join(unique_permissions)


def _stable_node_id(*parts: str) -> str:
    """Create a deterministic short node identifier from stable metadata parts.

    Args:
        *parts: Ordered non-secret metadata segments defining node identity.

    Returns:
        Hex-encoded SHA256 prefix (16 chars) suitable for HTML/tree ids.
    """
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _openapi_spec_version_to_package(spec_version: str) -> str | None:
    """Map suite spec version text to bundled standards package suffix.

    Args:
        spec_version: Suite metadata spec version, such as ``"v4.0.1"``.

    Returns:
        Package suffix such as ``"v4_0_1"``, or ``None`` when unsupported.
    """
    if not spec_version.startswith("v"):
        return None
    version_body = spec_version[1:]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version_body):
        return None
    return f"v{version_body.replace('.', '_')}"


def _is_token_exchange_step(step: ManifestStep) -> bool:
    """Return whether a manifest HTTP step is an OAuth authorization-code exchange.

    Args:
        step: Parsed v1 HTTP manifest step.

    Returns:
        ``True`` when the step body is form-encoded and ``grant_type`` equals
        ``"authorization_code"``.
    """
    request_body = step.request.body
    if not isinstance(request_body, FormBody):
        return False
    return request_body.fields.get("grant_type") == "authorization_code"


def _match_openapi_operation(
    method: str,
    normalized_path: str,
    openapi_index: dict[tuple[str, str], OpenApiOperation],
) -> OpenApiOperation | None:
    """Match a normalized manifest path to an OpenAPI operation entry.

    Args:
        method: HTTP method used by the manifest step.
        normalized_path: Manifest URL normalized into path form.
        openapi_index: OpenAPI operation lookup map.

    Returns:
        Matched operation when a direct or placeholder-segment match is found,
        otherwise ``None``.
    """
    direct_match = openapi_index.get((method.upper(), normalized_path))
    if direct_match is not None:
        return direct_match

    method_upper = method.upper()
    for (candidate_method, candidate_path), operation in openapi_index.items():
        if candidate_method != method_upper:
            continue
        if _path_matches_template(candidate_path, normalized_path):
            return operation
    return None


def _path_matches_template(template_path: str, concrete_path: str) -> bool:
    """Compare an OpenAPI path template against a normalized manifest path.

    Args:
        template_path: OpenAPI path template with optional ``{Param}`` segments.
        concrete_path: Normalized manifest path with concrete or placeholder segments.

    Returns:
        ``True`` when both paths are structurally compatible segment-by-segment.
    """
    template_segments = [segment for segment in template_path.strip("/").split("/") if segment]
    concrete_segments = [segment for segment in concrete_path.strip("/").split("/") if segment]
    if len(template_segments) != len(concrete_segments):
        return False

    for template_segment, concrete_segment in zip(template_segments, concrete_segments, strict=True):
        if template_segment.startswith("{") and template_segment.endswith("}"):
            if not concrete_segment:
                return False
            continue
        if _DYNAMIC_SEGMENT_PATTERN.match(concrete_segment):
            continue
        if template_segment != concrete_segment:
            return False
    return True


def _path_fallback_group_label(path: str) -> str:
    """Derive a fallback resource-group label from the first non-empty path segment.

    Args:
        path: Normalized OpenAPI path.

    Returns:
        Humanized first path segment, or ``"Resources"`` when unavailable.
    """
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return "Resources"
    return _tag_to_resource_group_label(parts[0])


def _bundle_by_consuming_step_id(auth_bundles: tuple[PlanAuthBundle, ...]) -> dict[str, PlanAuthBundle]:
    """Build a step-id lookup for auth bundles that consume protected-resource steps.

    Args:
        auth_bundles: Auth bundles inferred for the selected plan.

    Returns:
        Mapping of consuming step id to corresponding auth bundle.
    """
    mapping: dict[str, PlanAuthBundle] = {}
    for bundle in auth_bundles:
        for step_id in bundle.consuming_step_ids:
            mapping[step_id] = bundle
    return mapping


def _build_parent_node(
    *,
    node_id: str,
    label: str,
    node_type: TreeNodeType,
    children: tuple[StepTreeNode, ...],
    auth_bundle_id: str | None,
) -> StepTreeNode:
    """Create a non-leaf tree node with aggregate descendant counters.

    Args:
        node_id: Stable node identifier.
        label: Human-readable label.
        node_type: Node type discriminator.
        children: Child nodes contributing aggregate counts.
        auth_bundle_id: Auth bundle id for auth-bundle nodes, else ``None``.

    Returns:
        Parent node with derived descendant ids and counters.
    """
    descendant_ids = tuple(step_id for child in children for step_id in child.descendant_step_ids)
    return StepTreeNode(
        id=node_id,
        label=label,
        node_type=node_type,
        children=children,
        descendant_step_ids=descendant_ids,
        step_rows_at_node=(),
        total_count=sum(child.total_count for child in children),
        selected_count=sum(child.selected_count for child in children),
        mandatory_count=sum(child.mandatory_count for child in children),
        optional_count=sum(child.optional_count for child in children),
        cert_impact_count=sum(child.cert_impact_count for child in children),
        auth_bundle_id=auth_bundle_id,
    )


__all__ = [
    "OperationMatch",
    "OpenApiOperation",
    "StepTreeNode",
    "TreeNodeType",
    "build_plan_tree",
    "load_openapi_operations",
    "match_steps_to_operations",
    "normalize_manifest_url",
]
