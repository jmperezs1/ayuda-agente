"""
Turning what the sweep learned into new watch targets, and retiring the ones that went quiet.

The frontier starts as a list of places, because at minute zero a place is all anyone knows.
What the harvest then discovers is *accounts*: the municipal office posting every collection
point, the volunteer collective coordinating trucks, the neighbour who keeps reporting which
streets are cut. Those are worth following directly, and until now nothing turned that finding
into a target — the agent could allocate depth to an account, but no account was ever added.

The other direction matters just as much. A frontier that only grows ends up with every
municipality of a country in it, and the agent spends its attention re-reading places that
answered nothing four passes running.

Note:
    Both run on counters `record_harvest` and `record_actionable_find` already maintain, so
    this adds a decision rather than a measurement. Nothing here reads a post, which keeps the
    frontier agent's world the same few dozen rows it has always been.

    Exhaustion is reversible, and that is not a nicety. An emergency moves: a municipality
    nobody was posting about at midnight is the story at dawn, and a frontier that could only
    ever shrink would never look again. `record_actionable_find` revives whatever it credits.
"""

import logging
from collections import Counter

from django.db.models import Count

from ayudagente.radar.choices import (
    ContactKind,
    Direction,
    ExtractionClass,
    NodeStatus,
    Zone,
)
from ayudagente.radar.models import (
    ContactPoint,
    Event,
    FrontierNode,
    Observation,
    Requirement,
)

logger = logging.getLogger(__name__)

ACTIONABLE = (ExtractionClass.NEED, ExtractionClass.OFFER, ExtractionClass.BOTH)

# Distinct actionable posts before an account is worth following on its own
PROVEN_POSTS = 3

# Passes a place gets before producing nothing is treated as an answer
EXHAUST_AFTER_PASSES = 4


def promote_accounts(event: Event, limit: int | None = None) -> int:
    """
    Give a frontier node to the accounts that keep producing actionable posts.

    Args:
        event (Event): The emergency.
        limit (int | None): Cap on nodes created this round.

    Returns:
        int: Nodes created. Zero once every proven account already has one, which is the
            steady state.

    Note:
        Counted on the *posting* handle rather than on whether the model read the account as
        the subject of the need. Those are different questions: `is_author` asks whose need it
        is, this asks where good content comes from. A press account reposting forty real
        collection points is a target worth following and is nobody's author.

        An account with no `Actor` behind it is skipped rather than given one. Actors are graph
        entities that a map draws and an agent hands to a citizen as somebody to call, and
        inventing one so the frontier has something to point at would put an aggregator on the
        map as a place to go.
    """
    created = 0

    for platform, handle, _count in _proven_handles(event):
        if limit is not None and created >= limit:
            break

        actor = _actor_behind(event, platform, handle)
        if actor is None:
            continue

        _node, made = FrontierNode.objects.get_or_create(
            event=event,
            actor=actor,
            platform=platform,
            defaults={"admin_unit": None, "zone": _zone_for(actor)},
        )
        created += int(made)

    if created:
        logger.info("promoted %s accounts to frontier nodes for event %s", created, event.pk)
    return created


def retire_exhausted(event: Event) -> int:
    """
    Mark the places that answered nothing across several passes.

    Args:
        event (Event): The emergency.

    Returns:
        int: Nodes retired this round.

    Note:
        Only places. An account earned its node by producing actionable posts, and one quiet
        stretch from a municipal office is a quiet stretch, not an account that stopped being
        the municipal office.

        `total_items` must be non-zero. A node that returned no items at all failed at
        fetching rather than at yielding — a dead Actor or a query the platform rejects — and
        calling that exhausted hides a bug behind a decision.
    """
    retired = FrontierNode.objects.filter(
        event=event,
        actor__isnull=True,
        status=NodeStatus.ACTIVE,
        passes__gte=EXHAUST_AFTER_PASSES,
        actionable_items=0,
        total_items__gt=0,
    ).update(status=NodeStatus.EXHAUSTED)

    if retired:
        logger.info("retired %s exhausted nodes for event %s", retired, event.pk)
    return retired


def _proven_handles(event: Event) -> list[tuple[str, str, int]]:
    """
    The accounts whose posts the pipeline judged actionable often enough to follow.

    Returns:
        list[tuple[str, str, int]]: Platform, handle and count, best first.
    """
    rows = (
        Observation.objects.filter(event=event, extraction__classification__in=ACTIONABLE)
        .exclude(author_handle="")
        .values("platform", "author_handle")
        .annotate(found=Count("id", distinct=True))
        .filter(found__gte=PROVEN_POSTS)
        .order_by("-found")
    )
    return [(row["platform"], row["author_handle"], row["found"]) for row in rows]


def _actor_behind(event: Event, platform: str, handle: str):
    """
    The actor this posting handle reaches, if the pipeline ever resolved one.

    Note:
        Looked up through `ContactPoint`, which is where `_record_author_handle` stores the
        posting account. That write only happens when the model read the actor as the author,
        so the join is deliberately narrow: it finds the accounts that speak for themselves.
    """
    contact = (
        ContactPoint.objects.filter(
            actor__event=event,
            actor__merged_into__isnull=True,
            kind=ContactKind.HANDLE,
            platform=platform,
            value__iexact=handle,
        )
        .select_related("actor")
        .first()
    )
    return contact.actor if contact is not None else None


def _zone_for(actor) -> str:
    """
    Which query axis an account belongs to, read from what it has been posting.

    Note:
        Zone is structural rather than descriptive — it decides which axis runs. An account
        that mostly offers is on the supply side whatever its address says, so the direction
        of its requirements is the honest signal. Ties go to impact, because missing a need
        costs more than missing an offer.
    """
    directions = Counter(
        Requirement.objects.filter(actor=actor).values_list("direction", flat=True)
    )
    offers = directions.get(Direction.OFFERS, 0)
    needs = directions.get(Direction.NEEDS, 0)
    return Zone.SUPPORT if offers > needs else Zone.IMPACT
