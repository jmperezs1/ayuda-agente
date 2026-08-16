"""
Queries over supply and demand.

These are read tools: they never mutate state. Matching compatibility walks the
`ResourceType` hierarchy so a need for "colchonetas" can be met by an offer of "cobijas y
ropa de cama" when nothing closer exists.
"""

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    IntegerField,
    Q,
    QuerySet,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from slugify import slugify

from ayudagente.radar.choices import (
    Direction,
    RequirementStatus,
    Urgency,
    precisions_at_least,
)
from ayudagente.radar.models import AdminUnit, Requirement, ResourceType

OPEN_STATUSES = (
    RequirementStatus.OPEN,
    RequirementStatus.PARTIAL,
    RequirementStatus.UNVERIFIED,
)

# Below this a trigram word match is coincidence, not a hit
TEXT_MATCH_THRESHOLD = 0.4


def urgency_rank() -> Case:
    """
    An orderable integer for `urgency`, most urgent first.

    Returns:
        Case: 0 for the first `Urgency` member, 1 for the next, and so on.

    Note:
        `urgency` is a CharField, so `order_by("-urgency")` sorts alphabetically and puts
        `critical` *last*, behind `medium` and `low`. Combined with a row limit, that hides
        exactly the rows that matter most, and hides them silently. The ranking is derived
        from the enum's declaration order so adding a level cannot desynchronize it.
    """
    return Case(
        *[When(urgency=value, then=Value(rank)) for rank, value in enumerate(Urgency.values)],
        default=Value(len(Urgency.values)),
        output_field=IntegerField(),
    )


def resolve_resource(text: str) -> ResourceType | None:
    """
    Find a resource from whatever a caller wrote: its slug, or its display name.

    Three attempts in one query — exact slug ignoring case, the text slugified
    ("Agua Potable" → `agua_potable`), and the display name ignoring case and accents.

    Args:
        text (str): A slug or a human-readable name.

    Returns:
        ResourceType | None: None when nothing matches, which the caller must handle —
            silently searching every resource instead would answer a question nobody asked.

    Note:
        This is a lookup, not a guess. It will not translate: an English word never finds a
        Spanish resource, and it should not, because the near-miss it would have to accept
        to do so would also match the wrong resource. Callers offer the catalog instead.
    """
    normalized = text.strip()
    if not normalized:
        return None

    return ResourceType.objects.filter(
        Q(key__iexact=normalized)
        | Q(key__iexact=slugify(normalized, separator="_"))
        | Q(name__unaccent__iexact=normalized)
    ).first()


def resource_catalog(limit: int | None = None) -> list[dict]:
    """
    Every resource as key and name, ordered.

    Returns:
        list[dict]: Small enough to put in a prompt or an error hint — the taxonomy is
            tens of rows, not thousands, because extraction maps onto it rather than
            extending it.
    """
    qs = ResourceType.objects.values("key", "name").order_by("key")
    return list(qs[:limit] if limit else qs)


def find_admin_units(text: str, country_code: str = "") -> list[AdminUnit]:
    """
    Every administrative unit matching a name or a national code.

    Args:
        text (str): "Quibdó", "quibdo" or "27001".
        country_code (str): ISO 3166-1 alpha-2. Always pass the event's — `code` is only
            unique per country and level, so a bare code is ambiguous across the gazetteer.

    Returns:
        list[AdminUnit]: Usually one. Names repeat across regions — Colombia has several
            Santa Rosa — so the caller must handle more than one rather than take the
            first. Sending aid to the wrong Santa Rosa is worse than asking which was meant.
    """
    normalized = text.strip()
    if not normalized:
        return []

    qs = AdminUnit.objects.filter(
        Q(code=normalized)
        | Q(name_norm__iexact=slugify(normalized, separator=" "))
        | Q(name__unaccent__iexact=normalized)
    )
    if country_code:
        qs = qs.filter(country_code=country_code)

    return list(qs.select_related("parent").order_by("level", "code"))


def admin_unit_family(unit: AdminUnit) -> set[int]:
    """
    An administrative unit and everything below it.

    Returns:
        set[int]: Asking for a first-level unit has to reach the requirements sitting in
            its second-level children, which is where locations actually resolve.
    """
    ids = {unit.id}
    frontier = [unit.id]
    while frontier:
        children = AdminUnit.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
        frontier = [child_id for child_id in children if child_id not in ids]
        ids.update(frontier)
    return ids


def routable(qs: QuerySet[Requirement], only_unsaturated: bool = True) -> QuerySet[Requirement]:
    """
    Narrow a requirement queryset to rows that may still receive supply or attention.

    Four conditions, each there because ignoring it causes a specific harm: a closed time
    window means the collection centre already shut its doors; a merged actor is a
    duplicate that would be counted twice toward saturation; a saturated requirement keeps
    pulling people toward a site that already has enough.

    Args:
        qs (QuerySet[Requirement]): Any requirement queryset.
        only_unsaturated (bool): Drop requirements already fully covered.

    Returns:
        QuerySet[Requirement]: The subset still worth acting on.

    Note:
        The saturation clause mirrors `Requirement.is_saturated`, which cannot run in the
        database. The property's `quantity is None` branch checks for covered or expired
        statuses, and `OPEN_STATUSES` has already removed both, so the SQL only has to
        handle the stated-quantity case.
    """
    qs = qs.filter(status__in=OPEN_STATUSES)

    # A merged actor was absorbed into another and must not surface as its own opportunity
    qs = qs.filter(actor__merged_into__isnull=True)

    qs = qs.filter(Q(window_end__isnull=True) | Q(window_end__gte=timezone.now()))

    if only_unsaturated:
        qs = qs.exclude(Q(quantity__isnull=False) & Q(covered_quantity__gte=F("quantity")))
    return qs


def resource_family(resource: ResourceType) -> set[int]:
    """
    IDs of every resource compatible with the given one: itself, ancestors, descendants.

    An offer of the parent category can satisfy a need for the specific item, and a
    specific offer can satisfy a generic need.
    """
    ids = {resource.id}
    ids.update(ancestor.id for ancestor in resource.ancestors())

    frontier = [resource.id]
    while frontier:
        children = ResourceType.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
        frontier = [child_id for child_id in children if child_id not in ids]
        ids.update(frontier)
    return ids


def find_requirements(
    event_id: int,
    direction: str,
    resource: ResourceType | None = None,
    admin_unit: AdminUnit | None = None,
    text: str | None = None,
    near: Point | None = None,
    radius_km: float | None = None,
    min_precision: str | None = None,
    only_unsaturated: bool = True,
    limit: int = 50,
) -> QuerySet[Requirement]:
    """
    Open requirements matching direction and resource family, most urgent first.

    Args:
        event_id (int): The event to search within — requirements never cross events.
        direction (str): `Direction.NEEDS` or `Direction.OFFERS`.
        resource (ResourceType | None): When given, expands to its whole compatible family.
        admin_unit (AdminUnit | None): Restrict to this unit and everything below it, so a
            first-level unit reaches the second-level ones where locations resolve.
        text (str | None): Free-text match over `free_text`, for the specificity the coarse
            taxonomy deliberately does not carry ("leche de fórmula" under `alimentos`).
        near (Point | None): Reference point; results are annotated with `distance_m` and
            ordered by it instead of by urgency.
        radius_km (float | None): With `near`, a hard cutoff using the spatial index.
        min_precision (str | None): Minimum `LocationPrecision` — filters out the dots that
            cover a whole region.
        only_unsaturated (bool): Drop requirements already fully covered.
        limit (int): Row cap. The result is sliced and cannot be filtered further.

    Returns:
        QuerySet[Requirement]: Sliced and ready to iterate.

    Raises:
        ValueError: On an unknown direction or an unknown precision.

    Note:
        `text` filters and never reorders. Ranking by textual similarity would bury a
        critical need under a chattier post that happened to phrase things closer to the
        query.
    """
    if direction not in Direction.values:
        raise ValueError(f"unknown direction {direction!r}; expected one of {Direction.values}")

    qs = routable(
        Requirement.objects.filter(event_id=event_id, direction=direction),
        only_unsaturated=only_unsaturated,
    ).select_related("resource", "actor", "location", "location__admin_unit", "destination")

    if resource is not None:
        qs = qs.filter(resource_id__in=resource_family(resource))

    if admin_unit is not None:
        qs = qs.filter(location__admin_unit_id__in=admin_unit_family(admin_unit))

    if text:
        # Substring for the exact phrasing, trigram words for the misspelling of it
        qs = qs.annotate(text_match=TrigramWordSimilarity(text, "free_text")).filter(
            Q(free_text__unaccent__icontains=text) | Q(text_match__gte=TEXT_MATCH_THRESHOLD)
        )

    if min_precision is not None:
        qs = qs.filter(location__precision__in=precisions_at_least(min_precision))

    if near is not None:
        if radius_km is not None:
            qs = qs.filter(location__point__dwithin=(near, D(km=radius_km)))
        qs = qs.annotate(distance_m=Distance("location__point", near)).order_by("distance_m")
    else:
        qs = qs.annotate(urgency_order=urgency_rank()).order_by("urgency_order", "-confidence")

    return qs[:limit]


def get_balance(
    event_id: int,
    resource: ResourceType | None = None,
    admin_unit: AdminUnit | None = None,
) -> list[dict]:
    """
    Net deficit or surplus per resource, place and unit.

    One row per `(resource, admin unit, unit)` carrying what is needed, what is offered and
    the difference — the view behind "what is happening here", and the entry point that
    tells a caller which `resource_key` values are worth searching for.

    Args:
        event_id (int): The event to aggregate.
        resource (ResourceType | None): Restrict to one resource family.
        admin_unit (AdminUnit | None): Restrict to a unit and everything below it.

    Returns:
        list[dict]: `net` is negative when demand exceeds supply.

    Note:
        Rows pass through `routable`, the same gate `find_requirements` uses. Without it the
        two disagree — the balance counts a closed centre that the detail view hides, and
        the caller reads a deficit it cannot act on.

        Requirements with no stated quantity are counted separately rather than folded in
        as zero. Ten needs of unknown size are not a covered place, and summing them into
        `0` reads exactly like one.

        Grouping includes `unit` on purpose: two hundred litres and thirty bottles of the
        same resource are not two hundred and thirty of anything.
    """
    qs = routable(Requirement.objects.filter(event_id=event_id))
    if resource is not None:
        qs = qs.filter(resource_id__in=resource_family(resource))
    if admin_unit is not None:
        qs = qs.filter(location__admin_unit_id__in=admin_unit_family(admin_unit))

    rows = (
        qs.values(
            "resource_id",
            "resource__key",
            "resource__name",
            "direction",
            "unit",
            "location__admin_unit_id",
            "location__admin_unit__name",
        )
        .annotate(
            outstanding=Coalesce(
                Sum(F("quantity") - F("covered_quantity"), filter=Q(quantity__isnull=False)),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            requirements=Count("id"),
            unknown_quantity=Count("id", filter=Q(quantity__isnull=True)),
        )
        .order_by("resource__name", "location__admin_unit__name")
    )

    balance: dict[tuple, dict] = {}
    for row in rows:
        key = (row["resource_id"], row["location__admin_unit_id"], row["unit"])
        entry = balance.setdefault(
            key,
            {
                "resource_id": row["resource_id"],
                "resource_key": row["resource__key"],
                "resource": row["resource__name"],
                "admin_unit_id": row["location__admin_unit_id"],
                "admin_unit": row["location__admin_unit__name"],
                "unit": row["unit"] or None,
                "needed": 0,
                "offered": 0,
                "needs": 0,
                "offers": 0,
                "unknown_quantity": 0,
            },
        )
        is_need = row["direction"] == Direction.NEEDS
        entry["needed" if is_need else "offered"] = row["outstanding"]
        entry["needs" if is_need else "offers"] = row["requirements"]
        entry["unknown_quantity"] += row["unknown_quantity"]

    for entry in balance.values():
        entry["net"] = entry["offered"] - entry["needed"]

    return sorted(balance.values(), key=lambda e: (e["resource"], e["admin_unit"] or ""))
