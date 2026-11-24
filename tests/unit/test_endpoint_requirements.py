"""Specification endpoint requirements and parity with the reviewed evidence matrix."""

import re
from collections import Counter
from pathlib import Path

import pytest

from conformance.catalogue import EndpointRef
from conformance.endpoint_requirements import (
    read_write_endpoint_requirement,
    read_write_endpoint_requirements,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("version", "optional_count"),
    [("4.0.1", 24), ("4.0.0", 24), ("3.1.11", 22)],
)
def test_requirement_inventory_and_documentation_match(version: str, optional_count: int) -> None:
    requirements = read_write_endpoint_requirements(version)
    assert Counter(r.kind for r in requirements) == {
        "M": 20,
        "C": 29,
        "O": optional_count,
        "MP": 15,
        "MD": 1,
        "CN": 4,
    }
    assert len({r.endpoint for r in requirements}) == len(requirements)
    matrix = (Path(__file__).parents[2] / "docs/READ_WRITE_ENDPOINT_REQUIREMENTS.md").read_text()
    refs = dict(re.findall(r"^\[([^]]+)\]: (\S+)$", matrix, re.MULTILINE))
    column = {"4.0.1": 1, "4.0.0": 2, "3.1.11": 3}[version]
    documented: dict[str, tuple[str, str]] = {}
    for line in matrix.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        match = re.fullmatch(r"\[(M|C|O|MP|MD|CN)\]\[([^]]+)\]", cells[column])
        if match is not None:
            documented[cells[0].strip("`")] = (match[1], refs[match[2]])
    assert documented == {f"{r.endpoint.method} {r.endpoint.path}": (r.kind, r.source_url) for r in requirements}


@pytest.mark.parametrize("version", ["4.0.1", "4.0.0", "3.1.11"])
def test_post_dependencies_are_explicit_and_versioned(version: str) -> None:
    requirement = read_write_endpoint_requirement(
        version,
        EndpointRef(method="GET", path="/open-banking/v4.0/pisp/international-payments/{internationalPaymentId}"),
    )
    assert requirement is not None
    assert requirement.kind == "MP"
    assert requirement.prerequisite == EndpointRef(method="POST", path="/international-payments")
    assert requirement.condition == "Required when POST /international-payments is implemented."


def test_specific_conditions_disputes_and_endpoint_owner_are_preserved() -> None:
    requirements = {r.endpoint: r for r in read_write_endpoint_requirements("4.0.1")}
    immediate = requirements[
        EndpointRef(method="GET", path="/international-scheduled-payment-consents/{ConsentId}/funds-confirmation")
    ]
    assert immediate.kind == "MD"
    assert immediate.prerequisite is None
    assert "immediate debit" in immediate.condition
    assert immediate.disputed
    event = requirements[EndpointRef(method="DELETE", path="/event-subscriptions/{EventSubscriptionId}")]
    assert event.kind == "CN"
    assert "single event type" in event.condition
    assert event.disputed
    assert requirements[EndpointRef(method="POST", path="/event-notifications")].host == "TPP"
    assert requirements[EndpointRef(method="GET", path="/domestic-vrps/{DomesticVRPId}")].kind == "C"
    assert requirements[EndpointRef(method="GET", path="/domestic-vrps/{DomesticVRPId}")].prerequisite is None


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_migration_operations_are_not_inherited_by_v311(method: str) -> None:
    endpoints = {(str(r.endpoint.method), r.endpoint.path): r for r in read_write_endpoint_requirements("4.0.0")}
    assert endpoints[(method, "/domestic-vrp-consents/{ConsentId}")].kind == "O"
    assert (method, "/domestic-vrp-consents/{ConsentId}") not in {
        (r.endpoint.method, r.endpoint.path) for r in read_write_endpoint_requirements("3.1.11")
    }


@pytest.mark.parametrize(
    "path",
    ["/accounts/{AccountId}/beneficiaries/foobar", "/Accounts", "/token"],
)
def test_unclassified_paths_are_not_guessed_to_be_optional(path: str) -> None:
    assert read_write_endpoint_requirement("4.0.1", EndpointRef(method="GET", path=path)) is None


def test_unreviewed_versions_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="No reviewed endpoint requirements"):
        read_write_endpoint_requirements("5.0")
