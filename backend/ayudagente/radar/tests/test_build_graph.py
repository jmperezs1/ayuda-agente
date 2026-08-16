"""The build_graph command: the trigger that gives the graph its edges."""

from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from ayudagente.radar.choices import Direction
from ayudagente.radar.models import Match
from ayudagente.radar.tests.factories import (
    DOSQUEBRADAS,
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class BuildGraphCommandTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")

    def _pair(self):
        make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            self.agua,
            make_location(PEREIRA, "Barrio"),
            quantity=Decimal(100),
        )
        make_requirement(
            self.event,
            make_actor(self.event, "Vecino"),
            self.agua,
            make_location(DOSQUEBRADAS, "Vecino"),
            direction=Direction.OFFERS,
            quantity=Decimal(100),
        )

    def test_builds_edges_for_active_events(self):
        self._pair()
        out = StringIO()

        call_command("build_graph", stdout=out)

        self.assertEqual(Match.objects.filter(need__event=self.event).count(), 1)
        self.assertIn("1 matches proposed", out.getvalue())
        self.assertIn("rebuilt", out.getvalue())

    def test_event_flag_restricts_the_pass(self):
        self._pair()
        other = make_event(name="Otro evento")
        out = StringIO()

        call_command("build_graph", event=other.id, stdout=out)

        self.assertEqual(Match.objects.count(), 0)  # only the empty event was rebuilt

    def test_unknown_event_fails_loudly(self):
        with self.assertRaises(CommandError):
            call_command("build_graph", event=99999)
