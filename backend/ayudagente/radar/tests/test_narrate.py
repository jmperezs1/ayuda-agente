"""
The narrate command: the loop told as a story, for a screen somebody is watching.

What is worth asserting is the two modes not being the same thing. Following starts level with
the database so it reports only what arrives; summarising starts from zero so it reports what
is already there. A `--once` that narrated nothing would look identical to a quiet system.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from ayudagente.radar.choices import Direction, EventStatus
from ayudagente.radar.tests.factories import (
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_observation,
    make_requirement,
    make_resource,
)


class NarrateCommandTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")

    def _narrate(self, *args) -> str:
        out = StringIO()
        call_command("narrate", "--once", *args, stdout=out)
        return out.getvalue()

    def _need(self, event=None, name="Barrio Cuba"):
        event = event or self.event
        make_requirement(
            event,
            make_actor(event, name),
            self.agua,
            make_location(PEREIRA, "Pereira"),
            direction=Direction.NEEDS,
        )

    def test_it_summarises_the_events_already_recorded(self):
        output = self._narrate()

        self.assertIn("detected", output)
        self.assertIn(self.event.name, output)

    def test_an_event_says_whether_a_human_has_authorised_it(self):
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])
        make_event(name="Ya autorizada", status=EventStatus.ACTIVE)

        output = self._narrate()

        self.assertIn(f"{self.event.name} (CO) — waiting to be armed", output)
        self.assertIn("Ya autorizada (CO) — already armed", output)

    def test_posts_and_requirements_are_counted_rather_than_listed(self):
        for index in range(3):
            make_observation(self.event, text=f"necesitamos agua {index}")
        self._need()

        output = self._narrate()

        self.assertIn("3 posts", output)
        self.assertIn("1 needs", output)

    def test_naming_an_event_leaves_the_other_one_out(self):
        other = make_event(name="Otra emergencia")
        self._need(event=other, name="Vecino")

        output = self._narrate("--event", str(self.event.pk))

        self.assertNotIn("Otra emergencia", output)
        self.assertNotIn("1 needs", output)


class NarrateEmptyTests(TestCase):
    """The one case with no fixture at all: nothing recorded means nothing said."""

    def test_an_empty_database_narrates_nothing(self):
        out = StringIO()
        call_command("narrate", "--once", stdout=out)

        self.assertEqual(out.getvalue().strip(), "")
