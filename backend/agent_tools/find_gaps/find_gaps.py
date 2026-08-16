"""
`find_gaps` as an agent tool.

What nobody is handling, which is the question a coordinator cannot answer from a listing.
A table of open requirements says what was asked for; this says what is still asked for
after everything already promised, and separates the two reasons something goes unserved.

Note:
    "Nobody has brought it" and "nobody could bring it" look identical in a list and need
    opposite responses — redistribute versus go out and find supply. Splitting them is most
    of this tool's value.
"""

import networkx as nx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.network import URGENCY_RANK, Network
from agent_tools.shared import ToolInputError, get_event, resolve_place_arg
from ayudagente.radar.choices import Direction

# Above anything one event produces, so a bucket is whole unless the totals say otherwise
TOP_N = 300


class FindGapsInput(BaseModel):
    """Arguments for `find_gaps`."""

    event_id: int = Field(description="The emergency to survey.")
    place: str | None = Field(
        default=None, description="Narrow to one administrative area, by name or code."
    )


@tool("find_gaps", args_schema=FindGapsInput)
def find_gaps(event_id: int, place: str | None = None) -> dict:
    """
    List what is going unattended, and why, most urgent first.

    Use it for "what are we missing", "where should we push", "what has nobody picked up" —
    the standing question between specific requests.

    `unattended` are needs with nothing committed to them at all. `partially_covered` have
    help coming but not enough. Both are ranked by urgency.

    `cut_off` is the serious one: needs connected to nobody, where the problem is not that
    help is slow but that no supply is linked to them at all. `no_supply_anywhere` means
    the resource has no offer in the whole event — going out to find some is the only move,
    and that is a different instruction than reallocating.

    `unreachable_actors` counts the ones we have no contact detail for. Their needs cannot
    be acted on however much supply exists, which usually makes finding a phone number the
    highest-value thing to do.
    """
    try:
        event = get_event(event_id)
        admin_unit = resolve_place_arg(place, event.country_code) if place else None
    except ToolInputError as exc:
        return {**exc.payload, "unattended": []}

    network = Network(event_id)

    needs = [r for r in network.requirements.values() if r.direction == Direction.NEEDS]
    offers = [r for r in network.requirements.values() if r.direction == Direction.OFFERS]
    if admin_unit is not None:
        allowed = {admin_unit.id} | set(
            admin_unit.children.values_list("id", flat=True)  # type: ignore[attr-defined]
        )
        needs = [r for r in needs if r.location.admin_unit_id in allowed]

    offered_resources = {r.resource_id for r in offers}

    def describe(requirement) -> dict:
        return {
            "requirement_id": requirement.id,
            "actor": requirement.actor.canonical_name,
            "actor_id": requirement.actor_id,
            "resource_key": requirement.resource.key,
            "urgency": requirement.urgency,
            "still_needed": network.still_needed(requirement),
            "unit": requirement.unit or None,
            "municipality": (
                requirement.location.admin_unit.name if requirement.location.admin_unit else None
            ),
            "reachable_by_us": requirement.actor_id in network.contactable(),
        }

    unattended, partial, cut_off, no_supply = [], [], [], []
    for requirement in needs:
        committed = network.connected.get(requirement.id, 0)
        still = network.still_needed(requirement)
        row = describe(requirement)

        if requirement.resource_id not in offered_resources:
            no_supply.append(row)
        elif committed == 0:
            unattended.append(row)
        elif still is not None and still > 0:
            partial.append(row)

        actor_id = requirement.actor_id
        if actor_id in network.graph and nx.degree(network.graph, actor_id) == 0:
            cut_off.append(row)

    for bucket in (unattended, partial, cut_off, no_supply):
        bucket.sort(key=lambda r: URGENCY_RANK.get(r["urgency"], 9))

    unreachable = sum(1 for r in needs if r.actor_id not in network.contactable())

    return {
        "needs_total": len(needs),
        "unattended_total": len(unattended),
        "unattended": unattended[:TOP_N],
        "partially_covered": partial[:TOP_N],
        "cut_off": cut_off[:TOP_N],
        "no_supply_anywhere": no_supply[:TOP_N],
        "totals": {
            "partially_covered": len(partial),
            "cut_off": len(cut_off),
            "no_supply_anywhere": len(no_supply),
        },
        "truncated": max(len(partial), len(cut_off), len(no_supply), len(unattended)) > TOP_N,
        "unreachable_actors": unreachable,
    }
