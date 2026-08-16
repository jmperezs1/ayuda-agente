"""
What every tool needs: one failure shape, and the lookups more than one tool repeats.

A tool never raises *to the model*. An exception reaching the caller becomes an opaque
trace, while a returned value with an `error` and a `hint` is something the model can read
and correct on the next turn.

Inside a tool, argument resolution signals a bad argument by raising `ToolInputError` and
the tool body catches it once. That keeps the guards readable as a straight sequence — and,
unlike returning `(value, error)` pairs, it leaves the resolved values properly typed
rather than perpetually optional.
"""

from ayudagente.radar.models import AdminUnit, Event, Requirement, ResourceType
from ayudagente.radar.services import find_admin_units, resolve_resource, resource_catalog

# A miss should show the whole taxonomy; the cap is a backstop, not an expectation
MAX_CATALOG_ROWS = 40


def failure(error: str, hint: str | None = None, **extra) -> dict:
    """
    Build the shape every failure returns.

    Args:
        error (str): What went wrong, in the model's terms.
        hint (str | None): What to do instead. Omit when the error already says it.
        **extra: Data the caller needs in order to retry — `available`, `candidates`.

    Returns:
        dict: Always carries `error`, so checking for that key is enough to branch on.
    """
    payload = {"error": error}
    if hint:
        payload["hint"] = hint
    payload.update(extra)
    return payload


class ToolInputError(Exception):
    """
    A bad argument, carrying the payload the tool should return.

    Note:
        Never allowed to escape a tool. Each tool catches it and returns `payload`, adding
        whatever empty collection its own contract promises.
    """

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "invalid argument"))
        self.payload = payload


def validate_coordinates(lat: float, lon: float, label: str = "") -> None:
    """
    Reject a point that is not on Earth.

    Raises:
        ToolInputError: When either value is out of range.

    Note:
        PostGIS accepts an impossible latitude and orders results by distance from it
        without complaining, so the reply looks like a valid "nearest first" computed from
        a place that does not exist. Caught here or not at all.
    """
    prefix = f"{label} " if label else ""
    if not -90 <= lat <= 90:
        raise ToolInputError(
            failure(f"{prefix}latitude {lat} is out of range", "latitude runs from -90 to 90")
        )
    if not -180 <= lon <= 180:
        raise ToolInputError(
            failure(f"{prefix}longitude {lon} is out of range", "longitude runs from -180 to 180")
        )


def validate_radius(radius_km: float) -> None:
    """
    Reject a search radius that can contain nothing.

    Raises:
        ToolInputError: When the radius is zero or negative.

    Note:
        Without this the query is well-formed and matches nothing, so an impossible request
        reads as "no results" — and the agent tells a coordinator a place needs nothing.
    """
    if radius_km <= 0:
        raise ToolInputError(
            failure(
                f"radius_km must be greater than zero, got {radius_km}",
                "use a positive distance, or omit it to search the whole area",
            )
        )


def get_event(event_id: int) -> Event:
    """
    Fetch an event by id.

    Raises:
        ToolInputError: When it does not exist. Reported rather than answered with an empty
            list, because "no needs found" and "that event does not exist" lead the agent
            to opposite conclusions.
    """
    event = Event.objects.filter(id=event_id).only("id", "country_code", "status").first()
    if event is None:
        raise ToolInputError(failure(f"event {event_id} does not exist"))
    return event


def require_same_event(event_id: int, row, label: str) -> None:
    """
    Refuse a row belonging to another emergency.

    Args:
        event_id (int): The event the conversation is bound to.
        row: Anything carrying an `event_id` — a requirement, an actor, a frontier node.
        label (str): How to name it in the error.

    Raises:
        ToolInputError: When the row is from a different event.

    Note:
        Ids from a previous conversation, or from a listing the coordinator is looking at on
        another screen, are valid integers that resolve to real rows. Without this the reply
        is about a different disaster and reads exactly like a correct one.
    """
    if row.event_id != event_id:
        raise ToolInputError(
            failure(
                f"{label} {row.id} belongs to another emergency",
                "answer only about the event this conversation is bound to",
            )
        )


def get_requirement(requirement_id: int, event_id: int, *related: str) -> Requirement:
    """
    Fetch one requirement of this event.

    Args:
        requirement_id (int): The row to read.
        event_id (int): The event the conversation is bound to.
        *related: Relations to follow in the same query.

    Raises:
        ToolInputError: When it does not exist, or belongs to another emergency. Reported
            apart because they call for different behaviour: a stale id is the agent's to
            correct, a foreign one is a question it must not answer at all.
    """
    requirement = Requirement.objects.select_related(*related).filter(id=requirement_id).first()
    if requirement is None:
        raise ToolInputError(failure(f"requirement {requirement_id} does not exist"))
    require_same_event(event_id, requirement, "requirement")
    return requirement


def resolve_resource_arg(resource_key: str) -> ResourceType:
    """
    Resolve a resource by slug or display name.

    Raises:
        ToolInputError: With `available` listing the valid keys. The taxonomy is in Spanish
            and lives in a table that changes mid-emergency, so it can be neither
            translated nor frozen into a prompt.
    """
    resource = resolve_resource(resource_key)
    if resource is None:
        raise ToolInputError(
            failure(
                f"unknown resource {resource_key!r}",
                "pick a key from `available`, or omit the argument to search everything",
                available=resource_catalog(limit=MAX_CATALOG_ROWS),
            )
        )
    return resource


def resolve_place_arg(place: str, country_code: str) -> AdminUnit:
    """
    Resolve a place name or national code within one country.

    Raises:
        ToolInputError: With `place_options` when a name matches several units. Place names
            repeat across regions, and picking the first silently routes aid to the wrong
            one — a failure nobody notices until the truck arrives.
    """
    matches = find_admin_units(place, country_code=country_code)
    if not matches:
        raise ToolInputError(
            failure(
                f"no administrative unit matches {place!r}",
                "use the name as the official gazetteer spells it, or its national code",
            )
        )
    if len(matches) > 1:
        raise ToolInputError(
            failure(
                f"{place!r} matches {len(matches)} administrative units",
                "call again with the national code of the one you mean",
                place_options=[
                    {
                        "code": unit.code,
                        "name": unit.name,
                        "level": unit.level,
                        "parent": unit.parent.name if unit.parent else None,
                    }
                    for unit in matches
                ],
            )
        )
    return matches[0]
