"""
Reading filters and paging out of a query string.

Every list endpoint takes the same shape of parameters, and the interesting decision is what
to do with a bad one. A filter that is silently ignored returns a plausible page of the wrong
rows, and nobody notices until the numbers are used. So an unknown value is a 400 naming what
was expected, and only a *missing* parameter falls back to a default.
"""

from collections.abc import Sequence
from functools import wraps

from django.contrib.gis.geos import Point, Polygon
from django.db import models
from django.db.models import QuerySet
from django.http import JsonResponse

# Only reached by a client that asks for a page; without `limit` the whole set comes back
MAX_LIMIT = 500
DEFAULT_RADIUS_KM = 25.0

EARTH_SRID = 4326


class QueryError(Exception):
    """A malformed query string. Turned into a 400 by `reports_query_errors`."""


def reports_query_errors(view):
    """Turn a `QueryError` raised anywhere inside a view into a 400 with its message."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except QueryError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapper


def paginate(queryset: QuerySet, request) -> tuple[list, dict]:
    """
    Slice a queryset by `limit` and `offset`, returning everything when neither is asked for.

    Args:
        queryset (QuerySet): Must already be ordered, or the page is not reproducible.
        request: Carries the parameters.

    Returns:
        tuple[list, dict]: The rows, and the envelope fields to merge into the response.

    Raises:
        QueryError: On a non-numeric or negative value.

    Note:
        A page is opt-in. Defaulting to a hundred rows meant a client that never sent `limit`
        drew a hundred of five hundred requirements and had no way to know from the map that
        it was missing four fifths of them — the `count` said so and nobody read it.

        `limit` still works and is still capped, so a client that wants pages gets pages. The
        envelope reports what was actually returned rather than the cap that was asked for.
    """
    offset = integer(request, "offset", 0)
    requested = integer(request, "limit", 0)
    limit = min(requested, MAX_LIMIT) if requested else None

    rows = list(queryset[offset : offset + limit] if limit else queryset[offset:])
    return rows, {"count": queryset.count(), "limit": limit or len(rows), "offset": offset}


def integer(request, name: str, default: int) -> int:
    """
    One non-negative integer parameter.

    Raises:
        QueryError: When it is present but not a non-negative integer.
    """
    raw = request.GET.get(name)
    if raw is None:
        return default
    if not raw.isdigit():
        raise QueryError(f"{name} must be a non-negative integer, got {raw!r}")
    return int(raw)


def decimal(request, name: str) -> float | None:
    """
    One float parameter, or None when absent.

    Raises:
        QueryError: When it is present but not a number.
    """
    raw = request.GET.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise QueryError(f"{name} must be a number, got {raw!r}") from exc


def choices(
    request,
    name: str,
    allowed: type[models.TextChoices],
    default: Sequence[str] | None = None,
) -> list[str] | None:
    """
    A repeatable parameter constrained to an enumeration.

    Repeat the parameter to widen the filter — `?status=open&status=partial` — rather than
    comma-separating, so a value that legitimately contains a comma stays possible.

    Args:
        request: Carries the parameters.
        name (str): Parameter name.
        allowed (type[models.TextChoices]): The enumeration the values must belong to.
        default (Sequence[str] | None): Used when the parameter is absent.

    Returns:
        list[str] | None: The requested values, or `default`.

    Raises:
        QueryError: On any value outside the enumeration.
    """
    values = request.GET.getlist(name)
    if not values:
        return list(default) if default else None

    valid = set(allowed.values)
    unknown = [value for value in values if value not in valid]
    if unknown:
        raise QueryError(f"unknown {name}: {', '.join(unknown)}. Expected {sorted(valid)}")
    return values


def bbox(request) -> Polygon | None:
    """
    The `bbox=minLon,minLat,maxLon,maxLat` filter, as a polygon ready for a spatial lookup.

    Returns:
        Polygon | None: None when the parameter is absent, which means no spatial filter.

    Raises:
        QueryError: On the wrong number of parts, a non-numeric part, or reversed corners.
    """
    raw = request.GET.get("bbox")
    if raw is None:
        return None

    parts = raw.split(",")
    if len(parts) != 4:
        raise QueryError("bbox takes four numbers: minLon,minLat,maxLon,maxLat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as exc:
        raise QueryError(f"bbox must be four numbers, got {raw!r}") from exc

    if min_lon >= max_lon or min_lat >= max_lat:
        raise QueryError("bbox corners are reversed: expected minLon,minLat,maxLon,maxLat")
    return Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))


def near(request) -> tuple[Point, float] | None:
    """
    The `near=lat,lon` filter with its `radius_km`, for "what is around this pin".

    Returns:
        tuple[Point, float] | None: Centre and radius in kilometres, or None when absent.

    Raises:
        QueryError: On a malformed centre or a non-positive radius.

    Note:
        `near` is written latitude first because that is the order a person reads off a map
        and pastes from Google Maps. `bbox` is longitude first because that is the order every
        geospatial tool emits. Neither convention is worth fighting, so each is documented
        where it is used.
    """
    raw = request.GET.get("near")
    if raw is None:
        return None

    parts = raw.split(",")
    if len(parts) != 2:
        raise QueryError("near takes two numbers: lat,lon")
    try:
        latitude, longitude = (float(part) for part in parts)
    except ValueError as exc:
        raise QueryError(f"near must be two numbers, got {raw!r}") from exc

    # Explicit None check: `or` would read a deliberate 0 as absent and silently widen it
    radius_km = decimal(request, "radius_km")
    if radius_km is None:
        radius_km = DEFAULT_RADIUS_KM
    if radius_km <= 0:
        raise QueryError("radius_km must be greater than zero")
    return Point(longitude, latitude, srid=EARTH_SRID), radius_km
