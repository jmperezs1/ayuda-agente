"""
Tying a geocoded place to the municipality it falls in.

Every geocoded `Location` used to be stored with `admin_unit=None`, and three consumers read
that link: the agent's place filter, the blocking stage of identity resolution, and the
frontier's promotion of accounts. All three answered as if the country were empty.
"""

from io import StringIO

from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase

from ayudagente.radar.models import AdminUnit, Location
from ayudagente.radar.services.geocoding import UNIT_REACH_KM, unit_for
from ayudagente.radar.tests.factories import (
    DOSQUEBRADAS,
    PEREIRA,
    make_actor,
    make_event,
    make_location,
)

FAR_AWAY = Point(-70.0, 10.0, srid=4326)


def make_unit(name: str, centroid: Point, code: str = "66001") -> AdminUnit:
    """One municipality with a centroid, which is all the resolver reads."""
    return AdminUnit.objects.create(
        country_code="CO",
        code=code,
        name=name,
        name_norm=name.casefold(),
        level="admin_2",
        centroid=centroid,
    )


class UnitForTests(TestCase):
    def test_a_point_resolves_to_the_nearest_municipality(self):
        make_unit("Pereira", PEREIRA)
        make_unit("Dosquebradas", DOSQUEBRADAS, code="66170")

        resolved = unit_for(PEREIRA, "CO")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "Pereira")

    def test_a_point_with_nothing_within_reach_stays_unlinked(self):
        make_unit("Pereira", PEREIRA)

        self.assertIsNone(unit_for(FAR_AWAY, "CO"))

    def test_another_country_is_never_borrowed(self):
        make_unit("Pereira", PEREIRA)

        self.assertIsNone(unit_for(PEREIRA, "MX"))

    def test_the_reach_is_generous_enough_for_a_centroid_off_centre(self):
        self.assertGreaterEqual(UNIT_REACH_KM, 25)


class LinkLocationsCommandTests(TestCase):
    def test_it_attaches_the_places_stored_before_the_link_existed(self):
        unit = make_unit("Pereira", PEREIRA)
        event = make_event()
        location = make_location(PEREIRA, "Pereira")
        make_actor(event, "Barrio Cuba", location=location)

        call_command("link_locations", stdout=StringIO())

        location.refresh_from_db()
        self.assertEqual(location.admin_unit, unit)

    def test_a_dry_run_writes_nothing(self):
        make_unit("Pereira", PEREIRA)
        event = make_event()
        location = make_location(PEREIRA, "Pereira")
        make_actor(event, "Barrio Cuba", location=location)

        call_command("link_locations", "--dry-run", stdout=StringIO())

        location.refresh_from_db()
        self.assertIsNone(location.admin_unit)

    def test_a_place_nothing_references_is_left_alone(self):
        make_unit("Pereira", PEREIRA)
        orphan = make_location(PEREIRA, "Pereira")

        call_command("link_locations", stdout=StringIO())

        orphan.refresh_from_db()
        self.assertIsNone(orphan.admin_unit)  # no event, so no country to search
        self.assertTrue(Location.objects.filter(pk=orphan.pk).exists())
