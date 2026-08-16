"""
`propose_match` as an agent tool.

A write tool, so every guard the batch pass applies up front is re-checked here: the pass
filters its candidates, but a tool call arrives with two arbitrary ids. All of that lives
in the service — this wrapper only translates ids and turns the refusals into text the
agent can learn from.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import failure
from ayudagente.radar.models import Requirement
from ayudagente.radar.services import propose_match as propose_match_service

RATIONALE_MIN_CHARS = 20


class ProposeMatchInput(BaseModel):
    """Arguments for `propose_match`."""

    event_id: int = Field(description="The emergency this conversation is bound to.")
    need_id: int = Field(description="The requirement with direction 'needs'.")
    offer_id: int = Field(description="The requirement with direction 'offers'.")
    rationale: str = Field(
        description=(
            "Why this pairing, in Spanish, in one or two sentences. A coordinator reads it "
            "to decide whether to act, so name the resource, the distance and the urgency."
        )
    )
    via_transport_id: int | None = Field(
        default=None,
        description=(
            "A transport offer that carries the goods, when the two sides are too far "
            "apart to meet directly. Must itself be an offer of transport."
        ),
    )


@tool("propose_match", args_schema=ProposeMatchInput)
def propose_match(
    event_id: int,
    need_id: int,
    offer_id: int,
    rationale: str,
    via_transport_id: int | None = None,
) -> dict:
    """
    Propose pairing one need with one offer, for a human to review.

    This writes a proposal; it does not deliver anything and does not contact anyone. A
    proposal a human has already acted on is never overwritten — if the pairing has moved
    past `proposed`, the reply says so and nothing changes.

    Both sides must belong to this conversation's emergency and be located precisely
    enough to deliver to; a
    location covering a whole region is refused. Beyond about 30 km the pair needs
    `via_transport_id`, an offer of transport that can bridge them.

    `rationale` is mandatory and is shown to the coordinator. Write it for them, not for the
    log.
    """
    if len(rationale.strip()) < RATIONALE_MIN_CHARS:
        return failure(
            "rationale is too short to be useful to a coordinator",
            "say what is being moved, how far, and why it is urgent",
        )

    wanted = {need_id, offer_id} | ({via_transport_id} if via_transport_id else set())
    found = {
        r.id: r
        for r in Requirement.objects.select_related(
            "location", "actor", "resource", "destination", "event"
        ).filter(id__in=wanted)
    }
    missing = sorted(wanted - found.keys())
    if missing:
        return failure(f"requirements not found: {missing}")

    foreign = sorted(r.id for r in found.values() if r.event_id != event_id)
    if foreign:
        return failure(
            f"requirements from another emergency: {foreign}",
            "pair only rows from the event this conversation is bound to",
        )

    try:
        match = propose_match_service(
            need=found[need_id],
            offer=found[offer_id],
            via_transport=found[via_transport_id] if via_transport_id else None,
            rationale=rationale.strip(),
        )
    except ValueError as exc:
        return failure(str(exc))

    if match is None:
        return failure(
            "this pairing is already past `proposed` and a human is handling it",
            "leave it alone and propose a different pairing",
        )

    return {
        "match_id": match.id,
        "status": match.status,
        "need_id": match.need_id,
        "offer_id": match.offer_id,
        "via_transport_id": match.via_transport_id,
        "committed_quantity": (
            float(match.committed_quantity) if match.committed_quantity is not None else None
        ),
        "distance_km": match.distance_km,
        "score": round(match.score, 2),
    }
