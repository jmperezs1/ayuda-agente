"""
Noticing that a disaster happened, before anyone types it in.

Everything downstream of an `Event` row already works; what was missing is the row. This reads
USGS, which publishes every earthquake worldwide within minutes, for free and without a key.

**A proposed event is never harvested.** It is created `paused`, and `Event.is_harvestable`
already returns false for anything that is not `active` — so every writer in the system refuses
it without knowing this module exists. That is the whole cost control: a candidate costs
nothing, and arming it is a human act. It is also the demo, because watching a real emergency
be armed and start pulling posts is the thing worth showing.

Note:
    Detection is not the same problem as scoping. USGS says an earthquake happened, where and
    how hard; it says nothing about who needs water. The sweep still starts from the gazetteer,
    which is why a candidate in a country whose places are not loaded can be seen but not armed.

    PAGER's `alert` is what decides, not magnitude alone. A magnitude 7 under open ocean hurts
    nobody and a magnitude 6 under a city is a catastrophe — USGS already models that, and
    re-deriving it from magnitude and population would be worse and ours to maintain.

See:
    https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from ayudagente.radar.choices import EventStatus, HazardKind
from ayudagente.radar.models import AdminUnit, Event

logger = logging.getLogger(__name__)

USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson"

FEED_TIMEOUT = 20.0

# PAGER's own impact estimate. Green is "felt, nothing broke"; the rest are responses.
RESPONDING_ALERTS = frozenset({"yellow", "orange", "red"})

# A quake PAGER did not rate at all still counts this big, since silence is not an all-clear
MIN_MAGNITUDE = 6.5

# How far an epicentre may sit from a known place and still be attributed to its country
COUNTRY_REACH_KM = 400


@dataclass(frozen=True)
class Candidate:
    """
    One earthquake the feed reported, in our terms.

    Attributes:
        external_id (str): USGS's own id, stable across updates and what makes proposing
            idempotent.
        title (str): USGS's title, used verbatim — "M 7.4 - 13 km SE of Quibdó, Colombia".
        occurred_at (datetime): When it happened, UTC.
        epicenter (Point): Where.
        magnitude (float | None): Moment magnitude.
        depth_km (float | None): Depth.
        alert (str): PAGER level, empty when USGS has not rated it yet.
    """

    external_id: str
    title: str
    occurred_at: datetime
    epicenter: Point
    magnitude: float | None
    depth_km: float | None
    alert: str


def poll(feed_url: str = USGS_FEED, client: httpx.Client | None = None) -> list[Candidate]:
    """
    Read the feed and return the earthquakes worth responding to.

    Args:
        feed_url (str): Override for a different USGS summary feed.
        client (httpx.Client | None): Override for tests.

    Returns:
        list[Candidate]: Newest first. Empty when the feed holds nothing that clears the bar,
            which is the normal state most of the time.

    Raises:
        httpx.HTTPError: If the feed cannot be read. Left to the caller, because a watch stage
            that silently swallows a dead feed reports "no disasters" forever.
    """
    owned = client is None
    client = client or httpx.Client(timeout=FEED_TIMEOUT)
    try:
        response = client.get(feed_url)
        response.raise_for_status()
        features = response.json().get("features", [])
    finally:
        if owned:
            client.close()

    candidates = [_candidate(feature) for feature in features]
    return [c for c in candidates if c is not None and _worth_responding_to(c)]


def propose(candidate: Candidate) -> tuple[Event | None, bool]:
    """
    Record one candidate as a paused event.

    Args:
        candidate (Candidate): What the feed reported.

    Returns:
        tuple[Event | None, bool]: The event and whether this call created it. The event is
            None when the epicentre matches no loaded gazetteer, because an event with no
            country cannot be swept and recording it would only look like progress.

    Note:
        Idempotent on `external_id`. USGS revises magnitude and alert level for hours after a
        quake, so the same id arrives repeatedly, and a second row would split the response in
        half. An existing event is left exactly as it is — including one a human already armed,
        which a re-poll must never quietly pause again.
    """
    existing = Event.objects.filter(detection_source="usgs", external_id=candidate.external_id)
    found = existing.first()
    if found is not None:
        return found, False

    country = country_of(candidate.epicenter)
    if country is None:
        logger.info("no gazetteer covers %s; not proposing it", candidate.title)
        return None, False

    event = Event.objects.create(
        name=candidate.title,
        hazard=HazardKind.EARTHQUAKE,
        occurred_at=candidate.occurred_at,
        epicenter=candidate.epicenter,
        magnitude=candidate.magnitude,
        depth_km=candidate.depth_km,
        country_code=country,
        detection_source="usgs",
        external_id=candidate.external_id,
        status=EventStatus.PAUSED,
        lexicon={"hashtags": [], "negatives": [], "demand": [], "supply": []},
    )
    logger.info("proposed %s (id %s), paused until armed", event.name, event.pk)
    return event, True


def watch(feed_url: str = USGS_FEED, client: httpx.Client | None = None) -> list[Event]:
    """
    One pass: read the feed and propose whatever is new.

    Returns:
        list[Event]: Only the events this pass created, so a caller can report what changed
            rather than restating the whole feed every time.
    """
    proposed = []
    for candidate in poll(feed_url, client):
        event, created = propose(candidate)
        if created and event is not None:
            proposed.append(event)
    return proposed


def country_of(point: Point) -> str | None:
    """
    Which country an epicentre falls in, according to the gazetteers we hold.

    Returns:
        str | None: ISO 3166-1 alpha-2, or None when nothing loaded is near enough.

    Note:
        Answered from `AdminUnit` rather than from the feed's place string. USGS writes
        "13 km SE of Quibdó, Colombia" for people, not for parsing, and the gazetteer is the
        same source the sweep will use — so agreeing with it is the point. A country we cannot
        name here is a country we could not have swept anyway.
    """
    nearest = (
        AdminUnit.objects.filter(centroid__isnull=False)
        .filter(centroid__distance_lte=(point, D(km=COUNTRY_REACH_KM)))
        .annotate(separation=Distance("centroid", point))
        .order_by("separation")
        .values_list("country_code", flat=True)
        .first()
    )
    return nearest


def _candidate(feature: dict) -> Candidate | None:
    """Read one GeoJSON feature, or None when it is missing what we need."""
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    external_id = feature.get("id") or ""
    epoch_ms = properties.get("time")

    if not external_id or epoch_ms is None or len(coordinates) < 2:
        return None

    longitude, latitude = coordinates[0], coordinates[1]
    depth = coordinates[2] if len(coordinates) > 2 else None
    return Candidate(
        external_id=external_id,
        title=(properties.get("title") or "").strip()[:200],
        occurred_at=datetime.fromtimestamp(epoch_ms / 1000, tz=UTC),
        epicenter=Point(longitude, latitude, srid=4326),
        magnitude=properties.get("mag"),
        depth_km=depth,
        alert=(properties.get("alert") or "").lower(),
    )


def _worth_responding_to(candidate: Candidate) -> bool:
    """
    Whether this quake is one people organise around.

    Note:
        PAGER first, magnitude only as the fallback for a quake it has not rated. The two are
        not interchangeable: a rated green quake is explicitly "no response needed" however
        large its magnitude, so magnitude must not override it.
    """
    if candidate.alert:
        return candidate.alert in RESPONDING_ALERTS
    return (candidate.magnitude or 0) >= MIN_MAGNITUDE
