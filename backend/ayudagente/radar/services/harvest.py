"""
Executing a harvest job: the step that turns a decision into posts.

The frontier agent writes `HarvestJob` rows and never runs one. This module runs them, and
the split is what lets a failed harvest be retried without invoking the model again — the
decision is already recorded, only the fetch has to repeat.

Persistence is shared with the pilot loader on purpose. A post harvested live and one replayed
from the saved corpus go through the same normalizer and the same writer, so anything measured
against the corpus stays true of production. Two writers would drift within a week.

Note:
    `actor_down` exists because an Apify Actor can return success with zero results while
    being broken — the TikTok scraper did exactly that during the pilot, including on a
    control query. Reading that as "no signal here" makes the frontier abandon a place that
    had information, which is the most expensive mistake this module can make. So a zero
    result is only called empty when the same Actor has recently produced something.

See:
    `ayudagente.radar.services.frontier` for the decision this executes, and for the
    counters a finished run feeds back.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from ayudagente.radar.choices import HarvestTarget, JobStatus
from ayudagente.radar.models import Event, HarvestJob, Media, Observation
from ayudagente.radar.services.frontier import record_harvest
from ayudagente.radar.services.normalize import normalize
from ayudagente.radar.services.pacing import harvest_refusal, trip_ceiling

logger = logging.getLogger(__name__)

RUN_TIMEOUT = timedelta(minutes=10)  # a search that has not answered by now will not
MAX_ITEMS = 200

# How many recent runs of the same Actor must have come back empty before it is called down
ACTOR_DOWN_STREAK = 3

# How far before an event a post may be and still be evidence about it
STALE_GRACE = timedelta(days=2)


class HarvestNotConfigured(RuntimeError):
    """Raised when `APIFY_TOKEN` is missing, so the failure names its cause."""


@dataclass
class Harvested:
    """
    What one run produced.

    Attributes:
        items_returned (int): Rows the Actor gave back, duplicates included.
        items_new (int): Observations actually created, after deduplication.
        media (int): Media rows created alongside them.
        skipped (int): Items with no id or no timestamp, which `Observation` cannot store.
        stale (int): Items that predate the emergency and cannot be evidence about it.
        observation_ids (list[int]): The new observations, for the caller to queue.
    """

    items_returned: int = 0
    items_new: int = 0
    media: int = 0
    skipped: int = 0
    stale: int = 0
    observation_ids: list[int] = field(default_factory=list)


def build_client():
    """
    The Apify client, built from the configured token.

    Raises:
        HarvestNotConfigured: When no token is set. Failing here names the cause; letting the
            client raise produces a 401 from a library, three frames deep.
    """
    if not settings.APIFY_TOKEN:
        raise HarvestNotConfigured("APIFY_TOKEN is not set")

    from apify_client import ApifyClient  # imported late: only the worker needs it

    return ApifyClient(settings.APIFY_TOKEN)


def persist_items(job: HarvestJob, items: list[dict], *, is_comment: bool = False) -> Harvested:
    """
    Turn raw Actor output into observations, skipping what cannot be stored.

    Args:
        job (HarvestJob): The job these items came from; supplies the event and the platform.
        items (list[dict]): Whatever the Actor returned.
        is_comment (bool): Selects the comment normalizer, whose shape differs from a post's.

    Returns:
        Harvested: Counts, and the ids of the observations created.

    Note:
        A duplicate is reused rather than skipped. The same post comes back from two different
        queries all the time, and the uniqueness constraint on `(platform, platform_id)` is
        what makes re-harvesting a place cheap instead of destructive.

        Posts predating the emergency are dropped, and that is not a nicety. Only X honours a
        date filter; Instagram's hashtag scraper has none and TikTok's applies to profiles,
        not searches, so a live sweep came back 31% older than the event — including posts
        from previous years. Reading those costs a model call each and puts last year's
        collection point on today's map.

        One unstorable item costs one item, never the batch. A live Facebook pull died on a
        single identifier longer than its column and took 200 good comments with it, and the
        job was left saying `running` — which blocks that target forever, because a running
        job is in flight and nothing retries it. Each row therefore commits on its own.
    """
    result = Harvested(items_returned=len(items))

    for item in items:
        try:
            with transaction.atomic():
                stored = _store(job, item, is_comment, result)
        except DatabaseError as exc:
            logger.warning("job %s: an item could not be stored: %s", job.pk, exc)
            result.skipped += 1
            continue

        if stored is None:
            continue

        result.items_new += 1
        result.observation_ids.append(stored)

    return result


def _store(job: HarvestJob, item: dict, is_comment: bool, result: Harvested) -> int | None:
    """
    Store one item, or say why it is not stored.

    Returns:
        int | None: The new observation's id, or None when the item was skipped, stale or
            already held.

    Note:
        Its own transaction, so a row the database rejects is rolled back alone. Without that
        the failed statement poisons the surrounding transaction and every later item in the
        batch fails too, which looks exactly like the Actor having returned nothing.
    """
    fields, media_specs = normalize(job.platform, item, is_comment=is_comment)
    if not fields.get("platform_id") or not fields.get("posted_at"):
        result.skipped += 1
        return None

    if _predates_the_event(job, fields["posted_at"]):
        result.stale += 1
        return None

    observation, was_created = Observation.objects.get_or_create(
        platform=job.platform,
        platform_id=fields["platform_id"],
        defaults={**fields, "event": job.event, "job": job, "raw": item},
    )
    if not was_created:
        return None

    Media.objects.bulk_create(Media(observation=observation, **media) for media in media_specs)
    result.media += len(media_specs)
    return observation.pk


def _predates_the_event(job: HarvestJob, posted_at) -> bool:
    """Whether a post is too old to be evidence about this emergency."""
    occurred_at = job.event.occurred_at
    return bool(occurred_at and posted_at and posted_at < occurred_at - STALE_GRACE)


def run_harvest_job(job_id: int, client=None) -> Harvested:
    """
    Run one pending job against Apify and store what comes back.

    Args:
        job_id (int): The job to run.
        client: Override for tests. Defaults to a client built from the settings.

    Returns:
        Harvested: What the run produced. Empty counts when the Actor returned nothing, and
            also when `harvest_refusal` stopped the job before it could spend.

    Raises:
        HarvestNotConfigured: When no Apify token is set.
        ValueError: When the job is not pending. A job already running or done must not be
            executed twice — the second run would bill again for posts already stored.
        Exception: Whatever the client raised, after the failure is recorded on the job. The
            caller decides whether to retry; the row already says what happened.

    Note:
        A refused job stays `pending` and is not marked failed. It was never wrong, only
        untimely, and leaving it queued is what lets raising the ceiling resume the sweep
        instead of forcing somebody to queue it again.

        The job is marked `running` before the call and never inside the same transaction as
        the persistence. A worker killed mid-run must leave a row that says `running`, because
        a row still saying `pending` would be picked up again by the next dispatch.

        Storing is inside the same guard as fetching. It used to be outside, so a row the
        database rejected left the job saying `running` with no error on it — and a running job
        is in flight, so nothing retried it and that target was never harvested again. A job
        that dies has to say it died.
    """
    job = HarvestJob.objects.select_related("event", "node").get(pk=job_id)
    if job.status != JobStatus.PENDING:
        raise ValueError(f"job {job_id} is {job.status}, not pending")

    refusal = harvest_refusal(job.event)
    if refusal is not None:
        logger.warning("harvest job %s refused: %s", job_id, refusal)
        return Harvested()

    job.status = JobStatus.RUNNING
    job.save(update_fields=["status"])

    try:
        run, items = _fetch(job, client or build_client())
        result = persist_items(job, items, is_comment=job.target_kind == HarvestTarget.COMMENTS)
    except Exception as exc:
        logger.exception("harvest job %s failed", job_id)
        _finish(job, JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        raise

    status = _outcome_status(job, result)

    cost = _cost(run)
    with transaction.atomic():
        _finish(
            job,
            status,
            run_id=run.id,
            dataset_id=run.default_dataset_id or "",
            items_returned=result.items_returned,
            items_new=result.items_new,
            cost_usd=cost,
        )
        record_harvest(
            job,
            items_new=result.items_new,
            counts_as_evidence=status != JobStatus.ACTOR_DOWN,
        )
        # Rolled up here or the event's total stays zero and the circuit breaker never trips
        Event.objects.filter(pk=job.event.pk).update(spent_usd=F("spent_usd") + cost)

    job.event.refresh_from_db(fields=["spent_usd"])
    trip_ceiling(job.event)

    logger.info(
        "harvest job %s: %s items, %s new, %s",
        job_id,
        result.items_returned,
        result.items_new,
        status,
    )
    return result


def _fetch(job: HarvestJob, client) -> tuple[Any, list[dict]]:
    """
    Call the Actor and read its dataset.

    Returns:
        tuple: The run, and every item it produced.

    Raises:
        RuntimeError: When the run did not finish inside `RUN_TIMEOUT`, so the caller records
            a failure rather than storing a partial dataset as if it were the whole answer.
    """
    run = client.actor(job.apify_actor).call(
        run_input=job.actor_input,
        max_items=MAX_ITEMS,
        run_timeout=RUN_TIMEOUT,
        wait_duration=RUN_TIMEOUT,
    )
    if run is None:
        raise RuntimeError(f"{job.apify_actor} did not finish within {RUN_TIMEOUT}")

    return run, list(client.dataset(run.default_dataset_id).iterate_items())


def _outcome_status(job: HarvestJob, result: Harvested) -> str:
    """
    Decide what a run's result means.

    Returns:
        str: `done` when anything came back, `empty` when the place is genuinely quiet, and
            `actor_down` when the Actor itself is the likelier explanation.
    """
    if result.items_returned:
        return JobStatus.DONE

    recent = (
        HarvestJob.objects.filter(apify_actor=job.apify_actor, finished_at__isnull=False)
        .exclude(pk=job.pk)
        .order_by("-finished_at")[:ACTOR_DOWN_STREAK]
    )
    streak = [previous.items_returned == 0 for previous in recent]
    if len(streak) == ACTOR_DOWN_STREAK and all(streak):
        return JobStatus.ACTOR_DOWN
    return JobStatus.EMPTY


def _cost(run) -> Decimal:
    """
    What the run billed, or zero when the field is absent.

    Note:
        Recorded because Apify returns it for free and a runaway loop should be visible.
        Nothing reads it to decide anything, so a missing value costs nothing.
    """
    usd = getattr(run, "usage_total_usd", None)
    return Decimal(str(usd)) if usd is not None else Decimal("0")


def _finish(job: HarvestJob, status: str, **updates) -> None:
    """Stamp a job with its outcome, writing only the fields that changed."""
    job.status = status
    job.finished_at = timezone.now()
    changed = ["status", "finished_at"]

    for name, value in (
        ("run_id", updates.get("run_id")),
        ("dataset_id", updates.get("dataset_id")),
        ("error", updates.get("error")),
    ):
        if value is not None:
            setattr(job, name, value)
            changed.append(name)

    for name, key in (("items_returned", "items_returned"), ("items_new", "items_new")):
        if key in updates:
            setattr(job, name, updates[key])
            changed.append(name)

    if "cost_usd" in updates:
        job.actual_cost_usd = updates["cost_usd"]
        changed.append("actual_cost_usd")

    job.save(update_fields=changed)
