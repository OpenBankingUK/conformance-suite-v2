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
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from conformance.manifest import Manifest


@dataclass(frozen=True)
class TestPlanEntry:
    """A single row in a :class:`TestPlan`.

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
            to ``False`` via :meth:`TestPlan.with_deselection`.
    """

    # Class starts with "Test" but is production code, not a pytest collection target.
    __test__: ClassVar[bool] = False

    step_id: str
    mandatory: bool
    optional: bool
    selected: bool


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
    def default_plan_from_manifest(cls, manifest: Manifest) -> TestPlan:
        """Build the default plan for ``manifest``: every mandatory + non-optional step selected.

        Steps declared ``optional: true`` are present in the plan but start
        deselected — participants opt into them deliberately. Steps without
        an explicit ``optional`` flag are treated as part of the default
        coverage and are selected. Mandatory steps are always selected by
        default; deselecting them is a deliberate caller action via
        :meth:`with_deselection`.

        v0 manifests are not currently surfaced through the TestPlan model:
        v0 tests run via the legacy code path and have no concept of
        mandatory/optional. For a v0 manifest this returns an empty plan,
        which the executor interprets as "fall back to the v0 behaviour".

        Args:
            manifest: Parsed manifest to derive the default plan from.

        Returns:
            A new :class:`TestPlan` with one entry per v1 step, pre-selected
            according to the mandatory/optional defaults described above.
        """
        if manifest.schema_version != "v1":
            return cls(entries=())
        entries = tuple(
            TestPlanEntry(
                step_id=step.id,
                mandatory=step.mandatory,
                optional=step.optional,
                # Mandatory always selected; non-optional selected by default;
                # optional deselected by default. (mandatory and optional are
                # mutually exclusive at parse time so the precedence here is
                # unambiguous.)
                selected=step.mandatory or not step.optional,
            )
            for step in manifest.steps
        )
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

        Returns:
            Dict with ``totalSteps``, ``selectedSteps``, ``deselectedSteps``,
            ``mandatorySelected``, ``mandatoryDeselected``. Keys are stable
            and consumed by :func:`conformance.results.build_smoke_check_result`.
        """
        total = len(self.entries)
        selected = sum(1 for entry in self.entries if entry.selected)
        mandatory_selected = sum(1 for entry in self.entries if entry.mandatory and entry.selected)
        mandatory_deselected = sum(1 for entry in self.entries if entry.mandatory and not entry.selected)
        return {
            "totalSteps": total,
            "selectedSteps": selected,
            "deselectedSteps": total - selected,
            "mandatorySelected": mandatory_selected,
            "mandatoryDeselected": mandatory_deselected,
        }
