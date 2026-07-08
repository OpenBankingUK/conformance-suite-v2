"""DCR scenario definitions and applicability helpers.

Each :class:`DcrScenario` encodes one conformance scenario — its stable ID,
human-readable description, corresponding catalogue entry, and whether it is
a negative-path test.

The :func:`applicable_scenarios` function filters the full scenario list
based on discovery metadata and the optional ``advertised_operations``
parameter, so the runner only attempts scenarios that the ASPSP claims to
support.

Scenario IDs (for auditability):

- ``DCR-001`` — Registration with valid SSA and valid claims → expect 201
- ``DCR-002`` — Retrieve registered client → GET /register/{clientId}
- ``DCR-003`` — Update registered client → PUT /register/{clientId}
- ``DCR-004`` — Delete registered client → DELETE /register/{clientId}
- ``DCR-005`` — Registration with expired SSA → expect 4xx
- ``DCR-007`` — Registration with invalid issuer → expect 4xx
- ``DCR-008`` — Registration with invalid token endpoint auth method → expect 4xx
- ``DCR-009`` — Registration with wrong response type → expect 4xx
- ``DCR-010`` — Access using deleted client ID → expect 4xx
- ``DCR-011`` — Access using wrong client ID → expect 4xx
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DcrScenario:
    """Definition of one DCR conformance scenario.

    Attributes:
        scenario_id: Stable scenario identifier (e.g. ``"DCR-001"``).
        description: Human-readable scenario description.
        catalogue_entry_id: The catalogue ``endpointId`` this scenario
            corresponds to.
        is_negative: ``True`` for negative-path scenarios that expect 4xx
            responses; ``False`` for positive-path scenarios.
        requires_delete: ``True`` when the scenario can only run after a
            successful DELETE (DCR-010).
        requires_get: ``True`` when the scenario requires GET /register
            to be advertised (DCR-002).
        requires_put: ``True`` when the scenario requires PUT /register
            to be advertised (DCR-003).
        requires_delete_advertised: ``True`` when the scenario requires
            DELETE /register to be advertised (DCR-004 and DCR-010).
    """

    scenario_id: str
    description: str
    catalogue_entry_id: str
    is_negative: bool = False
    requires_delete: bool = False
    requires_get: bool = False
    requires_put: bool = False
    requires_delete_advertised: bool = False


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

ALL_SCENARIOS: Final[tuple[DcrScenario, ...]] = (
    DcrScenario(
        scenario_id="DCR-001",
        description="Registration with valid SSA and valid claims",
        catalogue_entry_id="dcr.register.post",
        is_negative=False,
    ),
    DcrScenario(
        scenario_id="DCR-002",
        description="Retrieve registered client (GET /register/{clientId})",
        catalogue_entry_id="dcr.register.get",
        is_negative=False,
        requires_get=True,
    ),
    DcrScenario(
        scenario_id="DCR-003",
        description="Update registered client (PUT /register/{clientId})",
        catalogue_entry_id="dcr.register.put",
        is_negative=False,
        requires_put=True,
    ),
    DcrScenario(
        scenario_id="DCR-004",
        description="Delete registered client (DELETE /register/{clientId})",
        catalogue_entry_id="dcr.register.delete",
        is_negative=False,
        requires_delete_advertised=True,
    ),
    DcrScenario(
        scenario_id="DCR-005",
        description="Registration with expired SSA",
        catalogue_entry_id="dcr.negative.expired-ssa",
        is_negative=True,
    ),
    DcrScenario(
        scenario_id="DCR-007",
        description="Registration with invalid issuer",
        catalogue_entry_id="dcr.negative.invalid-issuer",
        is_negative=True,
    ),
    DcrScenario(
        scenario_id="DCR-008",
        description="Registration with invalid token endpoint auth method",
        catalogue_entry_id="dcr.negative.invalid-auth-method",
        is_negative=True,
    ),
    DcrScenario(
        scenario_id="DCR-009",
        description="Registration with wrong response type",
        catalogue_entry_id="dcr.negative.wrong-response-type",
        is_negative=True,
    ),
    DcrScenario(
        scenario_id="DCR-010",
        description="Access using deleted client ID",
        catalogue_entry_id="dcr.negative.deleted-client-access",
        is_negative=True,
        requires_delete=True,
        requires_delete_advertised=True,
    ),
    DcrScenario(
        scenario_id="DCR-011",
        description="Access using wrong client ID",
        catalogue_entry_id="dcr.negative.wrong-client-id",
        is_negative=True,
    ),
)
"""All DCR conformance scenarios in execution order.

Scenarios that require optional operations (GET, PUT, DELETE) are filtered
by :func:`applicable_scenarios` based on discovery metadata.
"""


# ---------------------------------------------------------------------------
# Applicability filtering
# ---------------------------------------------------------------------------


def applicable_scenarios(
    *,
    advertise_get: bool = True,
    advertise_put: bool = True,
    advertise_delete: bool = True,
    delete_succeeded: bool = False,
) -> tuple[DcrScenario, ...]:
    """Return the subset of scenarios applicable for a given run configuration.

    Scenarios requiring optional operations (GET, PUT, DELETE) are included
    only when the corresponding ``advertise_*`` flag is ``True``.  DCR-010
    additionally requires that a DELETE run succeeded (``delete_succeeded``).

    Args:
        advertise_get: ``True`` when GET /register/{clientId} should be run.
        advertise_put: ``True`` when PUT /register/{clientId} should be run.
        advertise_delete: ``True`` when DELETE /register/{clientId} should be
            run.
        delete_succeeded: ``True`` when a DELETE has completed successfully,
            enabling DCR-010.

    Returns:
        Tuple of applicable :class:`DcrScenario` instances in execution order.
    """
    result: list[DcrScenario] = []
    for scenario in ALL_SCENARIOS:
        if scenario.requires_get and not advertise_get:
            continue
        if scenario.requires_put and not advertise_put:
            continue
        if scenario.requires_delete_advertised and not advertise_delete:
            continue
        if scenario.requires_delete and not delete_succeeded:
            continue
        result.append(scenario)
    return tuple(result)


def scenario_by_id(scenario_id: str) -> DcrScenario | None:
    """Look up a scenario definition by its stable ID.

    Args:
        scenario_id: Scenario identifier (e.g. ``"DCR-001"``).

    Returns:
        The matching :class:`DcrScenario`, or ``None`` when not found.
    """
    for scenario in ALL_SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    return None
