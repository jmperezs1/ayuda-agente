"""
The fixed route one observation walks, as a retryable unit of work.

The whole sequence lives inside a single task rather than a chain of five. Five tasks would
mean five round trips through the broker and state threaded between them, and a failure at
step four would leave the question of whether to repeat step one — which is the expensive one.
Inside one task the answer is already settled: the `Extraction` is written the moment the
model answers, so a retry resumes past it.

The order is plain Python and the judgment inside each step belongs to the model. That is the
architecture, not an implementation detail: fifty observations process in parallel under a
concurrency cap, a rate limit retries one task rather than corrupting an agent's history, and
a bad field is diagnosed by reading one step's output.

Note:
    Rate limits are retried by the task rather than waited on inside the SDK. A wait long
    enough to clear a minute-scale quota would hold a worker idle, and the queue can hold
    the work for nothing instead.

    The reading throttle is `EXTRACTION_RATE_LIMIT`, and it is a guard rather than the pace.
    The pool size sets the pace: eight slots at 1.7 seconds a call is about 280 posts a minute,
    and a throttle below that leaves the pool idle in front of a full queue — which is exactly
    what 60/m did, holding two slots busy out of eight while 1244 posts waited.
"""

import logging
from uuid import uuid4

from celery import shared_task
from django.conf import settings
from django.db import transaction
from openai import APIError, BadRequestError, RateLimitError

from agent_tools.agents import LLMNotConfigured, build_agent
from ayudagente.radar.choices import EventStatus, JobStatus
from ayudagente.radar.models import Event, Extraction, HarvestJob, Observation, Requirement
from ayudagente.radar.services.comments import queue_comment_pulls
from ayudagente.radar.services.extraction import Extractor
from ayudagente.radar.services.frontier import record_actionable_find
from ayudagente.radar.services.harvest import HarvestNotConfigured, run_harvest_job
from ayudagente.radar.services.ingest import Ingested, Ingestor
from ayudagente.radar.services.media import fetch_media_for
from ayudagente.radar.services.pacing import Verdict, should_decide
from ayudagente.radar.services.promotion import promote_accounts, retire_exhausted

logger = logging.getLogger(__name__)

RETRYABLE = (RateLimitError, APIError)  # retried by the task, never waited on inline

# A 400 is the request itself being wrong, and it subclasses APIError, so it needs naming
PERMANENT = (BadRequestError,)

# What the agent is told each round. Everything else it needs is in its prompt template.
ROUND_PROMPT = (
    "Run a round. Read the frontier, pick the targets worth harvesting now, and queue "
    "a job for each with its rationale. Skip anything already in flight."
)


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE,
    dont_autoretry_for=PERMANENT,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
    rate_limit=settings.EXTRACTION_RATE_LIMIT,
)
def process_observation(self, observation_id: int, *, force: bool = False) -> dict:
    """
    Read one post and turn it into requirements.

    Args:
        observation_id (int): The post to process.
        force (bool): Re-extract even when a reading exists, to roll a new prompt over the
            corpus.

    Returns:
        dict: What was created and what was refused, so a caller can total a run without
            querying.

    Note:
        Safe to run twice. Extraction returns the stored reading, and ingest is skipped when
        the observation already produced requirements — otherwise a retry would duplicate
        every requirement the first attempt had already written.

        Images are fetched first, and the order is load-bearing. The extractor inlines our own
        stored copies, so a post read before its photo reached disk goes through as text only
        — and the platform URL it would have come from expires within hours, so there is no
        second chance. A live run lost the images of 296 posts of 574 that way.

        A `BadRequestError` is refused rather than retried. It subclasses `APIError`, so it
        counted as transient and spent five attempts failing identically — 146 posts doing
        that at once left a pool of 24 idle in front of a queue of 1244.
    """
    observation = Observation.objects.select_related("event").get(pk=observation_id)

    if not force and Requirement.objects.filter(evidence=observation).exists():
        return {"observation": observation_id, "skipped": "already ingested"}

    fetch_media_for(observation)
    extraction = Extractor().run(observation, force=force)
    outcome: Ingested = Ingestor().ingest(extraction)
    record_actionable_find(observation, outcome.requirements)

    return {
        "observation": observation_id,
        "classification": extraction.classification,
        "requirements": len(outcome.requirements),
        "dropped": outcome.dropped,
    }


@shared_task
def process_event(event_id: int, *, limit: int | None = None, force: bool = False) -> dict:
    """
    Queue every post of an event that has not been read yet.

    Args:
        event_id (int): The event to process.
        limit (int | None): Cap on how many to queue, for a cheap first pass.
        force (bool): Re-read posts that already have an extraction.

    Returns:
        dict: How many were queued.
    """
    pending = pending_observations(event_id, force=force)
    if limit is not None:
        pending = pending[:limit]

    ids = list(pending.values_list("pk", flat=True))
    for observation_id in ids:
        process_observation.delay(observation_id, force=force)  # type: ignore[attr-defined]
    logger.info("queued %s observations for event %s", len(ids), event_id)
    return {"event": event_id, "queued": len(ids)}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(HarvestNotConfigured, ValueError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def harvest(self, job_id: int) -> dict:
    """
    Run one harvest job and queue the pipeline over whatever it brought back.

    Args:
        job_id (int): The pending job to execute.

    Returns:
        dict: What the run produced, so a caller can total a round without querying.

    Note:
        Retries on anything transient — Apify rate limits and 5xx are routine — but never on
        a missing token or a job that is not pending. Those do not get better by waiting, and
        retrying them buries the real cause under three identical failures.

        The job is marked `running` by the service before the call, so a retry of a job that
        already started refuses rather than billing twice. That is deliberate: a duplicate
        harvest costs real money and produces nothing, since the posts are already stored.
    """
    result = run_harvest_job(job_id)
    for observation_id in result.observation_ids:
        process_observation.delay(observation_id)  # type: ignore[attr-defined]

    return {
        "job": job_id,
        "items_returned": result.items_returned,
        "items_new": result.items_new,
        "media": result.media,
        "skipped": result.skipped,
    }


def dispatch_pending(event_id: int, limit: int | None = None) -> dict:
    """
    Dispatch every job the frontier agent has queued and nobody has run.

    Args:
        event_id (int): The event whose jobs to run.
        limit (int | None): Cap on how many to dispatch in this round.

    Returns:
        dict: How many were dispatched.

    Note:
        Oldest first. A job queued twenty minutes ago was decided against a scoreboard that
        has since moved, and running it before a fresher one keeps the lag from growing.
    """
    jobs = HarvestJob.objects.filter(event_id=event_id, status=JobStatus.PENDING).order_by(
        "created_at"
    )
    ids = list(jobs.values_list("pk", flat=True)[: limit or None])
    for job_id in ids:
        harvest.delay(job_id)  # type: ignore[attr-defined]
    logger.info("dispatched %s harvest jobs for event %s", len(ids), event_id)
    return {"event": event_id, "dispatched": len(ids)}


@shared_task
def harvest_pending(event_id: int, *, limit: int | None = None) -> dict:
    """Queue the pending harvests of one event. See `dispatch_pending`."""
    return dispatch_pending(event_id, limit)


def run_round(event_id: int, force: bool = False) -> dict:
    """
    Let the frontier agent decide where to look next, with nobody watching.

    Args:
        event_id (int): The event to decide for.
        force (bool): Skip the pacing check. For a human triggering a round by hand.

    Returns:
        dict: Whether it ran, why not when it did not, and how many jobs it queued.

    Note:
        A fresh conversation every round, and that is the whole point. The checkpointer keeps
        state in Postgres, so reusing one thread would carry every previous round's tool calls
        into the next prompt — sixteen rounds overnight and the agent is reading its own
        history instead of the scoreboard.

        Nothing is lost by forgetting, because the memory that matters is in the database.
        `job_in_flight` tells it what is already queued and `create_harvest_job` refuses a
        target harvested minutes ago. A conversation is the wrong place to keep either.
    """
    event = Event.objects.get(pk=event_id)

    verdict = Verdict(True, "forced") if force else should_decide(event)
    if not verdict.proceed:
        logger.info("frontier round skipped for event %s: %s", event_id, verdict.reason)
        return {"event": event_id, "ran": False, "reason": verdict.reason}

    before = HarvestJob.objects.filter(event=event, status=JobStatus.PENDING).count()
    try:
        graph = build_agent("frontier", event)
        graph.invoke(
            {"messages": [{"role": "user", "content": ROUND_PROMPT}]},
            config={"configurable": {"thread_id": f"frontier-{event_id}-{uuid4()}"}},
        )
    except LLMNotConfigured as exc:
        logger.error("frontier round for event %s: %s", event_id, exc)
        return {"event": event_id, "ran": False, "reason": str(exc)}

    queued = HarvestJob.objects.filter(event=event, status=JobStatus.PENDING).count() - before
    logger.info("frontier round for event %s queued %s jobs (%s)", event_id, queued, verdict.reason)
    return {"event": event_id, "ran": True, "reason": verdict.reason, "queued": queued}


@shared_task
def frontier_round(event_id: int, *, force: bool = False) -> dict:
    """Run one decision round for an event. See `run_round`."""
    return run_round(event_id, force)


def run_tick() -> dict:
    """
    One beat of the perpetual loop, across every active event.

    Returns:
        dict: What each event did, so a single log line says whether the night is progressing.

    Note:
        Harvest first, then decide. A round that runs before its predecessor's jobs have been
        executed reads a scoreboard where nothing has moved, and the pacing check would see a
        deep queue and skip anyway — doing the work first is what keeps the loop moving rather
        than oscillating.

        Comment pulls are queued mechanically, between the two. Choosing which post to read
        replies under needs to know what the post said, and the frontier agent never sees a
        post — so that decision cannot be its.

        The frontier is reshaped before the agent reads it, not after. Promotion and retirement
        run on counters the previous round already wrote, so doing them first means the agent
        sees the accounts the sweep just proved and stops being offered the places that went
        quiet — a round late is a round spent on a scoreboard that was already out of date.
    """
    outcomes = {}
    for event in Event.objects.filter(status=EventStatus.ACTIVE):
        dispatch_pending(event.pk)
        queue_comment_pulls(event)
        promote_accounts(event)
        retire_exhausted(event)
        outcomes[event.pk] = run_round(event.pk)
    logger.info("tick covered %s active events", len(outcomes))
    return {"events": outcomes}


@shared_task
def tick() -> dict:
    """One beat of the perpetual loop. See `run_tick`."""
    return run_tick()


@shared_task
def watch_for_events() -> dict:
    """
    Look for disasters nobody has told us about yet.

    Returns:
        dict: What this pass proposed, so one log line says whether the world moved.

    Note:
        Scheduled on its own beat rather than folded into `tick`, and the reason is the
        separation the whole design rests on: this one is free. It reads a public feed, writes
        `paused` events and spends nothing, so it can run unattended forever — while `tick`
        only ever touches events a human armed.

        A dead feed is logged and swallowed here. Detection failing must not take the beat down
        with it; the harvest loop has nothing to do with USGS being up.
    """
    from ayudagente.radar.services.watch import watch

    try:
        proposed = watch()
    except Exception as exc:
        logger.warning("watch pass failed: %s", exc)
        return {"proposed": 0, "error": str(exc)}

    for event in proposed:
        logger.info("detected %s (id %s), paused until armed", event.name, event.pk)
    return {"proposed": len(proposed), "events": [e.pk for e in proposed]}


def pending_observations(event_id: int, *, force: bool = False):
    """
    The posts of an event still waiting to be read.

    Args:
        event_id (int): The event.
        force (bool): When true, everything counts as pending.

    Returns:
        QuerySet[Observation]: Ordered oldest first, so a partial run covers the earliest
            part of the emergency rather than a random slice of it.
    """
    queryset = Observation.objects.filter(event_id=event_id).order_by("posted_at")
    if force:
        return queryset
    return queryset.filter(extraction__isnull=True)


@shared_task
def refresh_coverage(event_id: int) -> dict:
    """
    Repair the coverage cache from the matches a human acted on.

    Args:
        event_id (int): The event whose requirements to recompute.

    Returns:
        dict: How many rows were repaired.

    Note:
        `covered_quantity` is a cache, and a match that later fails leaves it overstating
        what is handled. An overstated cache is the dangerous direction: it makes a shortage
        look covered and stops the site being proposed.
    """
    event = Event.objects.get(pk=event_id)  # raises when the id is wrong, rather than no-op
    repaired = 0
    with transaction.atomic():
        for requirement in Requirement.objects.filter(event=event).select_for_update():
            before = requirement.covered_quantity
            requirement.recompute_covered_quantity()
            repaired += int(requirement.covered_quantity != before)
    return {"event": event_id, "repaired": repaired}


def unread_count(event_id: int) -> int:
    """
    How many posts of an event have never been read.

    Args:
        event_id (int): The event.

    Returns:
        int: Observations with no extraction.
    """
    return Observation.objects.filter(event_id=event_id, extraction__isnull=True).count()


def extraction_cost_estimate(event_id: int, count: int) -> tuple[int, int]:
    """
    Project the token spend of reading `count` more posts, from what reading cost so far.

    Args:
        event_id (int): The event to measure against.
        count (int): How many posts are about to be read.

    Returns:
        tuple[int, int]: Projected input and output tokens. Both zero until at least one
            observation has been read, because there is nothing to extrapolate from and a
            made-up number is worse than none.
    """
    done = Extraction.objects.filter(observation__event_id=event_id).exclude(input_tokens=0)
    sample = done.count()
    if not sample:
        return 0, 0
    totals = {"input": 0, "output": 0}
    for extraction in done.only("input_tokens", "output_tokens"):
        totals["input"] += extraction.input_tokens
        totals["output"] += extraction.output_tokens
    return (
        round(totals["input"] / sample * count),
        round(totals["output"] / sample * count),
    )


@shared_task
def rebuild_graph(event_id: int) -> dict:
    """
    Bring the event's stored graph up to date.

    Safe to over-fire: when the input fingerprint matches the stored snapshot the call
    costs a hash comparison and does no matching work. Signals queue this on every write
    to actors, requirements or matches, so the stored graph is already current by the
    time anyone fetches it.
    """
    from ayudagente.radar.services.graph import refresh_graph

    _snapshot, rebuilt = refresh_graph(event_id)
    return {"event": event_id, "rebuilt": rebuilt}
