"""
What the loop did while nobody was watching.

Everything else in this API answers "what does the emergency look like". This answers "is the
machine still working", which is a different question and the only one that matters at eight
in the morning after a night of unattended harvesting.

Note:
    `rationale` is the point. It is the agent's own record of why it spent a pass on that
    target, written to be read by a person, and until now nothing could show it. A dashboard
    that lists jobs without it shows activity; one that includes it shows reasoning.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from ayudagente.radar.choices import DecisionSource, JobStatus
from ayudagente.radar.models import Event, HarvestJob
from ayudagente.radar.services.pacing import recent_novelty, should_decide
from ayudagente.radar.views import payloads, query


@require_GET
@query.reports_query_errors
def job_list(request, event_id: int):
    """
    Harvest jobs for one event, newest first.

    Accepts `status` and `platform` (both repeatable), `decided_by`, `limit` and `offset`.
    """
    event = get_object_or_404(Event, id=event_id)
    queryset = HarvestJob.objects.filter(event=event)

    for parameter, allowed, field in (
        ("status", JobStatus, "status__in"),
        ("decided_by", DecisionSource, "decided_by__in"),
    ):
        values = query.choices(request, parameter, allowed)
        if values:
            queryset = queryset.filter(**{field: values})

    platforms = request.GET.getlist("platform")
    if platforms:
        queryset = queryset.filter(platform__in=platforms)

    queryset = queryset.select_related("node__admin_unit", "node__actor").order_by("-created_at")

    rows, envelope = query.paginate(queryset, request)
    return JsonResponse({**envelope, "results": [_job(job) for job in rows]})


@require_GET
def loop_status(request, event_id: int):
    """
    Whether the perpetual loop is working, and what it will do next.

    Note:
        `next_round` carries the pacing verdict verbatim. A loop that is deliberately waiting
        and one that is broken look identical from the outside — both are quiet — and the
        difference is a sentence this endpoint already has.
    """
    event = get_object_or_404(Event, id=event_id)
    verdict = should_decide(event)
    jobs = HarvestJob.objects.filter(event=event)

    return JsonResponse(
        {
            "event": event.id,
            "status": event.status,
            "harvestable": event.is_harvestable,
            "spent_usd": float(event.spent_usd),
            "next_round": {"will_run": verdict.proceed, "reason": verdict.reason},
            "novelty": recent_novelty(event),
            "jobs": {status: jobs.filter(status=status).count() for status in JobStatus.values},
            "last_harvest_at": payloads.timestamp(
                jobs.filter(finished_at__isnull=False)
                .order_by("-finished_at")
                .values_list("finished_at", flat=True)
                .first()
            ),
        }
    )


def _job(job: HarvestJob) -> dict:
    """
    One harvest job, with the reasoning that produced it.

    Note:
        `actor_input` is included whole. It is the exact payload sent to Apify, and the first
        live run turned on being able to read it: three Actors silently ignored an invented
        field and nothing in the system could show what had actually been asked for.
    """
    target = job.node.admin_unit or job.node.actor if job.node else None

    return {
        "id": job.id,
        "platform": job.platform,
        "target_kind": job.target_kind,
        "target": str(target) if target else None,
        "status": job.status,
        "decided_by": job.decided_by,
        "rationale": job.rationale,
        "apify_actor": job.apify_actor,
        "actor_input": job.actor_input,
        "items_returned": job.items_returned,
        "items_new": job.items_new,
        "cost_usd": float(job.actual_cost_usd),
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "finished_at": payloads.timestamp(job.finished_at),
    }
