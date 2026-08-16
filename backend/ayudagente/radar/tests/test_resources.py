"""
Tests for resolving an extracted resource onto the catalog.

The catalog has already split once in production — `agua` beside `water`, `transporte` beside
`transport` — and each half matched nothing the other did. Every case here is about that not
happening again: the same thing under a drifted name has to land on the row that already
exists, and a genuinely new thing has to arrive with a parent rather than as an island.
"""

from unittest.mock import patch

from django.test import TestCase

from ayudagente.radar.models import ResourceType
from ayudagente.radar.schemas import ResourceVerdict
from ayudagente.radar.services import taxonomy
from ayudagente.radar.services.requirements import resource_family
from ayudagente.radar.services.resources import resolve_resource


def verdict(
    matches_key: str = "",
    parent_key: str = "",
    name: str = "",
    confidence: float = 0.9,
) -> ResourceVerdict:
    """A model answer with everything the schema needs."""
    return ResourceVerdict(
        matches_key=matches_key,
        parent_key=parent_key,
        name=name,
        confidence=confidence,
        reason="because",
    )


class ResolveTests(TestCase):
    def setUp(self):
        taxonomy.load()

    def _resolve(self, key: str, label: str = ""):
        return resolve_resource(key, label, use_llm=False)

    def test_a_known_key_is_returned_untouched(self):
        result = self._resolve("water", "agua potable")

        self.assertEqual(result.resource.key, "water")
        self.assertEqual(result.method, "key")
        self.assertFalse(result.created)

    def test_a_drifted_spelling_lands_on_the_row_that_exists(self):
        result = self._resolve("waters", "agua")

        self.assertEqual(result.resource.key, "water")
        self.assertEqual(result.method, "trigram")

    def test_the_spanish_name_catches_what_the_english_key_cannot(self):
        # The extractor emits English keys; the post says "colchonetas"
        result = self._resolve("colchonetas", "colchonetas y cobijas")

        self.assertEqual(result.resource.key, "bedding")

    def test_accents_do_not_split_a_resource(self):
        result = self._resolve("medicamentos", "medicamentos")

        self.assertEqual(result.resource.key, "medicine")

    def test_a_resolved_guess_is_remembered_as_an_alias(self):
        self._resolve("waters", "agua")

        water = ResourceType.objects.get(key="water")
        self.assertIn("waters", water.alternate_keys)

    def test_the_second_post_takes_the_alias_and_never_recomputes(self):
        self._resolve("waters", "agua")

        result = self._resolve("waters", "agua")

        self.assertEqual(result.method, "alias")
        self.assertEqual(result.resource.key, "water")

    def test_the_catalog_does_not_grow_a_duplicate(self):
        before = ResourceType.objects.count()

        for label in ("agua", "agua potable", "agua embotellada"):
            self._resolve("waters", label)

        self.assertEqual(ResourceType.objects.count(), before)

    def test_something_genuinely_new_is_created_rather_than_dropped(self):
        result = self._resolve("sandbags", "sacos de arena")

        self.assertTrue(result.created)
        self.assertEqual(ResourceType.objects.get(key="sandbags").name, "Sacos de arena")

    def test_a_new_resource_is_named_in_the_language_of_the_post(self):
        self._resolve("rubber_boots", "botas de caucho")

        self.assertEqual(ResourceType.objects.get(key="rubber_boots").name, "Botas de caucho")

    def test_an_empty_key_still_produces_something_usable(self):
        result = self._resolve("", "")

        self.assertIsNotNone(result.resource)


class AdjudicationTests(TestCase):
    """What the letters cannot settle goes to the model, once per resource."""

    def setUp(self):
        taxonomy.load()

    def _with_verdict(self, answer: ResourceVerdict):
        parsed = type("Response", (), {"output_parsed": answer})()
        fake = type(
            "Client", (), {"responses": type("R", (), {"parse": lambda *a, **k: parsed})()}
        )()
        return patch("ayudagente.radar.services.resources.client", return_value=fake)

    def test_a_semantic_match_reuses_the_existing_row(self):
        # "mercados" is Colombian for a food parcel; no spelling signal connects them
        with self._with_verdict(verdict(matches_key="food")):
            result = resolve_resource("mercados", "mercados para 40 familias")

        self.assertEqual(result.resource.key, "food")
        self.assertEqual(result.method, "llm")
        self.assertIn("mercados", ResourceType.objects.get(key="food").alternate_keys)

    def test_a_new_resource_arrives_with_the_parent_the_model_chose(self):
        with self._with_verdict(verdict(parent_key="hygiene", name="Mascarillas N95")):
            result = resolve_resource("n95_masks", "tapabocas N95")

        created = result.resource
        assert created.parent is not None
        self.assertEqual(created.parent.key, "hygiene")
        self.assertEqual(created.name, "Mascarillas N95")

    def test_a_parented_arrival_can_actually_be_matched_against(self):
        with self._with_verdict(verdict(parent_key="shelter", name="Sacos de arena")):
            result = resolve_resource("sandbags", "sacos de arena")

        family = set(
            ResourceType.objects.filter(id__in=resource_family(result.resource)).values_list(
                "key", flat=True
            )
        )
        self.assertIn("shelter", family)  # a shelter offer can cover it

    def test_an_unsure_model_leaves_the_catalog_alone(self):
        with self._with_verdict(verdict(matches_key="food", confidence=0.3)):
            result = resolve_resource("mercados", "mercados")

        self.assertTrue(result.created)
        self.assertEqual(result.resource.key, "mercados")

    def test_a_model_failure_does_not_lose_the_resource(self):
        with patch(
            "ayudagente.radar.services.resources.client", side_effect=RuntimeError("no api")
        ):
            result = resolve_resource("sandbags", "sacos de arena")

        self.assertEqual(result.resource.key, "sandbags")

    def test_the_model_is_asked_once_and_the_alias_answers_afterwards(self):
        with self._with_verdict(verdict(matches_key="food")) as mocked:
            resolve_resource("mercados", "mercados")
            resolve_resource("mercados", "mercados otra vez")

        self.assertEqual(mocked.call_count, 1)
