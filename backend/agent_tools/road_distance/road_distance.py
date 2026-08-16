"""
`road_distance` as an agent tool.

The only tool besides `plan_trip_stops` that leaves the process, so it is the only one that
can fail for reasons having nothing to do with the request. OSRM's public server is rate
limited and will time out during a demo; that comes back as a value telling the agent to
fall back on the straight-line distance it already has, not as a stack trace.
"""

from django.contrib.gis.geos import Point
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import ToolInputError, failure, get_requirement
from ayudagente.radar.services import RoutingError
from ayudagente.radar.services import road_distance as road_distance_service


class RoadDistanceInput(BaseModel):
    """Arguments for `road_distance`. Give either two requirement ids or two coordinates."""

    event_id: int = Field(description="The emergency this conversation is bound to.")
    from_requirement_id: int | None = Field(
        default=None, description="Start at this requirement's location."
    )
    to_requirement_id: int | None = Field(
        default=None, description="Finish at this requirement's location."
    )
    from_lat: float | None = Field(default=None, description="Start latitude, if no id.")
    from_lon: float | None = Field(default=None, description="Start longitude, if no id.")
    to_lat: float | None = Field(default=None, description="Destination latitude, if no id.")
    to_lon: float | None = Field(default=None, description="Destination longitude, if no id.")


def _point_for(
    event_id: int, requirement_id: int | None, lat: float | None, lon: float | None, label: str
) -> Point:
    """
    Resolve one endpoint from a requirement id or a coordinate pair.

    Raises:
        ToolInputError: When the requirement is missing or belongs to another emergency, or
            when only half a coordinate pair was given.
    """
    if requirement_id is not None:
        return get_requirement(requirement_id, event_id, "location").location.point

    if lat is None or lon is None:
        raise ToolInputError(
            failure(
                f"{label} endpoint is incomplete",
                f"give {label}_requirement_id, or both {label}_lat and {label}_lon",
            )
        )
    return Point(lon, lat, srid=4326)


@tool("road_distance", args_schema=RoadDistanceInput)
def road_distance(
    event_id: int,
    from_requirement_id: int | None = None,
    to_requirement_id: int | None = None,
    from_lat: float | None = None,
    from_lon: float | None = None,
    to_lat: float | None = None,
    to_lon: float | None = None,
) -> dict:
    """
    Driving distance and time between two points, over the real road network.

    Use this before committing to a delivery. The distances other tools report are
    straight-line: in mountainous terrain a lake or a ridge can double the real drive, and
    a pairing that looks close can be a five-hour trip.

    Give two requirement ids, or two coordinate pairs. Returns `distance_km` and
    `duration_min`.

    If routing is unavailable the reply carries `error` with `fallback: "geodesic"` — that
    means the straight-line distance you already have is the best estimate available, and
    it is a floor, never an overestimate. Say so rather than presenting it as a drive.
    """
    try:
        origin = _point_for(event_id, from_requirement_id, from_lat, from_lon, "from")
        destination = _point_for(event_id, to_requirement_id, to_lat, to_lon, "to")
    except ToolInputError as exc:
        return exc.payload

    try:
        route = road_distance_service(origin, destination)
    except (RoutingError, OSError) as exc:
        return failure(
            f"routing unavailable: {exc}",
            "use the straight-line distance and say it is an underestimate",
            fallback="geodesic",
        )

    # The geometry is hundreds of coordinate pairs — it belongs on a map, not in a prompt
    return {"distance_km": route["distance_km"], "duration_min": route["duration_min"]}
