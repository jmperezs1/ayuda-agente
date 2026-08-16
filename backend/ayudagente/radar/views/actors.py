"""
Actor endpoints: the list behind the map's pins, and the panel that opens when one is clicked.

Note:
    A merged actor resolves to the one it was merged into rather than 404ing. Identity
    resolution keeps running after a frontend has already rendered an id, and a link that
    dies because two duplicates were correctly unified is a bug the user reads as data loss.
"""

from django.db.models import Prefetch, Q, QuerySet
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import ActorKind
from ayudagente.radar.models import Actor, ContactPoint, Event, Requirement
from ayudagente.radar.views import payloads, query
from ayudagente.radar.views.policy import OPEN_REQUIREMENT_STATUSES

MAX_MERGE_HOPS = 10  # a cycle would otherwise hang the request


@require_GET
@query.reports_query_errors
def actor_list(request, event_id: int):
    """
    Actors of one event, filtered and paged.

    Accepts `kind` (repeatable), `q` for a name search, `organizations=true` to keep only
    entities that are not individuals, `limit` and `offset`. Merged duplicates never appear.
    """
    event = get_object_or_404(Event, id=event_id)
    queryset = _filtered(Actor.objects.filter(event=event, merged_into__isnull=True), request)

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse(
        {**envelope, "results": [_row(actor) for actor in rows]},
    )


@require_GET
def actor_detail(request, actor_id: int):
    """
    One actor with its contact details and everything it needs or offers.

    Returns:
        JsonResponse: The actor's payload, plus `requested_id` when the id asked for had been
            merged into this one, so the frontend can correct the link it holds.
    """
    requested = get_object_or_404(
        Actor.objects.select_related("location", "location__admin_unit"), id=actor_id
    )
    actor = _survivor(requested)

    contacts = sorted(
        ContactPoint.objects.filter(actor=actor),
        key=lambda contact: (contact.preference_rank(), -contact.times_seen),
    )
    requirements = (
        Requirement.objects.filter(actor=actor, status__in=OPEN_REQUIREMENT_STATUSES)
        .select_related("resource", "destination")
        .order_by("-last_seen_at")
    )

    payload = payloads.actor_detail(actor, contacts=contacts, requirements=list(requirements))
    if actor.id != requested.id:
        payload["requested_id"] = requested.id
    return JsonResponse(payload)


def _survivor(actor: Actor) -> Actor:
    """
    Follow the merge chain to the actor that is still in use.

    Note:
        Follows every hop, not one. Merges chain — A into B, later B into C — and stopping at
        the first hop returns an actor that is itself retired, which reads as an empty profile
        rather than as a bug.
    """
    seen = {actor.id}
    for _ in range(MAX_MERGE_HOPS):
        parent = actor.merged_into
        if parent is None or parent.id in seen:
            break
        actor = parent
        seen.add(actor.id)
    return actor


def _filtered(queryset: QuerySet, request) -> QuerySet:
    """Apply the name and kind filters, and prefetch what a row needs."""
    kinds = query.choices(request, "kind", ActorKind)
    if kinds:
        queryset = queryset.filter(kind__in=kinds)

    if request.GET.get("organizations") == "true":
        queryset = queryset.exclude(kind=ActorKind.PERSON)

    text = request.GET.get("q", "").strip()
    if text:
        queryset = queryset.filter(
            Q(canonical_name__icontains=text) | Q(name_norm__icontains=text.casefold())
        )

    open_requirements = Prefetch(
        "requirements",
        queryset=Requirement.objects.filter(status__in=OPEN_REQUIREMENT_STATUSES).select_related(
            "resource", "destination"
        ),
        to_attr="open_requirements",
    )
    reachable_contacts = Prefetch(
        "contact_points",
        queryset=ContactPoint.objects.filter(reachable=True),
        to_attr="reachable_contacts",
    )

    return (
        queryset.select_related("location", "location__admin_unit")
        .prefetch_related(open_requirements, reachable_contacts)
        .order_by("-last_seen_at")
    )


def _row(actor: Actor) -> dict:
    """
    One actor as a list row: where it is, what it wants, and whether it can be reached.

    Note:
        `contact_count` rather than the contacts themselves. A list of two hundred actors
        carrying every phone number is a payload nobody needs and a disclosure nobody asked
        for; the detail endpoint is one click away.
    """
    open_reqs: list[Requirement] = getattr(actor, "open_requirements", [])
    contacts: list[ContactPoint] = getattr(actor, "reachable_contacts", [])

    return {
        **payloads.actor_brief(actor),
        "location": payloads.location(actor.location),
        "last_seen_at": actor.last_seen_at.isoformat(),
        "contact_count": len(contacts),
        "can_be_reached": any(contact.can_carry_a_message for contact in contacts),
        "requirements": [payloads.requirement_brief(req) for req in open_reqs],
    }
