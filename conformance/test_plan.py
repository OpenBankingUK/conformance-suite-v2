"""First-class :class:`TestPlan` model with mandatory pre-population and deselection.

Implements PRD Participant Story #4 and OBL Standards Story #3: a manifest
declares which steps exist and which are mandatory/optional, but the
*plan* — the ordered subset of steps a participant will actually run — is
a separate object the caller can shape before execution.

By default the plan pre-populates with every mandatory step plus every
non-optional step. Callers may deselect individual steps (by id) via
:meth:`TestPlan.with_deselection`, which returns a new plan (the dataclass
is immutable). The executor consumes the plan rather than the manifest's
raw step list, and steps that are deselected do not run and produce no
``StepResult`` — they are not the same as ``SKIPPED``.

Eligibility semantics live in :mod:`conformance.results`; this module only
exposes the structural facts (which step ids were selected, which mandatory
step ids were deselected) so the eligibility computation can be driven from
the plan as well as from executed step outcomes.

Conditional-row semantics extend the mandatory/optional model: steps whose
``selectionMetadata.conditional`` flag is ``True`` are auto-selected only when
all of their ``requiredTestValueKeys`` resolve to non-empty values in the
effective test-value profile.  The resolved status (profile id, source, and
any per-step missing keys) is stored on each :class:`TestPlanEntry` so plan
preview and result evidence can explain *why* a conditional row was selected
or deselected without re-running the resolution logic.

Use :func:`build_plan_test_value_context` to derive the :class:`PlanTestValueContext`
from a manifest and participant :class:`~conformance.model_bank_config.TestValuesConfig`,
then pass it to :meth:`TestPlan.default_plan_from_manifest`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Literal

from conformance.manifest import Manifest, V1Step
from conformance.model_bank_config import TestDataConfig, TestValuesConfig

if TYPE_CHECKING:
    from conformance.run_configuration import RunConfiguration

TestValueProfileSource = Literal["default", "overridden"]
"""Source descriptor for the test-value profile resolved at plan build time.

``"default"`` means the manifest's ``defaultProfileId`` was used (no participant
override to a different profile, and no override keys were applied).
``"overridden"`` means the participant config selected a non-default profile,
applied override keys, or both.
"""


@dataclass(frozen=True)
class PlanTestValueContext:
    """Resolved test-value state needed to drive conditional plan selection.

    Computed once per plan build by :func:`build_plan_test_value_context` and
    passed to :meth:`TestPlan.default_plan_from_manifest` so conditional-row
    auto-selection can inspect which values are available without re-running
    the resolution logic inside the plan model.

    Attributes:
        effective_values: Immutable mapping of resolved key names to their
            effective string values for this run.  Empty when the manifest
            declares no ``testValueProfiles`` and no ``testValues``.
        profile_id: The effective profile identifier that was selected (either
            the manifest's ``defaultProfileId`` or the participant-chosen
            override).  ``None`` when the manifest has no ``testValueProfiles``.
        profile_source: Whether the effective profile is the manifest default
            or participant-overridden.  ``None`` when the manifest has no
            ``testValueProfiles``.  For manifests with a ``testValues`` block
            this is derived from :attr:`baseline_delta_keys` when a
            :class:`~conformance.run_configuration.RunConfiguration` is provided
            to :func:`build_plan_test_value_context`.
        override_keys: Frozenset of key names supplied by the participant via
            ``testValues.overrides`` in the config (legacy profiles) or the
            set of test-data value keys supplied by the participant (new
            baseline-delta path).  Empty when no overrides were applied.
        baseline_delta_keys: Frozenset of key names whose effective value
            differs from the suite manifest baseline.  Populated from
            :attr:`~conformance.run_configuration.RunConfiguration.baseline_delta_keys`
            when a compiled :class:`~conformance.run_configuration.RunConfiguration`
            is passed to :func:`build_plan_test_value_context`.  For callers
            that do not provide a ``RunConfiguration`` this is a best-effort
            approximation (the set of participant-supplied test-data keys) and
            may include same-as-baseline values before normalisation.
    """

    effective_values: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    profile_id: str | None = None
    profile_source: TestValueProfileSource | None = None
    override_keys: frozenset[str] = field(default_factory=frozenset)
    baseline_delta_keys: frozenset[str] = field(default_factory=frozenset)


def build_plan_test_value_context(
    manifest: Manifest,
    config_test_values: TestValuesConfig | None,
    config_test_data: TestDataConfig | None = None,
    run_configuration: RunConfiguration | None = None,
) -> PlanTestValueContext:
    """Derive the test-value context used for conditional plan auto-selection.

    Resolves the effective profile values from ``manifest.testValueProfiles``
    and the participant's ``testValues`` config section.  Any resolution error
    (unknown profile, disallowed override key) is re-raised so callers can
    surface it before building the plan.

    When ``run_configuration`` is provided and the manifest declares a
    ``testValues`` block, the context is derived directly from the compiled
    :class:`~conformance.run_configuration.RunConfiguration`: effective values
    are taken from
    :attr:`~conformance.run_configuration.RunConfiguration.effective_test_values`
    and ``baseline_delta_keys`` from
    :attr:`~conformance.run_configuration.RunConfiguration.baseline_delta_keys`.
    The ``profile_source`` is set to ``"overridden"`` when any baseline delta
    keys exist, or ``"default"`` otherwise (backward compat mapping).

    When the manifest declares no ``testValueProfiles`` and the config has no
    ``testValues`` section, returns an empty context with ``profile_id=None``
    and empty ``effective_values`` so old manifests continue to work without
    change.

    Args:
        manifest: Parsed manifest whose ``testValueProfiles`` metadata
            describes available profiles and allowed override keys, and/or
            whose ``testValues`` block declares baseline/generation metadata.
        config_test_values: Participant config test-value selection, or
            ``None`` when the config omits the ``testValues`` section.
        config_test_data: Participant config custom test-data mapping, or
            ``None`` when the config omits the ``testData`` section.
        run_configuration: Optional compiled run configuration from
            :func:`~conformance.run_configuration.compile_run_configuration`.
            When provided and the manifest has a ``testValues`` block, its
            effective values and baseline delta keys are used directly
            instead of re-resolving from config.

    Returns:
        A :class:`PlanTestValueContext` suitable for passing to
        :meth:`TestPlan.default_plan_from_manifest`.

    Raises:
        ValueError: If the participant config requests test values but the
            manifest declares no profiles, the selected profile id does not
            exist, or an override key is not allow-listed by the manifest.
    """
    # Defer import to avoid a package-level circular import; context.py imports
    # conformance.manifest which is already imported at module scope here.
    from conformance.context import build_runtime_test_values  # noqa: PLC0415 — deferred

    profile_spec = manifest.test_value_profiles
    manifest_test_values = manifest.test_values

    # Fast-path: RunConfiguration already compiled — use it directly for
    # manifests with a testValues block (new baseline-delta path).
    if run_configuration is not None and manifest_test_values is not None:
        delta_keys = run_configuration.baseline_delta_keys
        profile_source: TestValueProfileSource = "overridden" if delta_keys else "default"
        return PlanTestValueContext(
            effective_values=run_configuration.effective_test_values,
            profile_source=profile_source,
            override_keys=delta_keys,
            baseline_delta_keys=delta_keys,
        )

    if (
        profile_spec is None
        and manifest_test_values is None
        and config_test_values is None
        and config_test_data is None
    ):
        return PlanTestValueContext()

    effective_values = build_runtime_test_values(manifest, config_test_values, config_test_data)
    if profile_spec is None:
        # Manifest has testValues but no testValueProfiles.  The best-effort
        # baseline_delta_keys is the full set of participant-supplied test-data
        # keys (before same-as-baseline normalisation, which requires a compiled
        # RunConfiguration).  Conditional step selection only needs to know
        # whether each required key has *some* value, which this set provides.
        custom_data_keys = frozenset(config_test_data.values) if config_test_data is not None else frozenset()
        return PlanTestValueContext(
            effective_values=effective_values,
            override_keys=custom_data_keys,
            baseline_delta_keys=custom_data_keys,
        )

    # Determine which profile id was selected.
    if config_test_values is not None and config_test_values.profile is not None:
        profile_id = config_test_values.profile
    else:
        profile_id = profile_spec.default_profile_id

    # Determine source: overridden when a non-default profile was selected or
    # any override keys were applied.
    override_keys: frozenset[str] = (
        frozenset(config_test_values.overrides) if config_test_values is not None else frozenset()
    )
    is_default_profile = profile_id == profile_spec.default_profile_id
    derived_source: TestValueProfileSource = "default" if (is_default_profile and not override_keys) else "overridden"

    return PlanTestValueContext(
        effective_values=effective_values,
        profile_id=profile_id,
        profile_source=derived_source,
        override_keys=override_keys,
    )


@dataclass(frozen=True)
class TestPlanEntry:
    """A single row in a :class:`TestPlan`.

    Carries the mandatory/optional/selected flags from the manifest plus
    conditional-row status derived from the effective test-value profile at
    plan build time.  Fields that require test-value resolution default to
    ``False`` / ``None`` / empty tuple so existing manifests without
    ``testValueProfiles`` or ``selectionMetadata`` are unaffected.

    Attributes:
        step_id: Stable identifier of the manifest step this entry refers to.
        mandatory: Whether the underlying manifest step was declared
            ``mandatory``. Mirrored onto the entry so the plan is
            self-contained — callers do not need to keep the manifest in
            scope to ask "is this entry mandatory?".
        optional: Whether the underlying manifest step was declared
            ``optional``. Mutually exclusive with ``mandatory`` (enforced at
            manifest parse time).
        selected: Whether this entry will be executed when the plan runs.
            Default selection is mandatory ∪ non-optional; callers flip this
            to ``False`` via :meth:`TestPlan.with_deselection`.  Conditional
            steps start deselected when any ``required_test_value_keys`` are
            missing from the resolved profile.
        conditional: Whether the manifest step was declared conditional
            (``selectionMetadata.conditional: true``).  ``False`` for steps in
            old manifests and for unconditional steps.
        condition_id: Optional stable machine-readable identifier for the
            condition (e.g. ``"domestic-scheduled-payments-supported"``).
            ``None`` when the step is not conditional or omits the field.
        condition_label: Optional human-readable label for the condition,
            suitable for display in plan-preview badges.  ``None`` when the
            step is not conditional or omits the field.
        required_test_value_keys: Tuple of test-value key names declared by
            the step's ``selectionMetadata.requiredTestValueKeys``.  Empty for
            unconditional steps and manifests without profiles.
        missing_test_value_keys: Subset of ``required_test_value_keys`` whose
            values were absent from the effective profile at plan build time.
            Non-empty means the step was deselected because of missing values.
        test_value_profile_id: The profile id that was effective at plan build
            time (manifest default or participant-chosen).  ``None`` when the
            manifest has no ``testValueProfiles``.
        test_value_profile_source: Whether the effective profile was the
            manifest default or participant-overridden.  ``None`` when the
            manifest has no ``testValueProfiles``.
        test_value_override_keys: Tuple of key names from the participant's
            ``testValues.overrides`` that were applied to the effective values.
            Empty when no overrides were applied or the manifest has no profiles.
        consumed_test_value_keys: Frozen set of test-value key names consumed
            by this step via ``${testValues.<key>}`` placeholders.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    step_id: str
    mandatory: bool
    optional: bool
    selected: bool
    conditional: bool = False
    condition_id: str | None = None
    condition_label: str | None = None
    required_test_value_keys: tuple[str, ...] = ()
    missing_test_value_keys: tuple[str, ...] = ()
    test_value_profile_id: str | None = None
    test_value_profile_source: TestValueProfileSource | None = None
    test_value_override_keys: tuple[str, ...] = ()
    consumed_test_value_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class TestPlan:
    """Ordered, immutable plan of which manifest steps to execute.

    Holds one :class:`TestPlanEntry` per manifest step, in manifest order.
    Construct via :meth:`default_plan_from_manifest`, then narrow via
    :meth:`with_deselection`. The dataclass is frozen — every mutation
    returns a new plan, so plans are safe to pass across thread boundaries
    and to embed in cached configuration objects.

    Attributes:
        entries: Tuple of :class:`TestPlanEntry` rows in manifest order.
            Tuple (not list) so the plan is structurally immutable end to
            end — combined with ``frozen=True`` this lets plans be hashed
            and shared across threads without copying.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    entries: tuple[TestPlanEntry, ...]

    @classmethod
    def default_plan_from_manifest(
        cls,
        manifest: Manifest,
        *,
        test_value_context: PlanTestValueContext | None = None,
    ) -> TestPlan:
        """Build the default plan for ``manifest``: every mandatory + non-optional step selected.

        Steps declared ``optional: true`` are present in the plan but start
        deselected — participants opt into them deliberately. Steps without
        an explicit ``optional`` flag are treated as part of the default
        coverage and are selected. Mandatory steps are always selected by
        default; deselecting them is a deliberate caller action via
        :meth:`with_deselection`.

        Conditional steps (``selectionMetadata.conditional: true``) are
        auto-selected when *all* of their ``requiredTestValueKeys`` resolve
        to non-empty values in ``test_value_context.effective_values``.  When
        any required key is absent the step starts deselected and the missing
        keys are recorded on the entry for plan-preview evidence.  When
        ``test_value_context`` is omitted, conditional steps behave as if all
        required values are missing (i.e. deselected by default), which
        preserves backward compatibility for callers that do not yet pass
        profile context.

        v0 manifests are not currently surfaced through the TestPlan model:
        v0 tests run via the legacy code path and have no concept of
        mandatory/optional. For a v0 manifest this returns an empty plan,
        which the executor interprets as "fall back to the v0 behaviour".

        Args:
            manifest: Parsed manifest to derive the default plan from.
            test_value_context: Optional resolved test-value profile context.
                When provided, conditional steps are auto-selected when their
                required values are present in the effective profile.  Build
                this from :func:`build_plan_test_value_context` before
                calling.

        Returns:
            A new :class:`TestPlan` with one entry per v1 step, pre-selected
            according to the mandatory/optional and conditional defaults
            described above.
        """
        if manifest.schema_version != "v1":
            return cls(entries=())

        ctx = test_value_context or PlanTestValueContext()
        entries = tuple(_build_entry_from_step(step, ctx) for step in manifest.steps)
        return cls(entries=entries)

    def with_deselection(self, step_ids: Iterable[str]) -> TestPlan:
        """Return a new plan with the given step ids marked as not selected.

        Idempotent: deselecting a step that is already deselected is a
        no-op. Mandatory steps may be deselected via this method — the
        results layer surfaces the deselection in
        ``certificationEligibility`` so the run is correctly flagged as
        ineligible for certification.

        Args:
            step_ids: Iterable of step ids to mark as not selected. Order
                does not matter; duplicates are tolerated.

        Returns:
            A new :class:`TestPlan` with the requested entries deselected.
            All other entries (including their ``mandatory``/``optional``
            flags) are preserved.

        Raises:
            ValueError: If any provided id does not match an entry in this
                plan. Fails fast so callers see misspelled ids rather than
                silently running the original plan.
        """
        deselect_set = set(step_ids)
        known_ids = {entry.step_id for entry in self.entries}
        unknown = deselect_set - known_ids
        if unknown:
            unknown_list = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown step id(s) in deselection: {unknown_list}")
        new_entries = tuple(
            TestPlanEntry(
                step_id=entry.step_id,
                mandatory=entry.mandatory,
                optional=entry.optional,
                selected=False if entry.step_id in deselect_set else entry.selected,
                # Preserve conditional metadata fields unchanged — deselection
                # is a caller-driven action and does not alter the condition.
                conditional=entry.conditional,
                condition_id=entry.condition_id,
                condition_label=entry.condition_label,
                required_test_value_keys=entry.required_test_value_keys,
                missing_test_value_keys=entry.missing_test_value_keys,
                test_value_profile_id=entry.test_value_profile_id,
                test_value_profile_source=entry.test_value_profile_source,
                test_value_override_keys=entry.test_value_override_keys,
                consumed_test_value_keys=entry.consumed_test_value_keys,
            )
            for entry in self.entries
        )
        return TestPlan(entries=new_entries)

    def selected_step_ids(self) -> list[str]:
        """Return the ids of every selected entry, in manifest order.

        Returns:
            Ordered list of step ids the executor should run. May be empty
            (every entry deselected, or an empty plan).
        """
        return [entry.step_id for entry in self.entries if entry.selected]

    def deselected_step_ids(self) -> list[str]:
        """Return the ids of every deselected entry, in manifest order.

        Returns:
            Ordered list of step ids that will not be executed and will not
            produce a ``StepResult``.
        """
        return [entry.step_id for entry in self.entries if not entry.selected]

    def deselected_mandatory_step_ids(self) -> list[str]:
        """Return the ids of mandatory entries that have been deselected.

        Used by :mod:`conformance.results` to flip ``certificationEligibility``
        to ineligible with the ``"Mandatory steps were deselected from the
        plan"`` reason.

        Returns:
            Ordered list of step ids that were declared mandatory in the
            manifest but were deselected from the plan. Empty when no
            mandatory step has been deselected.
        """
        return [entry.step_id for entry in self.entries if entry.mandatory and not entry.selected]

    def is_eligible_by_selection(self) -> bool:
        """Return whether the *plan shape* permits certification eligibility.

        This is a structural check only: it does not consider step outcomes.
        A plan is eligible by selection when at least one mandatory step is
        selected *and* no mandatory step has been deselected. The full
        eligibility decision (which also requires every mandatory step to
        finish ``passed`` or ``warn``) lives in :mod:`conformance.results`.

        Returns:
            True when the plan includes at least one mandatory step and
            does not deselect any mandatory step; False otherwise.
        """
        mandatory_entries = [entry for entry in self.entries if entry.mandatory]
        if not mandatory_entries:
            return False
        return all(entry.selected for entry in mandatory_entries)

    def summary(self) -> dict[str, int]:
        """Return aggregate counts for the report's top-level ``plan`` block.

        Extends the base mandatory/optional counts with conditional-row
        counters so plan-preview UI and result evidence can report how many
        conditional steps were auto-selected, how many were skipped because of
        missing values, and how many have all required values resolved.

        Returns:
            Dict with ``totalSteps``, ``selectedSteps``, ``deselectedSteps``,
            ``mandatorySelected``, ``mandatoryDeselected``,
            ``conditionalSelected``, ``conditionalDeselectedMissingValues``.
            Keys are stable and consumed by
            :func:`conformance.results.build_smoke_check_result`.
        """
        total = len(self.entries)
        selected = sum(1 for entry in self.entries if entry.selected)
        mandatory_selected = sum(1 for entry in self.entries if entry.mandatory and entry.selected)
        mandatory_deselected = sum(1 for entry in self.entries if entry.mandatory and not entry.selected)
        conditional_selected = sum(1 for entry in self.entries if entry.conditional and entry.selected)
        conditional_deselected_missing = sum(
            1 for entry in self.entries if entry.conditional and not entry.selected and entry.missing_test_value_keys
        )
        return {
            "totalSteps": total,
            "selectedSteps": selected,
            "deselectedSteps": total - selected,
            "mandatorySelected": mandatory_selected,
            "mandatoryDeselected": mandatory_deselected,
            "conditionalSelected": conditional_selected,
            "conditionalDeselectedMissingValues": conditional_deselected_missing,
        }


def _build_entry_from_step(step: V1Step, ctx: PlanTestValueContext) -> TestPlanEntry:
    """Build one :class:`TestPlanEntry` from a v1 manifest step and resolved test-value context.

    Computes conditional-row auto-selection by checking whether every key in
    ``step.selection_metadata.required_test_value_keys`` is present in
    ``ctx.effective_values``.  When all keys resolve, the step is selected;
    when any key is missing, the step is deselected and the missing keys are
    recorded on the entry.

    For unconditional steps the auto-selection logic follows the existing
    mandatory/optional semantics: mandatory → always selected; optional →
    always deselected by default; neither flag → selected by default.

    Args:
        step: Parsed v1 manifest step (HTTP or PSU authorisation).
        ctx: Resolved test-value context containing the effective profile
            values, profile id, source, and applied override keys.

    Returns:
        A populated :class:`TestPlanEntry` for the step.
    """
    meta = step.selection_metadata

    if meta is None or not meta.conditional:
        # Non-conditional: use the existing mandatory/optional selection rule.
        return TestPlanEntry(
            step_id=step.id,
            mandatory=step.mandatory,
            optional=step.optional,
            selected=step.mandatory or not step.optional,
            # Carry selection metadata fields even for non-conditional steps
            # so that plan-preview tooling can display condition labels.
            conditional=False,
            condition_id=meta.condition_id if meta is not None else None,
            condition_label=meta.condition_label if meta is not None else None,
            required_test_value_keys=meta.required_test_value_keys if meta is not None else (),
            missing_test_value_keys=(),
            test_value_profile_id=ctx.profile_id,
            test_value_profile_source=ctx.profile_source,
            test_value_override_keys=tuple(sorted(ctx.override_keys)),
            consumed_test_value_keys=step.consumed_test_value_keys,
        )

    # Conditional step: auto-select only when all required keys are present.
    required_keys = meta.required_test_value_keys
    missing_keys = tuple(key for key in required_keys if not ctx.effective_values.get(key))
    # A conditional step should never be mandatory; treat it as non-optional
    # (i.e. included in the default scope) when its required values are present.
    # When values are missing, start deselected so the participant must
    # explicitly opt in after configuring the required test-value overrides.
    selected = len(missing_keys) == 0

    return TestPlanEntry(
        step_id=step.id,
        mandatory=step.mandatory,
        optional=step.optional,
        selected=selected,
        conditional=True,
        condition_id=meta.condition_id,
        condition_label=meta.condition_label,
        required_test_value_keys=required_keys,
        missing_test_value_keys=missing_keys,
        test_value_profile_id=ctx.profile_id,
        test_value_profile_source=ctx.profile_source,
        test_value_override_keys=tuple(sorted(ctx.override_keys)),
        consumed_test_value_keys=step.consumed_test_value_keys,
    )
