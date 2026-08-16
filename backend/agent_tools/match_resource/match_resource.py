"""
`match_resource` as an agent tool.

The one the whole system exists for: somebody has something, somebody needs it, and this
finds the other side. Both directions are the same question asked from opposite ends —
"I can donate dog food, who needs it" and "we need volunteers here, who can come".

Note:
    What makes it more than a filtered list is `still_needed`. A requirement's stored
    coverage only counts matches a human already acted on, so a shelter with three fresh
    proposals still looks untouched — and the agent sends a fourth donor to a place that
    already has help on the way. Subtracting live commitments is the difference between a
    listing and a recommendation.
"""

from django.utils import timezone
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.match_resource.constants import DEFAULT_LIMIT, MAX_LIMIT, NOTE_CHARS
from agent_tools.network import DIRECT_REACH_KM, URGENCY_RANK, Network
from agent_tools.shared import (
    ToolInputError,
    failure,
    get_event,
    resolve_place_arg,
    resolve_resource_arg,
    validate_coordinates,
    validate_radius,
)
from ayudagente.radar.choices import Direction, RequirementStatus
from ayudagente.radar.services import find_requirements as find_requirements_service
from ayudagente.radar.services.matching import geodesic_km


class MatchResourceInput(BaseModel):
    """Arguments for `match_resource`. Descriptions here are what the model reads."""

    event_id: int = Field(description="The emergency this is about.")
    resource_key: str | None = Field(
        default=None,
        description=(
            "What is being offered or asked for, as its slug ('alimentos_mascotas') or its "
            "Spanish name ('Comida para mascotas'). The catalog is Spanish — do not "
            "translate. Search walks the resource tree, so a specific item also finds the "
            "general category and the other way round. Omit to see every resource, which "
            "is how to start when you do not know the right key yet."
        ),
    )
    text: str | None = Field(
        default=None,
        description=(
            "Words to look for in the original wording, for detail the catalog is too "
            "coarse to carry — 'leche de fórmula' lives inside 'alimentos'. Filters, never "
            "reorders. Spanish, like the posts."
        ),
    )
    offering: bool = Field(
        description=(
            "True when someone HAS the resource and you are looking for who needs it. "
            "False when someone NEEDS it and you are looking for who can supply it."
        )
    )
    place: str | None = Field(
        default=None,
        description="Where they are, by name or national code. Results come back nearest first.",
    )
    lat: float | None = Field(
        default=None, description="Latitude, when the spot has no administrative name."
    )
    lon: float | None = Field(default=None, description="Longitude. Required with lat.")
    radius_km: float | None = Field(
        default=None,
        description="With a location, discard anything farther than this many kilometres.",
    )
    sort: str = Field(
        default="urgency",
        description=(
            "'urgency' for what needs answering soonest, 'recent' for what was posted last. "
            "Use 'recent' whenever somebody asks what is new or what is happening now — "
            "urgency says how bad it is, not how fresh. A place given overrides both and "
            "sorts by distance."
        ),
    )
    limit: int = Field(default=DEFAULT_LIMIT, description=f"Rows to return, max {MAX_LIMIT}.")


def _serialize(requirement, network: Network, origin) -> dict:
    """
    Describe one counterparty as a decision: who, how far, how much is really left.

    Note:
        `confirmed` and `sources` travel with every row because the person asking is a member
        of the public, not somebody reviewing a dashboard. They read one sentence and act on
        it, so the answer has to be able to say "nobody has confirmed this yet" — and it can
        only say that if the row carries it.

        `source_post` is the same idea taken to its end: the strongest thing to hand someone
        who has to judge an unconfirmed claim is the post it came from, so they can read it
        themselves.

        `hours_ago` travels beside the timestamp because the agent has to *say* it, and asking
        a model to subtract two dates in its head is how "hace tres horas" becomes wrong. An
        answer with no age in it cannot be judged: during an emergency a collection point from
        four days ago and one from this morning are different facts.
    """
    location = requirement.location
    still = network.still_needed(requirement)
    row = {
        "requirement_id": requirement.id,
        "actor_id": requirement.actor_id,
        "actor": requirement.actor.canonical_name,
        "actor_kind": requirement.actor.kind,
        "resource_key": requirement.resource.key,
        "resource": requirement.resource.name,
        "place": location.raw_text,
        "municipality": location.admin_unit.name if location.admin_unit else None,
        "urgency": requirement.urgency,
        "unit": requirement.unit or None,
        "still_needed": still,
        "already_committed": network.connected.get(requirement.id, 0),
        "reachable_by_us": requirement.actor_id in network.contactable(),
        "confirmed": requirement.status != RequirementStatus.UNVERIFIED,
        "sources": requirement.evidence.count(),
        "actor_verified": requirement.actor.verified,
        "last_seen_at": _iso(requirement.last_seen_at),
        "hours_ago": _hours_ago(requirement.last_seen_at),
    }

    evidence = requirement.evidence.order_by("-posted_at").first()
    if evidence is not None:
        row["source_post"] = evidence.permalink
        row["source_platform"] = evidence.platform
        row["posted_at"] = _iso(evidence.posted_at)
        if evidence.author_handle:
            row["source_author"] = evidence.author_handle
    if requirement.free_text:
        row["note"] = requirement.free_text[:NOTE_CHARS]

    if origin is not None:
        km = round(geodesic_km(location.point, origin), 1)
        row["distance_km"] = km
        row["needs_carrier"] = km > DIRECT_REACH_KM
        if km > DIRECT_REACH_KM:
            carriers = network.carriers_near(origin)
            row["carriers_available"] = len(carriers)
    return row


@tool("match_resource", args_schema=MatchResourceInput)
def match_resource(
    event_id: int,
    offering: bool,
    resource_key: str | None = None,
    text: str | None = None,
    place: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
    sort: str = "urgency",
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """
    Find the other side of a resource: who needs what someone has, or who has what someone needs.

    This is the main tool. Use it for "I want to donate dog food", "we need volunteers at
    this collection centre", "someone has a truck" — anything where one side is known and
    the other has to be found. Set `offering` to say which side you are on.

    Narrow by `resource_key` for a category, or by `text` for wording the categories are
    too coarse to hold. Omit both to see everything open, which is how to start when you
    do not yet know which resource key to ask for.

    Results come nearest first when you give a place, and by urgency otherwise. A place
    orders the whole event rather than cutting it down to that municipality — somebody in
    Pereira needs the closest shelter there is, not only one inside Pereira.

    Read `still_needed`, not the raw amount: it already subtracts what other people have
    promised. A row with `still_needed` 0 is being handled — sending someone there wastes
    a trip. `already_committed` says how many are on it. A null `still_needed` means nobody
    stated an amount, which is not the same as nothing being needed.

    `needs_carrier` true means the two sides are too far apart to meet directly and someone
    has to move it; `carriers_available` counts the transport offers near enough to help.

    `reachable_by_us` false means we have no phone, email or handle for them — you can
    report the match but nobody can be told about it yet.
    """
    if (lat is None) != (lon is None):
        return failure("lat and lon must be given together", candidates=[])

    if radius_km is not None and lat is None and place is None:
        return failure(
            "radius_km needs somewhere to measure from",
            "pass a place, or lat and lon, alongside it",
            candidates=[],
        )

    try:
        if lat is not None and lon is not None:
            validate_coordinates(lat, lon)
        if radius_km is not None:
            validate_radius(radius_km)
        event = get_event(event_id)
        resource = resolve_resource_arg(resource_key) if resource_key else None
        admin_unit = resolve_place_arg(place, event.country_code) if place else None
    except ToolInputError as exc:
        return {**exc.payload, "candidates": []}

    origin = None
    if lat is not None and lon is not None:
        from django.contrib.gis.geos import Point

        origin = Point(lon, lat, srid=4326)
    elif admin_unit is not None and admin_unit.centroid is not None:
        origin = admin_unit.centroid

    # The counterparty is the opposite side of whoever is asking
    direction = Direction.NEEDS if offering else Direction.OFFERS
    limit = max(1, min(limit, MAX_LIMIT))

    network = Network(event_id)
    rows = list(
        find_requirements_service(
            event_id=event_id,
            direction=direction,
            resource=resource,
            # A place we can measure from sorts by distance; only one we cannot narrows
            admin_unit=None if origin is not None else admin_unit,
            text=text,
            near=origin,
            radius_km=radius_km,
            limit=MAX_LIMIT * 3,
        )
    )

    candidates = [_serialize(r, network, origin) for r in rows]
    # Anything already promised in full is not a candidate, it is a distraction
    open_candidates = [c for c in candidates if c["still_needed"] != 0]

    if origin is not None:
        open_candidates.sort(key=lambda c: c.get("distance_km", 1e9))
    elif sort == "recent":
        open_candidates.sort(
            key=lambda c: c.get("hours_ago") if c.get("hours_ago") is not None else 1e9
        )
    else:
        open_candidates.sort(key=lambda c: URGENCY_RANK.get(c["urgency"], 9))

    return {
        "asking_for": resource.key if resource is not None else None,
        "looking_for": "needs" if offering else "offers",
        "count": min(len(open_candidates), limit),
        "truncated": len(open_candidates) > limit,
        "sorted_by": "distance" if origin is not None else sort,
        "fully_covered_hidden": len(candidates) - len(open_candidates),
        "candidates": open_candidates[:limit],
    }


def _iso(moment) -> str | None:
    """The timestamp as an ISO string, or None when nothing recorded one."""
    return moment.isoformat() if moment is not None else None


def _hours_ago(moment) -> float | None:
    """
    How old this is, in hours.

    Note:
        Computed here rather than left to the model. A date subtraction done in prose is a
        date subtraction done wrong, and the age is the part the person actually hears.
    """
    if moment is None:
        return None
    return round((timezone.now() - moment).total_seconds() / 3600, 1)
