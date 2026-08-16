"""
The search frontier: the scoreboard the agent reads and the jobs it writes.

This is the whole world of the frontier agent. It reads `FrontierNode` and writes
`HarvestJob`, and it never touches an `Observation` — reading a scoreboard and deciding
where to look next is judgment; reading a post is a function.

Note:
    The query string is built here, from the event lexicon, and never by the model. Two
    guarantees depend on that: every query carries a real toponym, and the terms belonging
    to other concurrent emergencies are excluded. Both are one hallucination away from gone
    if the model composes the string.

    The counters are written back here too, and that is what makes the loop a loop. Without
    `record_harvest` and `record_actionable_find` the scoreboard never changes, so a frontier
    agent running every half hour reads identical rows and queues identical jobs all night.
"""

from collections import Counter
from datetime import timedelta

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db import transaction
from django.utils import timezone

from ayudagente.radar.choices import (
    DecisionSource,
    HarvestTarget,
    JobStatus,
    NodeStatus,
    Platform,
)
from ayudagente.radar.models import AdminUnit, Event, FrontierNode, HarvestJob, Observation
from ayudagente.radar.services.apify_inputs import (
    APIFY_ACTOR_BY_PLATFORM,
    Query,
    build_input,
)

MAX_QUERY_TERMS = 12
MAX_NEGATIVE_TERMS = 6

# What one deep pass on a single target is worth fetching
TARGET_ITEM_LIMIT = 100

# A job in either state is still going to produce posts; a second one would duplicate it
IN_FLIGHT_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING)

# How long a target stays off the table after being harvested
COOLDOWN = timedelta(minutes=20)

# How far a requirement may land from a watched centroid and still credit it
NEAREST_NODE_KM = 60


def get_frontier(event_id: int, limit: int = 60) -> list[FrontierNode]:
    """
    The event's watch targets, best quality first, unexplored ones included.

    Args:
        event_id (int): The event whose frontier to read.
        limit (int): Row cap. A few dozen rows is the whole point — this fits in a prompt.

    Returns:
        list[FrontierNode]: Active nodes ordered by `yield_rate`, highest first.

    Note:
        Paused and exhausted nodes are excluded: they are not decisions the agent still has
        to make. Unexplored nodes stay in with a yield of zero and are flagged rather than
        ranked up, because forcing exploration is the caller's policy, not the ordering's.
    """
    return list(
        FrontierNode.objects.filter(event_id=event_id, status=NodeStatus.ACTIVE)
        .select_related("admin_unit", "actor")
        .order_by("-yield_rate", "-updated_at")[:limit]
    )


def target_query(event: Event, admin_unit: AdminUnit | None, handle: str = "") -> Query:
    """
    Describe what a deep pass on one target is looking for.

    Args:
        event (Event): Source of the lexicon and the language.
        admin_unit (AdminUnit | None): The place, when the node watches one.
        handle (str): The account name, when it watches one instead.

    Returns:
        Query: Anchored on the target, widened by the event's own hashtags and nicknames.

    Raises:
        ValueError: When neither a place nor a handle is given. The anchor is not optional
            and not the model's to choose — without it a query for "earthquake help" pulls
            in every other country's earthquake.
    """
    anchor = admin_unit.name if admin_unit is not None else handle
    if not anchor:
        raise ValueError("a target query needs either a place or an account to anchor on")

    lexicon = event.lexicon or {}
    widening = [
        term for key in ("hashtags", "nicknames") for term in (lexicon.get(key) or []) if term
    ][: MAX_QUERY_TERMS - 1]

    return Query(
        toponyms=[anchor],
        hashtags=widening,
        negatives=(lexicon.get("negatives") or [])[:MAX_NEGATIVE_TERMS],
        limit=TARGET_ITEM_LIMIT,
        language=(event.languages or [""])[0],
        since=event.occurred_at.date() if event.occurred_at else None,
    )


def create_harvest_job(
    event_id: int,
    node_id: int,
    rationale: str,
    target_kind: str = HarvestTarget.SEARCH,
    decided_by: str = DecisionSource.AGENT,
) -> HarvestJob:
    """
    Turn a decision about where to look next into an executable, auditable job.

    Args:
        event_id (int): The event the job belongs to.
        node_id (int): The frontier node being harvested; it supplies platform and target.
        rationale (str): Why this target, in plain text. Mandatory.
        target_kind (str): A `HarvestTarget` value — a place sweep or the deep pass.
        decided_by (str): A `DecisionSource` value.

    Returns:
        HarvestJob: Pending, with the exact payload a worker will send.

    Raises:
        ValueError: When the event is not harvestable, when the node does not belong to it,
            when the platform has no configured Apify actor, when `rationale` is empty, or
            when this target was already queued or harvested too recently.

    Note:
        `rationale` is refused when blank rather than defaulted. It is the only record of
        why the agent spent a pass here, and an empty one makes both debugging and the
        dashboard useless.

        The duplicate guard is deliberate redundancy. The prompt already tells the agent not
        to re-harvest what it queued minutes ago, but a job takes minutes to run, so a round
        firing in between reads a scoreboard where nothing has moved yet. The prompt is the
        first defence and this is the one that holds when the model is wrong.
    """
    if not rationale or not rationale.strip():
        raise ValueError("rationale is mandatory: every agent decision records why")

    event = Event.objects.filter(id=event_id).first()
    if event is None:
        raise ValueError(f"event {event_id} does not exist")
    if not event.is_harvestable:
        raise ValueError(f"event {event_id} is {event.status} and accepts no new jobs")

    node = (
        FrontierNode.objects.select_related("admin_unit", "actor")
        .filter(id=node_id, event_id=event_id)
        .first()
    )
    if node is None:
        raise ValueError(f"frontier node {node_id} does not belong to event {event_id}")

    _refuse_duplicate(node, target_kind)

    apify_actor = APIFY_ACTOR_BY_PLATFORM.get(Platform(node.platform))
    if apify_actor is None:
        raise ValueError(f"no Apify actor configured for platform {node.platform!r}")

    # The check constraint guarantees exactly one target, but the types do not know that
    if node.admin_unit is not None:
        actor_input = build_input(node.platform, target_query(event, node.admin_unit))
    elif node.actor is not None:
        actor_input = build_input(
            node.platform, target_query(event, None, handle=node.actor.canonical_name)
        )
    else:
        raise ValueError(f"frontier node {node_id} watches neither a place nor an account")

    return HarvestJob.objects.create(
        event=event,
        node=node,
        platform=node.platform,
        target_kind=target_kind,
        apify_actor=apify_actor,
        actor_input=actor_input,
        decided_by=decided_by,
        rationale=rationale.strip(),
        status=JobStatus.PENDING,
    )


def _refuse_duplicate(node: FrontierNode, target_kind: str) -> None:
    """
    Reject a target that is already being harvested, or was harvested moments ago.

    Args:
        node (FrontierNode): The target.
        target_kind (str): Scoped per kind — a place sweep and a comment pull on the same
            node are different work and may legitimately run close together.

    Raises:
        ValueError: Naming what is already in flight and when the target frees up, so the
            agent can pick something else instead of retrying the same call.
    """
    in_flight = HarvestJob.objects.filter(
        node=node, target_kind=target_kind, status__in=IN_FLIGHT_STATUSES
    ).first()
    if in_flight is not None:
        raise ValueError(
            f"node {node.id} already has a {target_kind} job {in_flight.status} "
            f"(job {in_flight.id}); pick another target"
        )

    if node.last_harvest_at is not None:
        elapsed = timezone.now() - node.last_harvest_at
        if elapsed < COOLDOWN:
            minutes = int((COOLDOWN - elapsed).total_seconds() // 60) + 1
            raise ValueError(
                f"node {node.id} was harvested {int(elapsed.total_seconds() // 60)} minutes "
                f"ago; it is available again in {minutes} minutes"
            )


def record_harvest(job: HarvestJob, *, items_new: int, counts_as_evidence: bool = True) -> None:
    """
    Feed a finished run back into the scoreboard the agent reads.

    Args:
        job (HarvestJob): The finished job. A job with no node — a manual or seeded one —
            is ignored.
        items_new (int): Observations created, after deduplication.
        counts_as_evidence (bool): False when the Actor looked broken. A run that returned
            nothing because the scraper is down must not count as a pass, or the frontier
            learns that a place is quiet when what is quiet is the tool.

    Note:
        The yield denominator is *new* posts, not returned ones. Re-harvesting a place gives
        back the same posts, and counting them again would collapse the yield of exactly the
        targets worth revisiting.
    """
    node = job.node
    if node is None:
        return

    node.last_harvest_at = timezone.now()
    fields = ["last_harvest_at", "updated_at"]

    if counts_as_evidence:
        node.passes += 1
        node.total_items += items_new
        node.observed_cost_usd += job.actual_cost_usd
        node.refresh_yield_rate()
        fields += ["passes", "total_items", "observed_cost_usd", "yield_rate"]

    node.save(update_fields=fields)


def record_actionable_find(observation: Observation, requirements: list) -> None:
    """
    Credit the watch targets whose places produced actionable content.

    Args:
        observation (Observation): The post that was read.
        requirements (list[Requirement]): What it produced. Empty credits nothing.

    Note:
        Credited by *where the requirement landed*, not by which job fetched the post. A
        cold-start sweep batches dozens of toponyms into one query and therefore carries no
        node at all, so crediting the job would throw away everything the broadest and most
        valuable pass discovers.

        A node that produces something is taken out of `exhausted`, because an emergency
        moves: a municipality nobody was posting about at midnight is the story at dawn, and a
        frontier that could only ever shrink would never look there again.

        This is also the other half of `yield_rate`, and it arrives long after the harvest
        that earned it — the post has to be extracted, geocoded and ingested first. That lag
        is why the number is a running total rather than something derived per run: when a
        run finishes, nobody knows yet whether it found anything.
    """
    if not requirements or observation.job_id is None:
        return

    credits: Counter[int] = Counter()
    for requirement in requirements:
        node = _node_to_credit(observation, requirement)
        if node is not None:
            credits[node.pk] += 1

    if not credits:
        return

    with transaction.atomic():
        for node_id, count in credits.items():
            node = FrontierNode.objects.select_for_update().get(pk=node_id)
            node.actionable_items += count
            node.last_useful_find_at = timezone.now()
            node.refresh_yield_rate()
            fields = ["actionable_items", "last_useful_find_at", "yield_rate"]
            if node.status == NodeStatus.EXHAUSTED:
                node.status = NodeStatus.ACTIVE
                fields.append("status")
            node.save(update_fields=fields)


def _node_to_credit(observation: Observation, requirement) -> FrontierNode | None:
    """
    The watch target a requirement belongs to.

    Returns:
        FrontierNode | None: The node whose place this landed in, or None when it landed
            somewhere nobody is watching — which is a discovery, not a failure, and is what
            node promotion will act on.

    Note:
        Exact administrative match first, nearest watched centroid second. The geocoder does
        not always resolve an administrative unit, and for a *ranking* signal the nearest
        watched place is a better answer than none. It is a yield counter, not a claim about
        which municipality the truck should drive to.
    """
    watched = FrontierNode.objects.filter(
        event=observation.event, platform=observation.platform, actor__isnull=True
    )

    location = requirement.location
    if location.admin_unit_id is not None:
        exact = watched.filter(admin_unit_id=location.admin_unit_id).first()
        if exact is not None:
            return exact

    return (
        watched.filter(admin_unit__centroid__isnull=False)
        .annotate(separation=Distance("admin_unit__centroid", location.point))
        .filter(separation__lte=D(km=NEAREST_NODE_KM))
        .order_by("separation")
        .first()
    )
