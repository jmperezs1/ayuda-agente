"""
Requirement endpoints: the filtered list that feeds both the map and the feed, and the detail
view that shows the posts behind one item.

The list is the busiest endpoint in the API. It answers "draw every open need in this
municipality", "show me critical water requests" and "what is inside the box the user just
drew on the map" — all the same query with different filters, which is why they are parsed
generically rather than one endpoint per question.
"""

from django.contrib.gis.measure import D
from django.db.models import Case, IntegerField, Q, QuerySet, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import (
    ActorKind,
    Direction,
    LocationPrecision,
    RequirementStatus,
    Urgency,
    precisions_at_least,
)
from ayudagente.radar.models import Event, Match, Requirement
from ayudagente.radar.views import payloads, query
from ayudagente.radar.views.policy import OPEN_REQUIREMENT_STATUSES, VISIBLE_MATCH_STATUSES

# Rank explicitly: sorting the raw string puts "critical" after "high"
URGENCY_RANK = Case(
    *[When(urgency=value, then=rank) for rank, value in enumerate(Urgency.values)],
    output_field=IntegerField(),
)

ORDERINGS = {
    "urgency": ("urgency_rank", "-last_seen_at"),
    "recent": ("-last_seen_at",),
    "confidence": ("-confidence", "-last_seen_at"),
}


@require_GET
@query.reports_query_errors
def requirement_list(request, event_id: int):
    """
    Needs and offers for one event, filtered and paged.

    Accepts `direction`, `status`, `urgency`, `resource` and `actor_kind` (all repeatable),
    `q` for free text, `min_precision`, `bbox=minLon,minLat,maxLon,maxLat`, `near=lat,lon`
    with `radius_km`, `order`, `limit` and `offset`.

    Returns:
        JsonResponse: `{count, limit, offset, results}`. `count` is the total matching the
            filters, not the size of the page.

    Note:
        Status defaults to open and partial. A frontend asking for "the needs" means the live
        ones, and defaulting to everything would bury them under months of covered history.
    """
    event = get_object_or_404(Event, id=event_id)
    queryset = _filtered(Requirement.objects.filter(event=event), request)

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse({**envelope, "results": [payloads.requirement_row(row) for row in rows]})


@require_GET
def requirement_detail(request, requirement_id: int):
    """
    One requirement with the posts it came from and the matches proposed over it.

    Note:
        Matches are looked up on both sides. A requirement can be the need of one match and
        the offer of another — a collection center receives and distributes — and showing only
        one side makes half its activity invisible.
    """
    requirement = get_object_or_404(
        Requirement.objects.select_related(
            "actor", "resource", "location", "location__admin_unit", "destination"
        ),
        id=requirement_id,
    )

    evidence = requirement.evidence.prefetch_related("media").order_by("posted_at")
    matches = (
        Match.objects.filter(Q(need=requirement) | Q(offer=requirement))
        .filter(status__in=VISIBLE_MATCH_STATUSES)
        .select_related(
            "need__actor",
            "need__resource",
            "need__location",
            "offer__actor",
            "offer__resource",
            "offer__location",
            "via_transport__actor",
            "via_transport__resource",
            "via_transport__location",
        )
        .order_by("-score")
    )

    return JsonResponse(
        payloads.requirement_detail(requirement, evidence=list(evidence), matches=list(matches))
    )


def _filtered(queryset: QuerySet, request) -> QuerySet:
    """
    Apply every filter present in the query string.

    Returns:
        QuerySet: Ordered and ready to page, with the joins the payload needs already
            selected so a page of 100 rows does not become 400 queries.
    """
    statuses = query.choices(
        request, "status", RequirementStatus, default=list(OPEN_REQUIREMENT_STATUSES)
    )
    queryset = queryset.filter(status__in=statuses)

    for parameter, allowed, field in (
        ("direction", Direction, "direction__in"),
        ("urgency", Urgency, "urgency__in"),
        ("actor_kind", ActorKind, "actor__kind__in"),
    ):
        values = query.choices(request, parameter, allowed)
        if values:
            queryset = queryset.filter(**{field: values})

    resources = request.GET.getlist("resource")
    if resources:
        queryset = queryset.filter(resource__key__in=resources)

    text = request.GET.get("q", "").strip()
    if text:
        queryset = queryset.filter(
            Q(free_text__icontains=text) | Q(actor__canonical_name__icontains=text)
        )

    precision = query.choices(request, "min_precision", LocationPrecision)
    if precision:
        queryset = queryset.filter(location__precision__in=precisions_at_least(precision[0]))

    queryset = _within_area(queryset, request)

    return (
        queryset.select_related(
            "actor", "resource", "location", "location__admin_unit", "destination"
        )
        .annotate(urgency_rank=URGENCY_RANK)
        .order_by(*_ordering(request))
    )


def _within_area(queryset: QuerySet, request) -> QuerySet:
    """
    Narrow to a box or a radius, when the request asked for one.

    Raises:
        QueryError: When both `bbox` and `near` are given. They would silently intersect, and
            an empty result from an intersection nobody meant to ask for is hard to diagnose.
    """
    box = query.bbox(request)
    around = query.near(request)

    if box is not None and around is not None:
        raise query.QueryError("use either bbox or near, not both")
    if box is not None:
        return queryset.filter(location__point__within=box)
    if around is not None:
        centre, radius_km = around
        return queryset.filter(location__point__distance_lte=(centre, D(km=radius_km)))
    return queryset


def _ordering(request) -> tuple[str, ...]:
    """
    The sort order, defaulting to most urgent first.

    Raises:
        QueryError: On an unknown `order` value.
    """
    requested = request.GET.get("order", "urgency")
    if requested not in ORDERINGS:
        raise query.QueryError(f"unknown order {requested!r}. Expected {sorted(ORDERINGS)}")
    return ORDERINGS[requested]
