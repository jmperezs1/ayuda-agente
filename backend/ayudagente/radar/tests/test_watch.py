"""
Tests for noticing a disaster before anyone types it in.

The guarantee these protect is not that detection is clever. It is that detection is **free**:
a proposed event is paused, every writer refuses a paused event through `is_harvestable`, and
arming it is a separate human act. Anything that lets a feed start spending is the bug.
"""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
from django.contrib.gis.geos import Point
from django.test import TestCase

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import AdminUnit, Event
from ayudagente.radar.services.watch import (
    MIN_MAGNITUDE,
    Candidate,
    country_of,
    poll,
    propose,
    watch,
)
from ayudagente.radar.tests.factories import PEREIRA

FEED = "https://example.test/feed.geojson"


def feature(**overrides) -> dict:
    """One USGS GeoJSON feature, shaped the way the real feed shapes them."""
    properties = {
        "title": "M 7.4 - 13 km SE of Quibdó, Colombia",
        "time": 1_754_827_200_000,
        "mag": 7.4,
        "alert": "orange",
    }
    properties.update(overrides.pop("properties", {}))
    return {
        "id": overrides.pop("id", "us7000abcd"),
        "properties": properties,
        "geometry": {"coordinates": [PEREIRA.x, PEREIRA.y, 12.5]},
        **overrides,
    }


def client_returning(*features) -> httpx.Client:
    """An httpx client that answers any GET with these features."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"features": list(features)})

    return httpx.Client(transport=httpx.MockTransport(handler))


class PollTests(TestCase):
    def test_a_quake_pager_expects_casualties_from_is_a_candidate(self):
        found = poll(FEED, client_returning(feature()))

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].external_id, "us7000abcd")
        self.assertEqual(found[0].magnitude, 7.4)

    def test_a_green_alert_is_not_a_disaster_however_large(self):
        # PAGER green means "felt, nothing broke"; magnitude must not override its judgement
        green = feature(properties={"alert": "green", "mag": 7.9})

        self.assertEqual(poll(FEED, client_returning(green)), [])

    def test_an_unrated_quake_falls_back_to_magnitude(self):
        unrated = feature(properties={"alert": None, "mag": MIN_MAGNITUDE + 0.1})

        self.assertEqual(len(poll(FEED, client_returning(unrated))), 1)

    def test_a_small_unrated_quake_is_ignored(self):
        small = feature(properties={"alert": None, "mag": MIN_MAGNITUDE - 0.5})

        self.assertEqual(poll(FEED, client_returning(small)), [])

    def test_a_feature_missing_what_we_need_is_skipped_rather_than_raising(self):
        broken = {"id": "", "properties": {}, "geometry": {}}

        self.assertEqual(poll(FEED, client_returning(broken)), [])

    def test_a_dead_feed_raises_instead_of_reporting_calm(self):
        def handler(request):
            return httpx.Response(503)

        with self.assertRaises(httpx.HTTPError):
            poll(FEED, httpx.Client(transport=httpx.MockTransport(handler)))


class ProposeBase(TestCase):
    def setUp(self):
        AdminUnit.objects.create(
            country_code="CO",
            code="66001",
            name="Pereira",
            name_norm="pereira",
            level="admin_2",
            centroid=PEREIRA,
        )

    def _candidate(self, **overrides) -> Candidate:
        base = Candidate(
            external_id="us7000abcd",
            title="M 7.4 - 13 km SE of Quibdó, Colombia",
            occurred_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
            epicenter=PEREIRA,
            magnitude=7.4,
            depth_km=12.5,
            alert="orange",
        )
        return replace(base, **overrides)

    def _propose(self, **overrides) -> tuple[Event, bool]:
        """Propose and insist a row came back, so the type is not Optional downstream."""
        event, created = propose(self._candidate(**overrides))
        assert event is not None
        return event, created


class ProposeTests(ProposeBase):
    def test_a_proposed_event_cannot_be_harvested(self):
        event, created = self._propose()

        self.assertTrue(created)
        self.assertEqual(event.status, EventStatus.PAUSED)
        self.assertFalse(event.is_harvestable)

    def test_the_country_comes_from_the_gazetteer_we_hold(self):
        event, _created = self._propose()

        self.assertEqual(event.country_code, "CO")

    def test_proposing_the_same_quake_twice_does_not_split_the_response(self):
        # USGS revises magnitude and alert for hours, so the same id arrives repeatedly
        self._propose()

        event, created = self._propose(magnitude=7.6)

        self.assertFalse(created)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(event.magnitude, 7.4)

    def test_a_re_poll_never_pauses_an_event_somebody_armed(self):
        event, _created = self._propose()
        Event.objects.filter(pk=event.pk).update(status=EventStatus.ACTIVE)

        propose(self._candidate())

        self.assertEqual(Event.objects.get(pk=event.pk).status, EventStatus.ACTIVE)

    def test_a_quake_no_gazetteer_covers_is_not_recorded(self):
        # An event with no country cannot be swept, and recording it would look like progress
        pacific = self._candidate(external_id="us9999", epicenter=Point(-140.0, -30.0, srid=4326))

        event, created = propose(pacific)

        self.assertIsNone(event)
        self.assertFalse(created)


class CountryTests(ProposeBase):
    def test_a_point_inside_a_loaded_country_resolves(self):
        self.assertEqual(country_of(PEREIRA), "CO")

    def test_a_point_nowhere_near_anything_loaded_resolves_to_nothing(self):
        self.assertIsNone(country_of(Point(-140.0, -30.0, srid=4326)))


class WatchTests(ProposeBase):
    def test_a_pass_reports_only_what_it_created(self):
        proposed = watch(FEED, client_returning(feature()))

        self.assertEqual(len(proposed), 1)

        again = watch(FEED, client_returning(feature()))

        self.assertEqual(again, [])
        self.assertEqual(Event.objects.count(), 1)
