"""
Tests for turning a place string into a point.

The precision mapping and the cache run without touching Google; the one test that calls the
real API is marked `live`.
"""

from datetime import UTC, datetime

import pytest

from ayudagente.radar.choices import GeocodeSource, LocationPrecision
from ayudagente.radar.models import Event, Location
from ayudagente.radar.services.geocoding import Geocoder
from ayudagente.radar.services.text import normalize


def result(types, location_type="APPROXIMATE", partial=False):
    """Build the shape of a Google result, with only the fields the code reads."""
    payload = {
        "types": types,
        "geometry": {"location": {"lat": 4.81, "lng": -75.69}, "location_type": location_type},
    }
    if partial:
        payload["partial_match"] = True
    return payload


@pytest.fixture
def event(db):
    return Event.objects.create(
        hazard="earthquake",
        name="Test event",
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        country_code="CO",
        languages=["es"],
        detection_source="manual",
    )


class TestNormalization:
    """The cache key has to survive the spelling the model happened to use."""

    def test_accents_and_case_collapse(self):
        assert normalize("Quibdó") == normalize("QUIBDO ")

    def test_inner_whitespace_collapses(self):
        assert normalize("Pereira,   Risaralda") == "pereira, risaralda"


class TestPrecision:
    """Precision decides what a location may be matched against, so it cannot be guessed."""

    def test_a_rooftop_hit_is_an_exact_point(self):
        assert Geocoder().precision_of(result(["street_address"], "ROOFTOP")) == (
            LocationPrecision.EXACT_POINT
        )

    def test_a_neighborhood_is_not_promoted_to_an_address(self):
        assert Geocoder().precision_of(result(["neighborhood", "political"])) == (
            LocationPrecision.NEIGHBORHOOD
        )

    def test_a_city_lands_at_second_level(self):
        assert Geocoder().precision_of(result(["locality", "political"])) == (
            LocationPrecision.ADMIN_2
        )

    def test_a_country_stays_a_country(self):
        assert Geocoder().precision_of(result(["country"])) == LocationPrecision.COUNTRY

    def test_an_unlabelled_place_falls_back_rather_than_claiming_precision(self):
        assert Geocoder().precision_of(result(["establishment"])) == LocationPrecision.ADMIN_2


class TestConfidence:
    """A partial match is where a coordinate looks certain and is not."""

    def test_a_rooftop_hit_is_fully_trusted(self):
        assert Geocoder().confidence_of(result(["street_address"], "ROOFTOP")) == 1.0

    def test_a_partial_match_is_discounted(self):
        exact = Geocoder().confidence_of(result(["locality"], "GEOMETRIC_CENTER"))
        partial = Geocoder().confidence_of(result(["locality"], "GEOMETRIC_CENTER", partial=True))
        assert partial < exact


class TestCache:
    """A named place appears in dozens of posts; Google should see it once."""

    def test_an_empty_query_resolves_to_nothing(self, event):
        assert Geocoder(api_key="unused").resolve("   ", event) is None

    def test_a_known_place_is_served_from_the_database(self, event, monkeypatch):
        stored = Location.objects.create(
            point="POINT(-75.69 4.81)",
            precision=LocationPrecision.ADMIN_2,
            raw_text="Pereira, Colombia",
            text_norm=normalize("Pereira, Colombia"),
            source=GeocodeSource.GOOGLE,
        )

        def explode(*args, **kwargs):
            raise AssertionError("a cached place must not reach the API")

        geocoder = Geocoder(api_key="unused")
        monkeypatch.setattr(geocoder, "lookup", explode)
        assert geocoder.resolve("PEREIRA, colombia", event) == stored


@pytest.mark.live
@pytest.mark.skipif(
    not __import__("django.conf", fromlist=["settings"]).settings.GOOGLE_GEOCODING_API_KEY,
    reason="Google Geocoding is not configured",
)
class TestAgainstGoogle:
    """Real strings the extractor produced, to prove precision is not wishful."""

    def test_a_street_address_resolves_finer_than_a_city(self, event):
        geocoder = Geocoder()
        address = geocoder.resolve("Carrera 28 #42-15, Teusaquillo, Bogotá, Colombia", event)
        city = geocoder.resolve("Cali, Valle del Cauca, Colombia", event)
        assert address is not None and city is not None
        assert address.is_at_least(LocationPrecision.NEIGHBORHOOD)
        assert not city.is_at_least(LocationPrecision.NEIGHBORHOOD)

    def test_a_rural_district_resolves_at_all(self, event):
        assert Geocoder().resolve("Herveo, Tolima, Colombia", event) is not None
