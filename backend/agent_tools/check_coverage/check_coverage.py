"""
`check_coverage` as an agent tool.

Asked about one requirement before promising anything about it: is help actually coming,
how much is genuinely still missing, and does it all depend on one person.

Note:
    The question this exists to prevent is a coordinator being told a shelter is covered
    because three proposals point at it, when all three are carried by the same van. The
    count looks like redundancy and is not.
"""

import networkx as nx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.network import DIRECT_REACH_KM, Network
from agent_tools.shared import ToolInputError, get_requirement
from ayudagente.radar.choices import MatchStatus
from ayudagente.radar.models import Match


class CheckCoverageInput(BaseModel):
    """Arguments for `check_coverage`."""

    event_id: int = Field(description="The emergency this conversation is bound to.")
    requirement_id: int = Field(
        description="The need or offer to inspect, as returned in `requirement_id`."
    )


def _party(requirement) -> dict:
    return {
        "actor_id": requirement.actor_id,
        "actor": requirement.actor.canonical_name,
        "place": (
            requirement.location.admin_unit.name
            if requirement.location.admin_unit
            else requirement.location.raw_text
        ),
    }


@tool("check_coverage", args_schema=CheckCoverageInput)
def check_coverage(event_id: int, requirement_id: int) -> dict:
    """
    Say whether one need is really being handled, and what the help depends on.

    Call this before telling anyone something is covered, and when choosing which of
    several open needs is most exposed.

    `still_needed` subtracts everything already promised, so it is the number to quote. A
    null means nobody ever stated an amount.

    `contributors` lists who is bringing what and at what stage. `depends_on` names actors
    every single delivery has to pass through — usually a carrier. When it is not empty,
    the coverage is only as good as those actors: two proposals through one van are one
    van, not two chances.

    `isolated` true means nothing at all connects this requirement to anyone. It is not
    unserved, it is cut off, and that needs a different response — finding supply rather
    than routing it.
    """
    try:
        requirement = get_requirement(
            requirement_id, event_id, "actor", "resource", "location__admin_unit", "event"
        )
    except ToolInputError as exc:
        return exc.payload

    network = Network(requirement.event_id)
    matches = (
        Match.objects.filter(need_id=requirement.id)
        .exclude(status__in=(MatchStatus.DISCARDED, MatchStatus.FAILED))
        .select_related("offer__actor", "offer__location__admin_unit", "via_transport__actor")
    )

    contributors = []
    carrier_sets = []
    for match in matches:
        carriers = []
        if match.via_transport is not None:
            carriers = [
                {
                    "actor_id": match.via_transport.actor_id,
                    "actor": match.via_transport.actor.canonical_name,
                }
            ]
        carrier_sets.append(tuple(c["actor_id"] for c in carriers))
        contributors.append(
            {
                "from": _party(match.offer),
                "quantity": float(match.committed_quantity)
                if match.committed_quantity is not None
                else None,
                "status": match.status,
                "distance_km": match.distance_km,
                "through": carriers,
            }
        )

    # Every route crossing the same actors means those actors are the delivery
    depends_on = []
    if carrier_sets and all(s and s == carrier_sets[0] for s in carrier_sets):
        depends_on = contributors[0]["through"]

    actor_id = requirement.actor_id
    isolated = actor_id not in network.graph or nx.degree(network.graph, actor_id) == 0

    nearby_carriers = (
        len(network.carriers_near(requirement.location.point, DIRECT_REACH_KM))
        if isolated
        else None
    )

    return {
        "requirement_id": requirement.id,
        "resource_key": requirement.resource.key,
        "direction": requirement.direction,
        "urgency": requirement.urgency,
        "unit": requirement.unit or None,
        "asked_for": float(requirement.quantity) if requirement.quantity is not None else None,
        "still_needed": network.still_needed(requirement),
        "contributors": contributors,
        "depends_on": depends_on,
        "isolated": isolated,
        "carriers_in_reach": nearby_carriers,
        "reachable_by_us": actor_id in network.contactable(),
    }
