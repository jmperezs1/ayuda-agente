"""
Turning the place a post named into a point, with an honest record of how fine it is.

The strings arriving here range from "Carrera 28 #42-15, Teusaquillo, Bogotá" to "Eje
Cafetero" to bare "Perú". They cannot be treated alike: matching a truck to a street address
is useful, matching it to a whole coffee-growing region is a lie dressed as a coordinate. So
every result carries the precision Google reported, and callers enforce a minimum.
"""

import requests
from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from ayudagente.radar.choices import AdminLevel, GeocodeSource, LocationPrecision
from ayudagente.radar.models import AdminUnit, Event, Location
from ayudagente.radar.services.text import normalize

GOOGLE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

# A municipality's centroid can sit well away from its edge, so the reach is generous
UNIT_REACH_KM = 50

# Google's place types, coarse to fine. The first match wins, so order matters.
PRECISION_BY_TYPE = (
    ("street_address", LocationPrecision.STREET_ADDRESS),
    ("premise", LocationPrecision.STREET_ADDRESS),
    ("subpremise", LocationPrecision.STREET_ADDRESS),
    ("route", LocationPrecision.STREET_ADDRESS),
    ("intersection", LocationPrecision.STREET_ADDRESS),
    ("neighborhood", LocationPrecision.NEIGHBORHOOD),
    ("sublocality", LocationPrecision.NEIGHBORHOOD),
    ("locality", LocationPrecision.ADMIN_2),
    ("administrative_area_level_2", LocationPrecision.ADMIN_2),
    ("administrative_area_level_1", LocationPrecision.ADMIN_1),
    ("country", LocationPrecision.COUNTRY),
)


class Geocoder:
    """
    Resolves place strings, reusing anything already resolved.

    Note:
        The `Location` table is the cache. A frequently named place — a stadium, a collection
        point — appears in dozens of posts, and paying Google once per post would be both slow
        and billed. Lookups are keyed on the normalized text, so the second post naming the
        same place costs a database hit.

        A miss returns None rather than a country centroid. An unresolvable string is not
        worth a coordinate: it would enter the graph looking exactly like a real location and
        pull matches toward a point nobody mentioned.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 10.0):
        self.api_key = api_key if api_key is not None else settings.GOOGLE_GEOCODING_API_KEY
        self.timeout = timeout

    def resolve(self, query: str, event: Event) -> Location | None:
        """
        Resolve one place string into a stored `Location`.

        Args:
            query (str): The place as the extraction wrote it.
            event (Event): Supplies the country used to bias the search.

        Returns:
            Location | None: The stored location, or None when the string is empty or
                unresolvable.
        """
        if not query.strip():
            return None

        text_norm = normalize(query)
        cached = Location.objects.filter(text_norm=text_norm).first()
        if cached:
            return cached

        result = self.lookup(query, event.country_code)
        if result is None:
            return None

        coordinates = result["geometry"]["location"]
        point = Point(coordinates["lng"], coordinates["lat"], srid=4326)
        location, _ = Location.objects.get_or_create(
            text_norm=text_norm,
            admin_unit=unit_for(point, event.country_code),
            defaults={
                "point": point,
                "precision": self.precision_of(result),
                "raw_text": query[:300],
                "source": GeocodeSource.GOOGLE,
                "confidence": self.confidence_of(result),
                "raw_response": result,
            },
        )
        return location

    def lookup(self, query: str, country_code: str) -> dict | None:
        """
        Ask Google for one place, biased to the event's country.

        Args:
            query (str): The place string.
            country_code (str): ISO 3166-1 alpha-2 code of the event's country.

        Returns:
            dict | None: The first result, or None when Google found nothing.

        Raises:
            RuntimeError: If the API reports a key, quota or request problem, which is a
                configuration failure worth stopping on rather than silently dropping every
                location in a harvest.
        """
        params = {"address": query, "key": self.api_key}
        if country_code:
            params["components"] = f"country:{country_code}"

        response = requests.get(GOOGLE_ENDPOINT, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        status = payload.get("status")
        if status == "ZERO_RESULTS":
            return None
        if status != "OK":
            raise RuntimeError(f"geocoding failed: {status} {payload.get('error_message', '')}")
        return payload["results"][0]

    def precision_of(self, result: dict) -> str:
        """
        Read how fine a result is, which decides what it may be matched against.

        Args:
            result (dict): One Google result.

        Returns:
            str: A `LocationPrecision` value. Falls back to `ADMIN_2` for the place types
                Google has no administrative label for — a named business or landmark sits
                at roughly city precision unless its geometry says otherwise.
        """
        if result.get("geometry", {}).get("location_type") == "ROOFTOP":
            return LocationPrecision.EXACT_POINT

        types = set(result.get("types", []))
        for name, precision in PRECISION_BY_TYPE:
            if name in types:
                return precision
        return LocationPrecision.ADMIN_2

    def confidence_of(self, result: dict) -> float:
        """
        Score how much to trust the point.

        Args:
            result (dict): One Google result.

        Returns:
            float: Derived from Google's own `location_type`, and lowered when the result is
                partial — a partial match means Google reinterpreted the query, which is
                exactly when a coordinate looks confident and is not.
        """
        by_location_type = {
            "ROOFTOP": 1.0,
            "RANGE_INTERPOLATED": 0.9,
            "GEOMETRIC_CENTER": 0.8,
            "APPROXIMATE": 0.6,
        }
        score = by_location_type.get(result.get("geometry", {}).get("location_type"), 0.6)
        return score * 0.7 if result.get("partial_match") else score


def unit_for(point: Point, country_code: str) -> "AdminUnit | None":
    """
    The municipality a geocoded point falls in, according to the gazetteer we hold.

    Args:
        point (Point): Where the geocoder placed the string.
        country_code (str): Narrows the search to the country the event is in.

    Returns:
        AdminUnit | None: The nearest second-level unit within reach, or None when the
            country has no gazetteer loaded or nothing is close enough.

    Note:
        Every geocoded `Location` used to be stored with `admin_unit=None`, and three
        different consumers read that link: the agent's place filter returned nothing for any
        place, identity resolution lost its blocking by municipality, and the frontier could
        not promote an account by where it posts. A point with no municipality is still a
        point, so failing to match one is left as None rather than raised.
    """
    return (
        AdminUnit.objects.filter(country_code=country_code, level=AdminLevel.ADMIN_2)
        .filter(centroid__isnull=False, centroid__distance_lte=(point, D(km=UNIT_REACH_KM)))
        .annotate(separation=Distance("centroid", point))
        .order_by("separation")
        .first()
    )
