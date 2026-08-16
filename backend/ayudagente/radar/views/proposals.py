"""
What the system proposes and a human decides on: matches and outreach drafts.

Both are read-only here. Acting on a proposal — marking a match confirmed, dismissing a
draft — goes through the admin for now, and the invariant behind that is not laziness:
nothing is ever sent by this system, so the only dispatch surface is a link a person clicks.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import MatchStatus, OutreachChannel, OutreachPurpose, OutreachStatus
from ayudagente.radar.models import Event, Match, Outreach
from ayudagente.radar.views import payloads, query
from ayudagente.radar.views.policy import VISIBLE_MATCH_STATUSES

MATCH_ORDERINGS = {
    "score": ("-score", "-created_at"),
    "recent": ("-created_at",),
    "distance": ("distance_km", "-score"),
}


@require_GET
@query.reports_query_errors
def match_list(request, event_id: int):
    """
    Proposed and accepted matches for one event.

    Accepts `status` (repeatable), `order` (`score`, `recent`, `distance`), `limit` and
    `offset`. Defaults to the four statuses the graph draws.
    """
    event = get_object_or_404(Event, id=event_id)

    statuses = query.choices(request, "status", MatchStatus, default=list(VISIBLE_MATCH_STATUSES))
    ordering = request.GET.get("order", "score")
    if ordering not in MATCH_ORDERINGS:
        raise query.QueryError(f"unknown order {ordering!r}. Expected {sorted(MATCH_ORDERINGS)}")

    queryset = (
        Match.objects.filter(need__event=event, status__in=statuses)
        .select_related(
            "need__actor",
            "need__resource",
            "need__location",
            "need__location__admin_unit",
            "offer__actor",
            "offer__resource",
            "offer__location",
            "offer__location__admin_unit",
            "via_transport__actor",
            "via_transport__resource",
            "via_transport__location",
        )
        .order_by(*MATCH_ORDERINGS[ordering])
    )

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse({**envelope, "results": [payloads.match_row(row) for row in rows]})


@require_GET
@query.reports_query_errors
def outreach_list(request, event_id: int):
    """
    Drafted messages for one event, newest first.

    Accepts `status`, `purpose` and `channel` (all repeatable), `limit` and `offset`. Defaults
    to drafts, because those are the ones still waiting for a person.

    Note:
        Every row carries `target_url`. That link *is* the send button — there is no endpoint
        that dispatches anything, by design.
    """
    event = get_object_or_404(Event, id=event_id)

    queryset = Outreach.objects.filter(target_actor__event=event)
    for parameter, allowed, field, default in (
        ("status", OutreachStatus, "status__in", [OutreachStatus.DRAFT]),
        ("purpose", OutreachPurpose, "purpose__in", None),
        ("channel", OutreachChannel, "channel__in", None),
    ):
        values = query.choices(request, parameter, allowed, default=default)
        if values:
            queryset = queryset.filter(**{field: values})

    queryset = queryset.select_related("target_actor", "contact_point").order_by("-created_at")

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse({**envelope, "results": [payloads.outreach_row(row) for row in rows]})
