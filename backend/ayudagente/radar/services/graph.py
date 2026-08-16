"""
Building and persisting the event graph.

The graph is derived data: actors and requirements are its nodes, matches its edges. This
module owns the derivation — serializing the payload, fingerprinting the inputs, and
deciding whether a rebuild is actually needed — so views serve a stored row and triggers
can over-fire without cost.
"""

import hashlib
from contextvars import ContextVar

from django.db.models import Prefetch

from ayudagente.radar.choices import MatchStatus
from ayudagente.radar.models import Actor, Event, GraphSnapshot, Match, Requirement
from ayudagente.radar.services.matching import run_matching_pass

VISIBLE_MATCH_STATUSES = (
    MatchStatus.PROPOSED,
    MatchStatus.CONTACTED,
    MatchStatus.CONFIRMED,
    MatchStatus.DELIVERED,
)

# True while a rebuild writes matches, so the signals its own writes fire become no-ops
rebuilding: ContextVar[bool] = ContextVar("rebuilding", default=False)


def input_fingerprint(event_id: int) -> str:
    """
    Hash of everything the graph payload is derived from.

    Two identical fingerprints mean a rebuild would produce an identical graph, so the
    caller can skip the matching pass and the serialization entirely. Fields listed here
    are exactly the ones the payload or the matching pass reads — widening the payload
    means widening this, or staleness hides behind a matching fingerprint.
    """
    digest = hashlib.sha256()
    parts: list = [
        Actor.objects.filter(event_id=event_id)
        .order_by("id")
        .values_list(
            "id",
            "kind",
            "canonical_name",
            "credibility",
            "verified",
            "location_id",
            "merged_into_id",
        ),
        Requirement.objects.filter(event_id=event_id)
        .order_by("id")
        .values_list(
            "id",
            "direction",
            "resource_id",
            "status",
            "urgency",
            "quantity",
            "covered_quantity",
            "unit",
            "confidence",
            "free_text",
            "location_id",
            "destination_id",
        ),
        Match.objects.filter(need__event_id=event_id)
        .order_by("id")
        .values_list(
            "id",
            "need_id",
            "offer_id",
            "via_transport_id",
            "status",
            "score",
            "distance_km",
            "committed_quantity",
            "rationale",
        ),
    ]
    for queryset in parts:
        for row in queryset.iterator():
            digest.update(repr(row).encode())
        digest.update(b"|")
    return digest.hexdigest()


def build_graph_payload(event: Event) -> dict:
    """
    Serialize the event's graph: actors as nodes, visible matches as edges.

    Note:
        The status set is imported rather than restated. This module used to keep its own
        copy, it never gained `unverified` when the policy did, and the map drew 64 of 512
        requirements while the list endpoint showed all of them. Imported here rather than
        at module level because the views package reaches back into this one.
    """
    from ayudagente.radar.views.policy import OPEN_REQUIREMENT_STATUSES

    open_requirements = Prefetch(
        "requirements",
        queryset=Requirement.objects.filter(status__in=OPEN_REQUIREMENT_STATUSES).select_related(
            "resource", "destination", "location"
        ),
        to_attr="open_requirements",
    )
    actors = (
        Actor.objects.filter(event=event, merged_into__isnull=True)
        .select_related("location", "location__admin_unit")
        .prefetch_related(open_requirements)
    )

    nodes = []
    for actor in actors:
        open_reqs: list[Requirement] = getattr(actor, "open_requirements", [])
        # Fall back to a requirement's point: a node with no dot vanishes from the map
        location = actor.location
        if location is None and open_reqs:
            location = open_reqs[0].location
        nodes.append(
            {
                "id": actor.id,
                "name": actor.canonical_name,
                "kind": actor.kind,
                "credibility": actor.credibility,
                "verified": actor.verified,
                "location": _point(location.point) if location else None,
                "precision": location.precision if location else None,
                "admin_unit": (
                    location.admin_unit.name if location and location.admin_unit else None
                ),
                "requirements": [_requirement(req) for req in open_reqs],
            }
        )

    matches = Match.objects.filter(
        need__event=event, status__in=VISIBLE_MATCH_STATUSES
    ).select_related("need__actor", "need__resource", "offer__actor", "via_transport__actor")
    edges = [
        {
            "id": match.id,
            "from_actor": match.offer.actor_id,
            "to_actor": match.need.actor_id,
            "resource": match.need.resource.name,
            "status": match.status,
            "score": match.score,
            "distance_km": match.distance_km,
            "committed_quantity": _number(match.committed_quantity),
            "via_transport_actor": (match.via_transport.actor_id if match.via_transport else None),
            "rationale": match.rationale,
        }
        for match in matches
    ]

    return {
        "event": {
            "id": event.id,
            "name": event.name,
            "epicenter": _point(event.epicenter),
        },
        "nodes": nodes,
        "edges": edges,
    }


def refresh_graph(event_id: int, force: bool = False) -> tuple[GraphSnapshot, bool]:
    """
    Bring the stored graph up to date, doing nothing when nothing changed.

    Runs the matching pass and re-serializes only when the input fingerprint differs from
    the stored one (or `force`). The stored fingerprint is taken *after* the pass, so the
    matches the pass wrote are inside it — a trigger fired by those very writes finds a
    matching fingerprint and stops. That is the loop terminator.

    Returns:
        (snapshot, rebuilt): `rebuilt` False means the trigger cost two queries and a hash.
    """
    event = Event.objects.get(id=event_id)
    fingerprint = input_fingerprint(event_id)

    snapshot = GraphSnapshot.objects.filter(event=event).first()
    if snapshot is not None and not force and snapshot.input_fingerprint == fingerprint:
        if snapshot.stale:
            snapshot.stale = False
            snapshot.save(update_fields=["stale"])
        return snapshot, False

    token = rebuilding.set(True)
    try:
        run_matching_pass(event_id)
    finally:
        rebuilding.reset(token)

    payload = build_graph_payload(event)
    fingerprint = input_fingerprint(event_id)  # the pass changed matches; stamp AFTER it

    snapshot, _created = GraphSnapshot.objects.update_or_create(
        event=event,
        defaults={"payload": payload, "input_fingerprint": fingerprint, "stale": False},
    )
    return snapshot, True


def _point(point):
    if point is None:
        return None
    return {"lat": point.y, "lon": point.x}


def _number(value):
    return float(value) if value is not None else None


def _requirement(req: Requirement) -> dict:
    return {
        "id": req.id,
        "direction": req.direction,
        "resource": req.resource.name,
        "resource_key": req.resource.key,
        "free_text": req.free_text,
        "urgency": req.urgency,
        "status": req.status,
        "quantity": _number(req.quantity),
        "covered_quantity": _number(req.covered_quantity),
        "outstanding": _number(req.outstanding_quantity),
        "unit": req.unit,
        "destination": _point(req.destination.point) if req.destination else None,
        "confidence": req.confidence,
    }
