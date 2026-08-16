"""
The arm_event command: the one act that authorises spending.

The gazetteer is the part worth testing. An event arrives from USGS already carrying its
country, so arming loads that country's places rather than refusing until somebody types them
in — and it has to refuse when the load comes back empty, because a sweep with no toponym
queries other countries' disasters.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import AdminUnit
from ayudagente.radar.services.gazetteer import GazetteerError
from ayudagente.radar.tests.factories import PEREIRA, make_event

LOAD = "ayudagente.radar.management.commands.arm_event.load_country"


def make_unit(country="CO", code="27001", name="Quibdó") -> AdminUnit:
    """One administrative unit, which is all a sweep needs to have a toponym."""
    return AdminUnit.objects.create(
        country_code=country,
        code=code,
        name=name,
        name_norm=name.casefold(),
        level="admin_2",
        centroid=PEREIRA,
    )


class ArmEventTests(TestCase):
    def setUp(self):
        self.event = make_event(status=EventStatus.PAUSED, country_code="CO")

    def _arm(self, *args) -> str:
        out = StringIO()
        call_command("arm_event", str(self.event.pk), *args, stdout=out)
        return out.getvalue()

    def test_a_country_already_loaded_is_not_downloaded_again(self):
        make_unit()

        with patch(LOAD) as load:
            self._arm()

        load.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.ACTIVE)

    def test_arming_loads_the_country_the_event_came_with(self):
        with patch(LOAD, side_effect=lambda code: make_unit(country=code)) as load:
            output = self._arm()

        load.assert_called_once_with("CO")
        self.assertIn("downloading it from GeoNames", output)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.ACTIVE)

    def test_an_empty_dump_refuses_to_arm(self):
        with patch(LOAD, return_value=None), self.assertRaises(CommandError):
            self._arm()

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.PAUSED)  # nothing may spend yet

    def test_a_failed_download_refuses_to_arm(self):
        with (
            patch(LOAD, side_effect=GazetteerError("geonames is down")),
            self.assertRaises(CommandError),
        ):
            self._arm()

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.PAUSED)
