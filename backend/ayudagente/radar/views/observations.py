"""
Observation endpoints: the raw radar feed, and one post with everything harvested from it.

These sit below the graph. The graph says "Barrio Cuba needs water"; this says which post
claimed it, who wrote it, what the photo showed and what the model made of it. It is the
answer to "how do you know that", which is the first question anyone asks of a system that
reads social media during an emergency.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import ExtractionClass, Platform
from ayudagente.radar.models import Event, Observation, Requirement
from ayudagente.radar.views import payloads, query


@require_GET
@query.reports_query_errors
def observation_list(request, event_id: int):
    """
    Posts harvested for one event, newest first.

    Accepts `platform` and `classification` (repeatable), `q` for free text, `has_media=true`,
    `unread=true` for posts the pipeline has not read yet, `limit` and `offset`.
    """
    event = get_object_or_404(Event, id=event_id)
    queryset = Observation.objects.filter(event=event)

    platforms = query.choices(request, "platform", Platform)
    if platforms:
        queryset = queryset.filter(platform__in=platforms)

    classifications = query.choices(request, "classification", ExtractionClass)
    if classifications:
        queryset = queryset.filter(extraction__classification__in=classifications)

    text = request.GET.get("q", "").strip()
    if text:
        queryset = queryset.filter(text__icontains=text)

    if request.GET.get("has_media") == "true":
        queryset = queryset.filter(media__isnull=False).distinct()

    if request.GET.get("unread") == "true":
        queryset = queryset.filter(extraction__isnull=True)

    queryset = queryset.prefetch_related("media").order_by("-posted_at")

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse({**envelope, "results": [payloads.observation_brief(row) for row in rows]})


@require_GET
def observation_detail(request, observation_id: int):
    """
    One post, what the model read in it, and the requirements it produced.

    Note:
        `extraction` is the audit trail: the classification, the confidence and the model that
        produced them. Showing it is what lets a coordinator disagree with the system on a
        specific item instead of distrusting all of it.
    """
    observation = get_object_or_404(
        Observation.objects.prefetch_related("media"), id=observation_id
    )
    requirements = (
        Requirement.objects.filter(evidence=observation)
        .select_related("actor", "resource", "location", "location__admin_unit", "destination")
        .order_by("-confidence")
    )

    extraction = getattr(observation, "extraction", None)
    return JsonResponse(
        {
            **payloads.observation_brief(observation),
            "hashtags": observation.hashtags,
            "mentions": observation.mentions,
            "external_links": observation.external_links,
            "platform_geo": payloads.point(observation.platform_geo),
            "platform_geo_name": observation.platform_geo_name,
            "harvested_at": observation.harvested_at.isoformat(),
            "extraction": _extraction(extraction) if extraction else None,
            "requirements": [payloads.requirement_row(item) for item in requirements],
        }
    )


def _extraction(extraction) -> dict:
    """
    What the model understood, without the raw payload.

    Note:
        `text_image_conflict` is the field worth surfacing. It means the photo does not match
        what the text claimed, which is the signature of recycled imagery — and a coordinator
        who can see that flag will treat the item very differently.
    """
    return {
        "classification": extraction.classification,
        "confidence": extraction.confidence,
        "visual_summary": extraction.visual_summary,
        "text_image_conflict": extraction.text_image_conflict,
        "geocode_query": extraction.geocode_query,
        "model": extraction.model,
        "prompt_version": extraction.prompt_version,
        "created_at": extraction.created_at.isoformat(),
    }
