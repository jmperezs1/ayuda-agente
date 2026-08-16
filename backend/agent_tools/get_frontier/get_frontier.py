"""
`get_frontier` as an agent tool.

The only read the frontier agent ever performs. A few dozen rows, a couple of thousand
tokens, and not a single post among them — the agent decides where to look, never what a
post said.

Note:
    `is_unexplored` is sent explicitly rather than left to be inferred from `passes == 0`.
    A fixed share of every round has to go to targets with no history, and the agent cannot
    honour a rule whose input it has to derive.

    So is `job_in_flight`. A harvest takes minutes, so a round firing while the last one is
    still running sees a scoreboard where nothing has moved — same yields, same staleness —
    and queues the same targets again. The counters cannot show that; only the job can.
"""

from django.utils import timezone
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import ToolInputError, get_event
from ayudagente.radar.models import HarvestJob
from ayudagente.radar.services import get_frontier as get_frontier_service
from ayudagente.radar.services.frontier import IN_FLIGHT_STATUSES

MAX_NODES = 300


class GetFrontierInput(BaseModel):
    """Arguments for `get_frontier`."""

    event_id: int = Field(description="Event whose search frontier to read.")
    limit: int = Field(default=MAX_NODES, description=f"Row cap, at most {MAX_NODES}.")


def _minutes_since(moment) -> int | None:
    """Whole minutes since a timestamp, or None when it never happened."""
    if moment is None:
        return None
    return int((timezone.now() - moment).total_seconds() // 60)


def serialize(node, in_flight: set[int] | None = None) -> dict:
    """
    Describe one watch target as a decision, not as a database row.

    Args:
        node: The frontier node.
        in_flight (set[int] | None): Node ids with a harvest already queued or running.

    Returns:
        dict: `node_id` is what `create_harvest_job` accepts. Timestamps become ages in
            minutes, because "43 minutes ago" is a decision input and an ISO string is not.
    """
    return {
        "node_id": node.id,
        "job_in_flight": node.id in (in_flight or set()),
        "target": node.actor.canonical_name if node.watches_account else node.admin_unit.name,
        "target_kind": "account" if node.watches_account else "place",
        "platform": node.platform,
        "zone": node.zone,
        "yield_rate": round(node.yield_rate, 1),
        "avg_credibility": round(node.avg_credibility, 2),
        "distance_km": node.distance_km,
        "passes": node.passes,
        "total_items": node.total_items,
        "actionable_items": node.actionable_items,
        "is_unexplored": node.is_unexplored,
        "minutes_since_harvest": _minutes_since(node.last_harvest_at),
        "minutes_since_useful_find": _minutes_since(node.last_useful_find_at),
    }


@tool("get_frontier", args_schema=GetFrontierInput)
def get_frontier(event_id: int, limit: int = MAX_NODES) -> dict:
    """
    Read the scoreboard of places and accounts being watched for this event.

    Each row is one target on one platform with its running record. `yield_rate` is
    actionable items per hundred harvested — the quality signal, and the one to rank by.
    Rows are already ordered by it, best first.

    `is_unexplored` marks targets never harvested: their yield is zero because nothing is
    known yet, not because they failed. Spend part of every round on them, or the search
    converges on what it already knows and never finds the district nobody was posting
    about twenty minutes ago.

    `minutes_since_useful_find` is how stale a target has gone; a high-yield node quiet for
    hours is worth less than the number alone suggests. Exhausted and paused targets are
    not listed — they are not decisions left to make.

    `job_in_flight` marks targets already being harvested. Skip them: a harvest takes minutes
    and its results are not in these numbers yet, so queueing one again buys nothing and
    `create_harvest_job` will refuse it.

    Use `node_id` with `create_harvest_job` to act on one.
    """
    try:
        get_event(event_id)
    except ToolInputError as exc:
        return {**exc.payload, "nodes": []}

    nodes = get_frontier_service(event_id, limit=min(limit, MAX_NODES))
    in_flight = set(
        HarvestJob.objects.filter(node__in=nodes, status__in=IN_FLIGHT_STATUSES).values_list(
            "node_id", flat=True
        )
    )
    rows = [serialize(node, in_flight) for node in nodes]

    return {
        "count": len(rows),
        "truncated": len(rows) >= min(limit, MAX_NODES),
        "unexplored": sum(1 for row in rows if row["is_unexplored"]),
        "in_flight": len(in_flight),
        "nodes": rows,
    }
