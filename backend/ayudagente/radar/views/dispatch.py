"""
The one place this API writes, and the only thing a human does.

Everything else here reads. Nothing is ever sent by the system — a drafted message resolves to
a `wa.me` or `mailto:` link that a person clicks, and that click is the entire dispatch
mechanism. This endpoint records it.

Note:
    We cannot observe whether the message was actually sent. WhatsApp opens in another app and
    never reports back. What we *can* observe is the click on our own button, which is why the
    frontend calls this as it opens the link rather than after.

    Recording it is not bookkeeping. `covered_quantity` counts from `contacted` onward, so an
    unrecorded dispatch leaves a site looking untouched and the system proposes it to the next
    ten people. Saturation counting is the reason this endpoint exists.
"""

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ayudagente.radar.choices import OutreachStatus
from ayudagente.radar.models import Outreach
from ayudagente.radar.views import payloads

# What a human may set by hand. Everything else is the system's to decide.
HUMAN_STATUSES = frozenset({OutreachStatus.DISPATCHED, OutreachStatus.DISMISSED})


@csrf_exempt
@require_POST
def dispatch_outreach(request, outreach_id: int):
    """
    Record that a person acted on a drafted message.

    Expects `{"status": "dispatched"}` or `{"status": "dismissed"}`. Defaults to dispatched,
    because opening the link is what the button does.

    Returns:
        JsonResponse: The updated draft, or 400 with the statuses a human may set.

    Note:
        Idempotent. A double click, a retried request and a browser that fired the handler
        twice all leave one dispatch — the timestamp is only written the first time, so the
        record says when the person acted rather than when they last tapped.
    """
    outreach = get_object_or_404(
        Outreach.objects.select_related("target_actor", "contact_point"), id=outreach_id
    )

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body must be JSON"}, status=400)

    status = payload.get("status", OutreachStatus.DISPATCHED)
    if status not in HUMAN_STATUSES:
        return JsonResponse(
            {"error": f"status must be one of {sorted(HUMAN_STATUSES)}"}, status=400
        )

    if outreach.status == OutreachStatus.DRAFT:
        outreach.status = status
        if status == OutreachStatus.DISPATCHED:
            outreach.dispatched_at = timezone.now()
            if request.user.is_authenticated:
                outreach.dispatched_by = request.user
        outreach.save(update_fields=["status", "dispatched_at", "dispatched_by"])

    return JsonResponse(payloads.outreach_row(outreach))
