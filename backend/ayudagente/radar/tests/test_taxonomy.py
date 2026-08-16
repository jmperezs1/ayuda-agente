"""
Tests for the resource catalog seed.

Creating rows is the easy half. The half worth testing is what the seed does to a database
that already has the wrong ones — the Spanish-keyed duplicates and the nameless leaves the
pipeline invents — because that is the state every existing machine is actually in.
"""

from django.test import TestCase

from ayudagente.radar.models import Requirement, ResourceType
from ayudagente.radar.services import taxonomy
from ayudagente.radar.services.requirements import resource_family
from ayudagente.radar.tests.factories import (
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_requirement,
)


def load() -> dict:
    """Run the seed with its output discarded."""
    return taxonomy.load(lambda _: None)


class LoadTests(TestCase):
    def test_it_creates_the_whole_catalog_and_wires_the_hierarchy(self):
        counts = load()

        self.assertEqual(counts["resource_types"], len(taxonomy.RESOURCES))
        parent = ResourceType.objects.get(key="pet_food").parent
        assert parent is not None
        self.assertEqual(parent.key, "food")

    def test_a_second_run_creates_nothing(self):
        load()

        self.assertEqual(load()["resource_types"], 0)
        self.assertEqual(ResourceType.objects.count(), len(taxonomy.RESOURCES))


class AdoptionTests(TestCase):
    """A key the pipeline invented gets its Spanish name once the taxonomy declares it."""

    def test_a_leaf_named_after_its_key_is_adopted(self):
        ResourceType.objects.create(key="support", name="support")

        counts = load()

        adopted = ResourceType.objects.get(key="support")
        assert adopted.parent is not None
        self.assertEqual(counts["adopted"], 1)
        self.assertEqual(adopted.name, "Apoyo general")
        self.assertEqual(adopted.parent.key, "volunteers")
        self.assertEqual(adopted.default_unit, "personas")

    def test_a_row_somebody_named_is_left_alone(self):
        ResourceType.objects.create(key="water", name="Agua potable del acueducto")

        counts = load()

        self.assertEqual(counts["adopted"], 0)
        self.assertEqual(ResourceType.objects.get(key="water").name, "Agua potable del acueducto")

    def test_adoption_does_not_repeat_on_a_second_run(self):
        ResourceType.objects.create(key="support", name="support")
        load()

        self.assertEqual(load()["adopted"], 0)


class HierarchyTests(TestCase):
    """Aid is the root of what a collection center hands out, and nothing else."""

    def setUp(self):
        load()

    def _family(self, key: str) -> set[str]:
        resource = ResourceType.objects.get(key=key)
        return set(
            ResourceType.objects.filter(id__in=resource_family(resource)).values_list(
                "key", flat=True
            )
        )

    def test_an_aid_offer_reaches_the_specific_needs_under_it(self):
        family = self._family("humanitarian_aid")

        self.assertLessEqual({"water", "food", "medicine", "hygiene", "shelter"}, family)
        self.assertLessEqual({"tents", "bedding", "pet_food", "medical_care"}, family)

    def test_an_aid_offer_is_never_proposed_as_transport_or_labour(self):
        family = self._family("humanitarian_aid")

        for key in ("transport", "machinery", "power", "generators", "volunteers", "cash"):
            with self.subTest(key=key):
                self.assertNotIn(key, family)

    def test_siblings_still_do_not_cover_each_other(self):
        # Water and food share a parent; widening the tree must not connect them
        self.assertNotIn("food", self._family("water"))
        self.assertNotIn("medicine", self._family("food"))

    def test_a_specific_offer_still_covers_the_generic_need(self):
        self.assertIn("humanitarian_aid", self._family("water"))

    def test_an_existing_row_is_moved_to_the_declared_parent(self):
        water = ResourceType.objects.get(key="water")
        water.parent = None
        water.save(update_fields=["parent"])

        counts = load()

        water.refresh_from_db()
        self.assertEqual(counts["regrafted"], 1)
        assert water.parent is not None
        self.assertEqual(water.parent.key, "humanitarian_aid")

    def test_regrafting_does_not_repeat(self):
        self.assertEqual(load()["regrafted"], 0)


class ClearTests(TestCase):
    """`Requirement.resource` is PROTECT, so clearing has to exclude what is in use."""

    def setUp(self):
        load()
        self.event = make_event()
        self.actor = make_actor(self.event, "Barrio Cuba")

    def test_a_referenced_type_is_skipped_rather_than_raising(self):
        make_requirement(
            self.event,
            self.actor,
            ResourceType.objects.get(key="support"),
            make_location(PEREIRA, "cuba"),
        )

        taxonomy.clear(lambda _: None)

        # `volunteers` survives too: it is the parent of the row that had to stay
        self.assertEqual(
            sorted(ResourceType.objects.values_list("key", flat=True)),
            ["support", "volunteers"],
        )

    def test_an_unused_catalog_is_removed_whole(self):
        self.assertEqual(taxonomy.clear(lambda _: None), len(taxonomy.RESOURCES))
        self.assertEqual(ResourceType.objects.count(), 0)


class LegacyKeyTests(TestCase):
    """The Spanish-keyed duplicates left behind by the data migration this seed replaced."""

    def setUp(self):
        self.event = make_event()
        self.actor = make_actor(self.event, "Barrio Cuba")

    def test_a_duplicate_with_no_requirements_is_removed(self):
        ResourceType.objects.create(key="agua", name="Agua")

        counts = load()

        self.assertEqual(counts["retired"], 1)
        self.assertFalse(ResourceType.objects.filter(key="agua").exists())

    def test_requirements_are_repointed_rather_than_orphaned(self):
        legacy = ResourceType.objects.create(key="transporte", name="Transporte")
        requirement = make_requirement(
            self.event, self.actor, legacy, make_location(PEREIRA, "cuba")
        )

        load()

        requirement.refresh_from_db()
        self.assertEqual(requirement.resource.key, "transport")
        self.assertFalse(ResourceType.objects.filter(key="transporte").exists())

    def test_a_child_of_a_duplicate_is_repointed_too(self):
        legacy = ResourceType.objects.create(key="alimentos", name="Alimentos")
        orphan = ResourceType.objects.create(key="enlatados", name="Enlatados", parent=legacy)

        load()

        orphan.refresh_from_db()
        assert orphan.parent is not None
        self.assertEqual(orphan.parent.key, "food")

    def test_nothing_is_retired_when_the_catalog_is_already_clean(self):
        load()

        self.assertEqual(load()["retired"], 0)

    def test_the_catalog_ends_with_exactly_the_declared_types(self):
        for legacy_key in taxonomy.LEGACY_KEYS:
            ResourceType.objects.create(key=legacy_key, name=legacy_key)

        load()

        self.assertEqual(
            sorted(ResourceType.objects.values_list("key", flat=True)),
            sorted(key for key, *_ in taxonomy.RESOURCES),
        )

    def test_no_requirement_is_lost_to_the_merge(self):
        for legacy_key in ("agua", "alimentos", "voluntarios"):
            legacy = ResourceType.objects.create(key=legacy_key, name=legacy_key)
            make_requirement(
                self.event, self.actor, legacy, make_location(PEREIRA, f"sitio {legacy_key}")
            )

        load()

        self.assertEqual(Requirement.objects.count(), 3)
        self.assertEqual(
            sorted(Requirement.objects.values_list("resource__key", flat=True)),
            ["food", "volunteers", "water"],
        )
