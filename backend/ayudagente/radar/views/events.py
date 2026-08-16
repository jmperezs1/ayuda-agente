"""
Event endpoints: the list, one event's header, and the whole graph in a single response.

The graph endpoint is deliberately not paginated. A map is only readable when it is complete —
half the needs of a city is a worse picture than none — and at pilot size the whole thing is a
few hundred nodes. When it stops fitting, that is the moment to page it, not before.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import Direction, EventStatus, OutreachStatus
from ayudagente.radar.models import (
    Actor,
    Event,
    GraphSnapshot,
    Match,
    Observation,
    Outreach,
    Requirement,
)
from ayudagente.radar.services import refresh_graph
from ayudagente.radar.views import payloads
from ayudagente.radar.views.policy import OPEN_REQUIREMENT_STATUSES, VISIBLE_MATCH_STATUSES


@require_GET
def event_list(request):
    """Active events, newest first — the frontend's entry point."""
    events = Event.objects.filter(status=EventStatus.ACTIVE).order_by("-occurred_at")
    return JsonResponse({"events": [payloads.event_brief(event) for event in events]})


@require_GET
def event_detail(request, event_id: int):
    """One event with the counts a dashboard header shows."""
    event = get_object_or_404(Event, id=event_id)
    return JsonResponse(payloads.event_detail(event, _summary(event)))


@require_GET
def event_graph(request, event_id: int):
    """
    The whole graph for one event: actors as nodes, matches as edges, open requirements
    attached to their node.

    Note:
        Served from the stored snapshot, which writes rebuild through signals. A read is one
        row whatever the graph's size, so the map's first paint does not pay for a matching
        pass that nothing has invalidated since the last one.

        `built_at` travels with it. Without it the frontend cannot tell a graph rebuilt a
        second ago from one whose rebuild trigger has been failing since midnight, and a
        stale map that looks live is worse than one that admits its age.

        A snapshot marked `stale` is rebuilt here rather than served. This used to rebuild
        only when none existed at all, which meant that once one was written it was served
        forever — an event reported 803 requirements in its summary and none in its graph,
        and neither endpoint was wrong by its own logic. The read path now closes the loop it
        used to delegate to a worker that a deployment may deliberately not run.
    """
    event = get_object_or_404(Event, id=event_id)
    snapshot = GraphSnapshot.objects.filter(event=event).first()
    if snapshot is None or snapshot.stale:
        snapshot, _rebuilt = refresh_graph(event.id)
    return JsonResponse({**snapshot.payload, "built_at": snapshot.built_at.isoformat()})


def _summary(event: Event) -> dict:
    """
    The counts a header shows.

    Note:
        `needs` and `offers` count open requirements, not all of them, so they answer "what is
        outstanding" rather than "what has ever been seen". `unread_observations` is the one
        that says whether the pipeline is behind.
    """
    open_requirements = Requirement.objects.filter(
        event=event, status__in=OPEN_REQUIREMENT_STATUSES
    )
    observations = Observation.objects.filter(event=event)

    return {
        "actors": Actor.objects.filter(event=event, merged_into__isnull=True).count(),
        "needs": open_requirements.filter(direction=Direction.NEEDS).count(),
        "offers": open_requirements.filter(direction=Direction.OFFERS).count(),
        "requirements": Requirement.objects.filter(event=event).count(),
        "matches": Match.objects.filter(
            need__event=event, status__in=VISIBLE_MATCH_STATUSES
        ).count(),
        "observations": observations.count(),
        "unread_observations": observations.filter(extraction__isnull=True).count(),
        "outreach_drafts": Outreach.objects.filter(
            target_actor__event=event, status=OutreachStatus.DRAFT
        ).count(),
    }
