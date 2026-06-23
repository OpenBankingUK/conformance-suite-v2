"""Compiled execution configuration assembled from modular inputs.

A RunConfiguration is the single, validated, immutable artifact passed to the
conformance executor for a run. It is produced by the RunConfigurationCompiler
from:

- the selected suite manifest (baseline values, generated keys, allowed keys);
- the selected run plan (step selection, endpoint selection);
- the participant's TestDataConfig (custom test-data values for the target ASPSP);
- optional inline UI edits applied at plan-builder time.

The compiler normalises away same-as-baseline values so that only genuine
deltas from the suite baseline appear in ``effective_test_values`` and
``baseline_delta_keys``. This drives certification value-purity gating and
result/log impact evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from conformance.manifest import ManifestTestValues

if TYPE_CHECKING:
    from conformance.manifest import Manifest

ValueSource = Literal["baseline", "custom", "generated"]
"""Origin of a resolved test value.

- ``"baseline"``: value matches the suite manifest baseline exactly.
- ``"custom"``: value is a participant-supplied delta from the suite baseline.
- ``"generated"``: value is produced at run-time (e.g. per-run UUID).
"""


@dataclass(frozen=True)
class ResolvedTestValue:
    """A single resolved test value for execution.

    Attributes:
        key: The placeholder key name (e.g. ``"creditorIdentification"``).
        value: The resolved string value used during execution.
        source: How the value was determined (``"baseline"``, ``"custom"``, or
            ``"generated"``).
        baseline_value: The suite manifest baseline value for this key, or
            ``None`` if the key has no baseline (generated-only keys).
    """

    key: str
    value: str
    source: ValueSource
    baseline_value: str | None


@dataclass(frozen=True)
class RunConfiguration:
    """Compiled, validated execution configuration for a single conformance run.

    Produced by :class:`RunConfigurationCompiler` from the selected suite
    manifest, run plan, and participant test data. Immutable once built.

    Attributes:
        suite_id: Manifest suite identifier.
        effective_test_values: Mapping of key to resolved value for all keys
            referenced by the selected plan. Includes baseline, custom, and
            generated values.
        resolved_values: Mapping of key to full :class:`ResolvedTestValue`
            for evidence and certification gating.
        baseline_delta_keys: Set of keys whose effective value differs from
            the suite baseline. Empty means all values match baseline (certifiable
            subject to coverage gate).
        missing_required_keys: Set of keys required by selected steps but not
            provided by baseline or test data. Non-empty means the run cannot
            proceed.
    """

    suite_id: str
    effective_test_values: Mapping[str, str]
    resolved_values: Mapping[str, ResolvedTestValue]
    baseline_delta_keys: frozenset[str]
    missing_required_keys: frozenset[str]

    @property
    def is_certifiable_by_value_purity(self) -> bool:
        """Return True when all baseline-backed values match the suite baseline.

        Does not check coverage completeness — that is a separate gate.

        Returns:
            ``True`` when :attr:`baseline_delta_keys` is empty.
        """
        return len(self.baseline_delta_keys) == 0

    @property
    def has_custom_values(self) -> bool:
        """Return True when at least one effective value differs from baseline.

        Returns:
            ``True`` when :attr:`baseline_delta_keys` is non-empty.
        """
        return len(self.baseline_delta_keys) > 0

    @property
    def can_execute(self) -> bool:
        """Return True when no required keys are missing.

        Returns:
            ``True`` when :attr:`missing_required_keys` is empty.
        """
        return len(self.missing_required_keys) == 0


class RunConfigurationCompiler:
    """Compiles modular inputs into a validated RunConfiguration.

    Combines suite manifest baseline values, selected plan references, and
    participant test-data values into an immutable RunConfiguration. Normalises
    same-as-baseline submitted values away. Tracks baseline deltas, required
    missing keys, and unknown submitted keys.
    """

    def __init__(self, manifest_test_values: ManifestTestValues) -> None:
        """Initialise the compiler with manifest test-value declarations.

        Args:
            manifest_test_values: Parsed test-value declarations from the suite
                manifest, providing baseline values, generated key strategies,
                and the allowed custom key set.
        """
        self._manifest = manifest_test_values

    def compile(
        self,
        *,
        suite_id: str,
        referenced_keys: set[str],
        test_data_values: Mapping[str, str],
    ) -> RunConfiguration:
        """Compile a RunConfiguration from suite manifest data and participant values.

        For each referenced key:
        - If in ``generated_keys``: source is ``"generated"``, value is the
          strategy identifier (executor expands at runtime).
        - If in ``test_data_values`` AND value differs from baseline: source is
          ``"custom"``, recorded as a baseline delta.
        - If in ``test_data_values`` AND value matches baseline: treated as
          absent (normalised away).
        - If in ``baseline``: source is ``"baseline"``, uses baseline value.
        - Otherwise: added to ``missing_required_keys``.

        Rejects any key in ``test_data_values`` not in ``allowed_custom_keys``
        by raising ``ValueError``.

        Args:
            suite_id: Manifest suite identifier for the compiled artifact.
            referenced_keys: Set of placeholder key names referenced by
                ``${testValues.X}`` in the selected plan steps.
            test_data_values: Participant-supplied test-data values from
                ``TestDataConfig.values`` or ``RunPlanTestData.values``.

        Returns:
            An immutable :class:`RunConfiguration` with all resolved values,
            delta tracking, and missing key tracking populated.

        Raises:
            ValueError: If any key in ``test_data_values`` is not listed in
                the manifest's ``allowed_custom_keys``.
        """
        disallowed = sorted(key for key in test_data_values if key not in self._manifest.allowed_custom_keys)
        if disallowed:
            raise ValueError(f"Test data contains keys not in allowedCustomKeys: {disallowed!r}")

        effective: dict[str, str] = {}
        resolved: dict[str, ResolvedTestValue] = {}
        delta_keys: set[str] = set()
        missing: set[str] = set()

        for key in referenced_keys:
            if key in self._manifest.generated_keys:
                strategy = self._manifest.generated_keys[key]
                rv = ResolvedTestValue(
                    key=key,
                    value=strategy,
                    source="generated",
                    baseline_value=None,
                )
                effective[key] = strategy
                resolved[key] = rv
            elif key in test_data_values:
                custom_val = test_data_values[key]
                baseline_val = self._manifest.baseline.get(key)
                if baseline_val is not None and custom_val == baseline_val:
                    rv = ResolvedTestValue(
                        key=key,
                        value=baseline_val,
                        source="baseline",
                        baseline_value=baseline_val,
                    )
                    effective[key] = baseline_val
                else:
                    rv = ResolvedTestValue(
                        key=key,
                        value=custom_val,
                        source="custom",
                        baseline_value=baseline_val,
                    )
                    effective[key] = custom_val
                    delta_keys.add(key)
                resolved[key] = rv
            elif key in self._manifest.baseline:
                baseline_val = self._manifest.baseline[key]
                rv = ResolvedTestValue(
                    key=key,
                    value=baseline_val,
                    source="baseline",
                    baseline_value=baseline_val,
                )
                effective[key] = baseline_val
                resolved[key] = rv
            else:
                missing.add(key)

        return RunConfiguration(
            suite_id=suite_id,
            effective_test_values=MappingProxyType(effective),
            resolved_values=MappingProxyType(resolved),
            baseline_delta_keys=frozenset(delta_keys),
            missing_required_keys=frozenset(missing),
        )


def compile_run_configuration(
    *,
    manifest: Manifest,
    selected_step_ids: set[str] | None = None,
    test_data_values: Mapping[str, str],
) -> RunConfiguration | None:
    """Compile a RunConfiguration for the given manifest and participant test data.

    Returns ``None`` when the manifest declares no ``testValues`` block (i.e.
    no test-value placeholders exist in the suite).

    Args:
        manifest: Parsed suite manifest.
        selected_step_ids: Optional set of step IDs to restrict referenced key
            extraction to selected steps. When ``None``, all steps are included.
        test_data_values: Participant test-data values from
            ``TestDataConfig.values`` or ``RunPlanTestData.values``.

    Returns:
        A compiled :class:`RunConfiguration`, or ``None`` if the manifest has
        no ``testValues`` block.

    Raises:
        ValueError: If test data contains keys not in ``allowedCustomKeys``.
    """
    if manifest.test_values is None:
        return None

    referenced_keys: set[str] = set()
    for step in manifest.steps:
        if selected_step_ids is None or step.id in selected_step_ids:
            referenced_keys.update(step.consumed_test_value_keys)

    compiler = RunConfigurationCompiler(manifest.test_values)
    return compiler.compile(
        suite_id=manifest.name,
        referenced_keys=referenced_keys,
        test_data_values=test_data_values,
    )
