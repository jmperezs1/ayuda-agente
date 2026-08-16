"""
Real road distances and multi-stop trip ordering, via OSRM.

Straight-line distance lies in this geography — a lake or a mountain range between two
points can multiply the real driving distance. OSRM answers over OpenStreetMap's actual
road network. The public server is fine for hackathon volume; self-host later by pointing
`OSRM_BASE_URL` at a local container fed with the Colombia extract.
"""

import json
import os
import urllib.parse
import urllib.request

from django.contrib.gis.geos import Point

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")


class RoutingError(Exception):
    """OSRM could not produce a route between the given points."""


def _osrm(service: str, coords: list[Point], params: dict) -> dict:
    path = ";".join(f"{p.x},{p.y}" for p in coords)
    url = f"{OSRM_BASE_URL}/{service}/v1/driving/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != "Ok":
        raise RoutingError(f"OSRM {service} returned {payload.get('code')}")
    return payload


def road_distance(origin: Point, destination: Point) -> dict:
    """
    Real driving distance and time between two points.

    Returns:
        dict: `distance_km`, `duration_min`, and `geometry` ([lon, lat] pairs) for maps.
    """
    payload = _osrm(
        "route",
        [origin, destination],
        {"overview": "simplified", "geometries": "geojson"},
    )
    route = payload["routes"][0]
    return {
        "distance_km": round(route["distance"] / 1000, 1),
        "duration_min": round(route["duration"] / 60, 1),
        "geometry": route["geometry"]["coordinates"],
    }


def plan_trip_stops(stops: list[dict]) -> dict:
    """
    Order pickups and dropoffs into the optimal driving sequence.

    OSRM's `trip` service solves the stop ordering over real roads; the caller decides
    *what* goes on the truck, this decides *in which order* to visit.

    Args:
        stops: Each `{'point': Point, 'label': str}`. First stop is treated as the start.

    Returns:
        dict: `ordered_stops` (input dicts, visit order), `total_km`, `total_min`,
            `geometry` for map rendering.
    """
    if len(stops) < 2:
        raise ValueError("a trip needs at least two stops")

    payload = _osrm(
        "trip",
        [s["point"] for s in stops],
        {
            "source": "first",
            "roundtrip": "false",
            "overview": "simplified",
            "geometries": "geojson",
        },
    )
    trip = payload["trips"][0]
    order = sorted(range(len(stops)), key=lambda i: payload["waypoints"][i]["waypoint_index"])
    return {
        "ordered_stops": [stops[i] for i in order],
        "total_km": round(trip["distance"] / 1000, 1),
        "total_min": round(trip["duration"] / 60, 1),
        "geometry": trip["geometry"]["coordinates"],
    }
