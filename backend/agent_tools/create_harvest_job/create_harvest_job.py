"""
`create_harvest_job` as an agent tool.

The frontier agent's only write, and the reason the whole frontier layer exists. Note what
is *not* an argument: the query string and the Apify actor. Both are built in the service,
from the event lexicon and a platform map.

Note:
    Two guarantees depend on the model never composing the query. Every query carries a
    real toponym, or it pulls in other countries' earthquakes; and the terms belonging to
    other concurrent emergencies are excluded. A hallucinated query loses both silently.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import failure
from ayudagente.radar.choices import HarvestTarget
from ayudagente.radar.services import create_harvest_job as create_harvest_job_service

RATIONALE_MIN_CHARS = 20
TARGET_VALUES = ", ".join(HarvestTarget.values)


class CreateHarvestJobInput(BaseModel):
    """Arguments for `create_harvest_job`."""

    event_id: int = Field(description="Event this harvest belongs to.")
    node_id: int = Field(
        description="Which target to harvest, from `get_frontier`. It supplies the platform."
    )
    rationale: str = Field(
        description=(
            "Why this target now, in one or two sentences. Mandatory. It is the only record "
            "of why the round was spent here, and it is shown on the dashboard."
        )
    )
    target_kind: str = Field(
        default=HarvestTarget.SEARCH,
        description=(
            f"One of {TARGET_VALUES}. 'search' sweeps a place and is the cheap default; "
            "the others pull one account, thread or comment set and are the deep pass, "
            "worth spending once a target has proven itself."
        ),
    )


@tool("create_harvest_job", args_schema=CreateHarvestJobInput)
def create_harvest_job(
    event_id: int,
    node_id: int,
    rationale: str,
    target_kind: str = HarvestTarget.SEARCH,
) -> dict:
    """
    Queue a harvest of one frontier target.

    This is how you act on the scoreboard. Pick a `node_id` from `get_frontier`, say why,
    and a worker runs it — you are not waiting for the result and will see its effect as a
    changed `yield_rate` on a later read.

    You do not choose the search terms or the scraper. Those are built from the event's own
    lexicon so every query stays anchored to a real place and excludes other emergencies'
    terms; composing them yourself would lose both guarantees.

    `rationale` is mandatory and refused when vague. Prefer 'search' unless a target has
    already proven it produces actionable content, in which case the deeper kinds are worth
    the pass.
    """
    if target_kind not in HarvestTarget.values:
        return failure(f"unknown target_kind {target_kind!r}", f"use one of {TARGET_VALUES}")

    if len(rationale.strip()) < RATIONALE_MIN_CHARS:
        return failure(
            "rationale is too short",
            "say what this target is and why it is worth a pass right now",
        )

    try:
        job = create_harvest_job_service(
            event_id=event_id,
            node_id=node_id,
            rationale=rationale,
            target_kind=target_kind,
        )
    except ValueError as exc:
        return failure(str(exc))

    return {
        "job_id": job.id,
        "status": job.status,
        "platform": job.platform,
        "target_kind": job.target_kind,
        "node_id": job.node.id if job.node else None,
        # Echoed so the agent can see what its decision actually became
        "query": job.actor_input.get("searchQuery") or job.actor_input.get("handle"),
    }
