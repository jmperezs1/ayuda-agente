"""
The resource catalog, which the frontend needs to build a filter menu.

Not paged and not scoped to an event: it is a couple of dozen rows shared by every emergency,
and it changes when someone edits the taxonomy, not while a disaster runs.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ayudagente.radar.models import ResourceType
from ayudagente.radar.views import payloads


@require_GET
def resource_type_list(request):
    """
    Every resource type, parents before children.

    Note:
        Keys are English and stable; `name` is the label to show and is Spanish, because it
        reaches an end user. Filter on `key`, never on `name` — the label is a product
        decision and will be rewritten.
    """
    resources = ResourceType.objects.select_related("parent").order_by("parent__key", "key")
    return JsonResponse(
        {"resource_types": [payloads.resource_type(resource) for resource in resources]}
    )
