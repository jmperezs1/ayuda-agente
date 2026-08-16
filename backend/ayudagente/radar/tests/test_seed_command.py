"""
Tests for the seed command, and for the line between fixtures and reference data.

What the command guarantees is that loading twice adds nothing and that clearing takes the
fixture without touching the catalog underneath it. The catalog is the part worth asserting:
it is loaded by its own command because a deployment depends on it, so a `--clear` run that
reached it would take production data out with a development fixture.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from ayudagente.radar.models import (
    Actor,
    Event,
    Extraction,
    Location,
    Match,
    Observation,
    Requirement,
    ResourceType,
)
from ayudagente.radar.seeds import SEEDS, pilot
from ayudagente.radar.services import taxonomy
from ayudagente.radar.tests.factories import (
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


def seed(*args) -> str:
    """Run the command with its output captured."""
    out = StringIO()
    call_command("seed", *args, stdout=out)
    return out.getvalue()


class RegistryTests(TestCase):
    """Nothing a deployment depends on may sit behind a `--clear` flag."""

    def test_the_registry_holds_only_development_fixtures(self):
        self.assertEqual(sorted(SEEDS), ["pilot"])

    def test_the_resource_catalog_is_not_a_seed(self):
        self.assertNotIn("taxonomy", SEEDS)


class PilotTests(TestCase):
    def setUp(self):
        taxonomy.load()
        seed("--only", "pilot")

    def test_the_corpus_loads_against_the_reference_catalog(self):
        self.assertEqual(ResourceType.objects.count(), len(taxonomy.RESOURCES))
        self.assertTrue(Observation.objects.exists())

    def test_clearing_a_fixture_leaves_the_catalog_alone(self):
        seed("--only", "pilot", "--clear")

        self.assertEqual(Observation.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(ResourceType.objects.count(), len(taxonomy.RESOURCES))

    def test_flush_ends_with_the_corpus_loaded_and_no_duplicates(self):
        before = Observation.objects.count()

        seed("--only", "pilot", "--flush")

        self.assertEqual(Observation.objects.count(), before)
        self.assertEqual(ResourceType.objects.count(), len(taxonomy.RESOURCES))

    def test_loading_twice_creates_nothing(self):
        before = Observation.objects.count()

        seed("--only", "pilot")

        self.assertEqual(Observation.objects.count(), before)


class ClearLeavesNothingDanglingTests(TestCase):
    """
    What a cascade cannot reach, and what clearing must not take with it.

    Note:
        `Location` hangs off no event, so deleting the pilot leaves every place it geocoded
        behind. Clearing has to collect those without touching the ones another emergency
        still points at — which is the whole reason the seed carries its own `clear`.
    """

    def _place(self, event, name):
        location = make_location(PEREIRA, name)
        make_requirement(
            event,
            make_actor(event, f"Actor de {name}"),
            make_resource(f"recurso-{name}"),
            location,
        )
        return location

    def test_clearing_removes_the_places_nothing_points_at_any_more(self):
        pilot_event = make_event(name=pilot.EVENT_NAMES[0])
        theirs = self._place(pilot_event, "quibdo")

        pilot.clear(lambda _: None)

        self.assertFalse(Location.objects.filter(pk=theirs.pk).exists())

    def test_clearing_leaves_a_place_another_emergency_still_uses(self):
        pilot_event = make_event(name=pilot.EVENT_NAMES[0])
        self._place(pilot_event, "quibdo")
        other_event = make_event(name="Otra emergencia")
        shared = self._place(other_event, "pereira")

        pilot.clear(lambda _: None)

        self.assertTrue(Location.objects.filter(pk=shared.pk).exists())

    def test_the_catalog_survives_clearing(self):
        pilot_event = make_event(name=pilot.EVENT_NAMES[0])
        self._place(pilot_event, "quibdo")
        before = ResourceType.objects.count()

        pilot.clear(lambda _: None)

        self.assertEqual(ResourceType.objects.count(), before)


class ProcessedSnapshotTests(TestCase):
    """
    What seeding restores now that a real pipeline pass is committed alongside the raw posts.

    Note:
        The point of the snapshot is that a fresh database draws a map without anyone paying
        for it again, so the assertions are on the derived rows rather than the observations.
        Those are what the front renders.
    """

    def test_seeding_restores_the_corpus_already_read(self):
        call_command("seed", stdout=StringIO())

        self.assertTrue(Extraction.objects.exists())
        self.assertTrue(Requirement.objects.exists())
        self.assertTrue(Actor.objects.exists())
        self.assertTrue(Match.objects.exists())

    def test_the_catalog_is_matched_by_slug_rather_than_duplicated(self):
        taxonomy.load()
        before = ResourceType.objects.count()

        call_command("seed", stdout=StringIO())

        self.assertEqual(ResourceType.objects.count(), before)

    def test_clearing_the_snapshot_leaves_nothing_of_the_event(self):
        call_command("seed", stdout=StringIO())

        call_command("seed", "--clear", stdout=StringIO())

        self.assertFalse(Requirement.objects.exists())
        self.assertFalse(Actor.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertFalse(Location.objects.exists())
