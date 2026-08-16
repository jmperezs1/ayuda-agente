"""
The cold-start pass: covering a whole country before anyone knows where to look.

This is a different mode from the frontier agent, and conflating the two is what made the
first version wrong. The agent allocates **depth** — which municipality deserves a profile
pull, which thread to open — and that is judgment, a handful of targets per round. Covering
the country is not judgment. It is breadth, it is mechanical, and it has to happen in minutes.

The difference is batching. `build_search_query` composes one query per place, so a country
of eleven hundred municipalities costs eleven hundred queries. A sweep puts many toponyms in
one query joined by OR: the pilot covered Colombia with ten queries across both axes for ten
cents, and one of them is what surfaced Herveo and the Wounaan reserve — places nobody would
have thought to enumerate.

Note:
    Sweep jobs carry no `FrontierNode`, because one query spans dozens of places and a job
    points at a single node. They are `decided_by=rule`, not `agent`. What they produce still
    credits the right node: the pipeline geocodes each post, and the requirement's admin unit
    is what the yield is written against.

    The axis vocabulary — the words that separate "necesitamos" from "estamos recogiendo" —
    lives in the event's lexicon, not here. It is per-language data, and the pipeline is not
    Colombia-specific.
"""

import logging

from django.contrib.gis.db.models.functions import Distance
from django.db.models import F, QuerySet

from ayudagente.radar.choices import (
    AdminLevel,
    DecisionSource,
    HarvestTarget,
    JobStatus,
    Platform,
    Zone,
)
from ayudagente.radar.models import AdminUnit, Event, FrontierNode, HarvestJob
from ayudagente.radar.services.apify_inputs import (
    APIFY_ACTOR_BY_PLATFORM,
    Query,
    build_input,
)
from ayudagente.radar.services.frontier import MAX_NEGATIVE_TERMS

logger = logging.getLogger(__name__)

# Places within this of the epicenter report needs; the rest of the country offers help
IMPACT_RADIUS_KM = 250

# Query length is the real cap. The pilot's queries carried about a dozen terms each
TOPONYMS_PER_QUERY = 8
AXIS_TERMS_PER_QUERY = 4

# How many places per zone become watch targets; the long tail arrives by promotion
MAX_PLACES_PER_ZONE = 25

SWEEP_ITEM_LIMIT = 200

# Explicit NULLS LAST, or Postgres ranks the places with no population figure above every city
BY_SIZE = F("population").desc(nulls_last=True)
BY_PROXIMITY = F("distance").asc(nulls_last=True)


def bootstrap_event(event: Event, platforms: list[str] | None = None) -> dict:
    """
    Prepare a fresh event to be harvested: watch targets, then the sweep that covers them.

    Args:
        event (Event): A newly created event. Its country decides which gazetteer is walked.
        platforms (list[str] | None): Defaults to every configured platform.

    Returns:
        dict: How many nodes and jobs were created.

    Raises:
        ValueError: When the country has no administrative units loaded. A sweep with no
            toponym would pull in every other country's disaster, which is invariant 9.

    Note:
        Idempotent. Nodes are matched on their uniqueness constraint and jobs are only created
        for a zone that has no pending sweep, so running this twice on the same event costs
        two queries and creates nothing.
    """
    platforms = platforms or list(APIFY_ACTOR_BY_PLATFORM)

    if not AdminUnit.objects.filter(country_code=event.country_code).exists():
        raise ValueError(
            f"no administrative units loaded for {event.country_code}; "
            f"run `manage.py load_gazetteer {event.country_code}` first"
        )

    nodes = _create_nodes(event, platforms)
    jobs = _create_sweep_jobs(event, platforms)

    logger.info("bootstrapped event %s: %s nodes, %s sweep jobs", event.pk, nodes, jobs)
    return {"nodes": nodes, "jobs": jobs}


def places_by_zone(event: Event, zone: str) -> QuerySet:
    """
    The places of one zone, most populous first.

    Args:
        event (Event): Supplies the country, the declared impact area and the epicenter.
        zone (str): A `Zone` value.

    Returns:
        QuerySet[AdminUnit]: First-level divisions and the largest second-level ones.

    Note:
        The country is the outer bound in every case. Invariant 9 exists because a query with
        no toponym from the event's country pulls in every other country's disaster, and a
        sweep is the pass most exposed to that.

        The two zones are ranked by different things, and it matters. Impact goes by distance
        to the epicenter, because the question there is who got hit; ranking it by population
        leads the query with the largest cities in the region while the epicentre's own
        municipality falls off the end. Support goes by population, because the question there
        is who can send help, and that is a matter of size.

        Neither is yield, because before any harvest yield is a column of zeroes.
    """
    units = AdminUnit.objects.filter(
        country_code=event.country_code, level__in=(AdminLevel.ADMIN_1, AdminLevel.ADMIN_2)
    )
    if event.epicenter is not None:
        units = units.annotate(distance=Distance("centroid", event.epicenter))

    impact = impact_units(event, units)
    ranking = BY_PROXIMITY if zone == Zone.IMPACT and event.epicenter is not None else BY_SIZE

    if impact is None:
        return units.none() if zone == Zone.SUPPORT else units.order_by(ranking)

    chosen = units.filter(pk__in=impact) if zone == Zone.IMPACT else units.exclude(pk__in=impact)
    return chosen.order_by(ranking)


def impact_units(event: Event, units: QuerySet) -> set[int] | None:
    """
    Which places count as affected.

    Args:
        event (Event): The emergency.
        units (QuerySet): The country's administrative units, annotated with `distance` when
            the event has an epicenter.

    Returns:
        set[int] | None: Ids of the affected units, or None when the whole country is treated
            as impact because nothing distinguishes one place from another yet.

    Note:
        `affected_units` comes first because it is a statement about this disaster rather than
        a shape derived from a point. A declared department carries its municipalities with
        it: saying "Chocó is affected" means Quibdó is.

        The radius is a cold-start fallback and a crude one. A circle is roughly right for an
        earthquake and wrong for everything else — a flood follows a basin, a cyclone a track,
        a wildfire the terrain — so it is what runs until the event declares its area or the
        yield says where the needs actually are.
    """
    declared = event.affected_units.all()
    if declared.exists():
        ids = set(declared.values_list("pk", flat=True))
        return ids | set(units.filter(parent__in=declared).values_list("pk", flat=True))

    if event.epicenter is None:
        return None

    near = units.exclude(centroid__isnull=True).filter(distance__lte=IMPACT_RADIUS_KM * 1000)
    return set(near.values_list("pk", flat=True))


def sweep_query(event: Event, units: list[AdminUnit], zone: str) -> Query:
    """
    Describe what a sweep is looking for, in the domain's terms rather than any Actor's.

    Args:
        event (Event): Source of the lexicon and the language.
        units (list[AdminUnit]): The places this query covers.
        zone (str): Selects the axis vocabulary — demand for impact, supply for support.

    Returns:
        Query: Toponyms first, then the axis words and hashtags. `build_input` decides how
            each platform receives them, because only X batches with OR.

    Note:
        Names are deduplicated because the two levels overlap: a capital district is both a
        first-level division and a municipality, and repeating "Bogotá" spends a slot in a
        query whose length is the real constraint.

        Each name is also produced carrying its region, for the platforms that tokenize
        rather than honour a quoted phrase. "Río Quito" alone returned Quito, Ecuador.
    """
    lexicon = event.lexicon or {}

    toponyms: list[str] = []
    qualified: list[str] = []
    for unit in units:
        if unit.name in toponyms:
            continue
        toponyms.append(unit.name)
        region = unit.parent.name if unit.parent and unit.parent.name != unit.name else ""
        qualified.append(f"{unit.name} {region}".strip())
        if len(toponyms) == TOPONYMS_PER_QUERY:
            break

    axis_key = "demand" if zone == Zone.IMPACT else "supply"
    return Query(
        toponyms=toponyms,
        qualified=qualified,
        axis_terms=(lexicon.get(axis_key) or [])[:AXIS_TERMS_PER_QUERY],
        hashtags=(lexicon.get("hashtags") or [])[:AXIS_TERMS_PER_QUERY],
        negatives=(lexicon.get("negatives") or [])[:MAX_NEGATIVE_TERMS],
        limit=SWEEP_ITEM_LIMIT,
        language=(event.languages or [""])[0],
        since=event.occurred_at.date() if event.occurred_at else None,
    )


def _create_nodes(event: Event, platforms: list[str]) -> int:
    """
    Give the event a scoreboard to start from: the places worth watching, per platform.

    Returns:
        int: Nodes created. Zero on a second run.
    """
    created = 0
    for zone in (Zone.IMPACT, Zone.SUPPORT):
        for unit in places_by_zone(event, zone)[:MAX_PLACES_PER_ZONE]:
            for platform in platforms:
                _node, was_created = FrontierNode.objects.get_or_create(
                    event=event,
                    admin_unit=unit,
                    platform=platform,
                    defaults={"zone": zone, "distance_km": _distance_km(unit)},
                )
                created += int(was_created)
    return created


def _distance_km(unit) -> float | None:
    """
    How far a place sits from the epicentre, in kilometres.

    Returns:
        float | None: None when the queryset carried no distance annotation.

    Note:
        Tested against None rather than for truth. A `Distance` of zero is falsy, so the
        shorter idiom stored the measure object itself in a float column and a unit whose
        centroid landed on the epicentre could not be armed at all.
    """
    distance = getattr(unit, "distance", None)
    return None if distance is None else distance.km


def _create_sweep_jobs(event: Event, platforms: list[str]) -> int:
    """
    Queue one batched harvest per platform and zone.

    Returns:
        int: Jobs created. A zone that already has a pending sweep is left alone, so a repeat
            bootstrap does not double the bill.
    """
    created = 0
    for zone in (Zone.IMPACT, Zone.SUPPORT):
        units = list(places_by_zone(event, zone)[:TOPONYMS_PER_QUERY])
        if not units:
            continue

        query = sweep_query(event, units, zone)
        for platform in platforms:
            actor_input = build_input(platform, query)
            already = HarvestJob.objects.filter(
                event=event,
                platform=platform,
                node__isnull=True,
                status=JobStatus.PENDING,
                actor_input=actor_input,
            ).exists()
            if already:
                continue

            HarvestJob.objects.create(
                event=event,
                node=None,
                platform=platform,
                target_kind=HarvestTarget.SEARCH,
                apify_actor=APIFY_ACTOR_BY_PLATFORM[Platform(platform)],
                actor_input=actor_input,
                decided_by=DecisionSource.RULE,
                rationale=(
                    f"Cold-start sweep of the {zone} zone on {platform}: "
                    f"{len(units)} toponyms in one query, before any yield is known."
                ),
                status=JobStatus.PENDING,
            )
            created += 1
    return created
