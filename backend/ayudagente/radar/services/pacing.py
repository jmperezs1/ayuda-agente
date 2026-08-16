"""
Deciding whether to run another round, when nobody is watching.

An emergency has no natural end: there is always another post. So a loop left running
overnight needs a reason to wait, and the honest one is not money — it is **novelty**.
`HarvestJob.items_new` counts what survived deduplication. A pass that returns two hundred
items of which five are new has exhausted its queries for the moment, and the answer is to
wait for the world to produce something, not to spend more looking at the same posts.

That keeps the invariant intact. Cost is still recorded and still decides nothing; novelty is
a quality signal, the same kind as `yield_rate`.

Note:
    Low novelty pauses rounds, it does not end them. A quiet window would otherwise deadlock
    the loop — the measurement is taken over recent jobs, so with no new jobs it stays low
    forever. `PROBE_AFTER` is the escape: past it a round runs regardless, because the only
    way to learn that the world moved is to look.

    The spend ceilings below are circuit breakers, not budgets. Nothing weighs them against
    anything; they exist so a runaway loop at three in the morning stops instead of billing
    until someone wakes up. One is per event and pauses it; the global one refuses at the
    gate and leaves the state alone, so lifting it resumes everything without rearming.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from ayudagente.radar.choices import EventStatus, JobStatus
from ayudagente.radar.models import Event, HarvestJob

logger = logging.getLogger(__name__)

# Share of a recent pass that has to be new before another round is worth deciding
MIN_NOVELTY = 0.10
NOVELTY_WINDOW = 10

# Past this with nothing harvested, run anyway: the only way to learn is to look
PROBE_AFTER = timedelta(minutes=90)

# Deciding more is pointless while this many decisions are still waiting to be executed
MAX_PENDING_JOBS = 12


@dataclass(frozen=True)
class Verdict:
    """
    Whether to act, and why not when the answer is no.

    Attributes:
        proceed (bool): Whether the caller should go ahead.
        reason (str): One line, written to be read in a log at 3am.
    """

    proceed: bool
    reason: str


def should_decide(event: Event) -> Verdict:
    """
    Whether the frontier agent should run a round for this event.

    Args:
        event (Event): The emergency.

    Returns:
        Verdict: `proceed` false when the loop should wait rather than queue more work.

    Note:
        Ordered cheapest first, and by severity. A paused event and a tripped ceiling are
        stops; a deep queue and low novelty are waits. The distinction matters in the log —
        one wants a human, the other resolves itself.
    """
    if not event.is_harvestable:
        return Verdict(False, f"event is {event.status}")

    if _over_ceiling(event):
        return Verdict(False, f"spend ceiling reached at ${event.spent_usd}")

    pending = HarvestJob.objects.filter(event=event, status=JobStatus.PENDING).count()
    if pending >= MAX_PENDING_JOBS:
        return Verdict(False, f"{pending} jobs already queued; the harvest is the bottleneck")

    novelty = recent_novelty(event)
    if novelty is None:
        return Verdict(True, "nothing harvested yet")

    if novelty >= MIN_NOVELTY:
        return Verdict(True, f"{novelty:.0%} of the last harvests were new")

    if _quiet_for_a_while(event):
        return Verdict(True, f"novelty at {novelty:.0%}, but nothing harvested in a while")

    return Verdict(False, f"only {novelty:.0%} new; the queries are exhausted for now")


def recent_novelty(event: Event) -> float | None:
    """
    The share of recently harvested items that were not already held.

    Args:
        event (Event): The emergency.

    Returns:
        float | None: Between 0 and 1, or None when nothing has been harvested yet — which
            is not the same as zero and must not be read as exhaustion.

    Note:
        Measured over jobs rather than over a time window, because harvests are bursty. Ten
        minutes with no jobs says nothing; the last ten jobs say what the platforms are
        currently giving back.
    """
    recent = HarvestJob.objects.filter(
        event=event, status__in=(JobStatus.DONE, JobStatus.EMPTY)
    ).order_by("-finished_at")[:NOVELTY_WINDOW]

    totals = HarvestJob.objects.filter(pk__in=[job.pk for job in recent]).aggregate(
        returned=Sum("items_returned"), new=Sum("items_new")
    )
    returned = totals["returned"] or 0
    if not returned:
        return None
    return (totals["new"] or 0) / returned


def trip_ceiling(event: Event) -> bool:
    """
    Pause an event whose spend has run away.

    Args:
        event (Event): The emergency.

    Returns:
        bool: True when this call paused it.

    Note:
        Pausing rather than merely refusing, because `Event.is_harvestable` is already the
        kill switch every writer checks. One state, one place to look, and one thing for a
        human to undo in the morning.
    """
    if not _over_ceiling(event) or event.status != EventStatus.ACTIVE:
        return False

    event.status = EventStatus.PAUSED
    event.save(update_fields=["status"])
    logger.error(
        "event %s paused: spent $%s against a ceiling of $%s",
        event.pk,
        event.spent_usd,
        settings.HARVEST_SPEND_CEILING_USD,
    )
    return True


def total_spent() -> Decimal:
    """
    What every event has spent between them.

    Returns:
        Decimal: The sum of `Event.spent_usd`, zero when nothing has been harvested yet.
    """
    return Event.objects.aggregate(total=Sum("spent_usd"))["total"] or Decimal("0")


def harvest_refusal(event: Event) -> str | None:
    """
    Why this event must not be harvested right now, or None when it may be.

    Args:
        event (Event): The emergency the job belongs to.

    Returns:
        str | None: A reason fit for a log line, or None to proceed.

    Note:
        Asked at the moment of spending, not at the moment of deciding. The status and the
        per-event ceiling used to be consulted only where jobs are *created*, so a job queued
        while an event was active still billed after the event was paused. This is the last
        gate before Apify and it holds regardless of who queued the job, or when.

        A refusal leaves the job pending on purpose. Raising the ceiling is then the whole
        undo, which is what makes the breaker something a human can lift mid-demonstration.
    """
    if not event.is_harvestable:
        return f"event is {event.status}"
    if _over_ceiling(event):
        return f"event spend ceiling reached at ${event.spent_usd}"

    ceiling = Decimal(str(settings.HARVEST_SPEND_TOTAL_CEILING_USD))
    spent = total_spent()
    if ceiling > 0 and spent >= ceiling:
        return f"global spend ceiling reached at ${spent} of ${ceiling}"
    return None


def _over_ceiling(event: Event) -> bool:
    """Whether recorded spend has passed the circuit breaker."""
    ceiling = Decimal(str(settings.HARVEST_SPEND_CEILING_USD))
    return ceiling > 0 and event.spent_usd >= ceiling


def _quiet_for_a_while(event: Event) -> bool:
    """Whether enough time has passed with no harvest to be worth probing again."""
    last = (
        HarvestJob.objects.filter(event=event, finished_at__isnull=False)
        .order_by("-finished_at")
        .values_list("finished_at", flat=True)
        .first()
    )
    return last is None or timezone.now() - last > PROBE_AFTER
