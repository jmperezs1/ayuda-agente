"""
Tests for the `match_resource` tool.

They cover the wrapper's own job — argument translation, truncation, serialization and
failures returned as values — plus the service guarantees the tool docstring promises to
the model, because a promise in a docstring the code does not keep is a lie the agent acts
on.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from agent_tools.match_resource import match_resource
from ayudagente.radar.choices import (
    ActorKind,
    AdminLevel,
    Urgency,
)
from ayudagente.radar.models import AdminUnit, ResourceType
from ayudagente.radar.tests.factories import (
    DOSQUEBRADAS,
    PEREIRA,
    QUIBDO,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class MatchResourceTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.alimentos = make_resource("alimentos", name="Alimentos")
        self.mascotas = make_resource("alimentos_mascotas", parent=self.alimentos)
        self.pereira = AdminUnit.objects.create(
            country_code="CO",
            code="66001",
            name="Pereira",
            name_norm="pereira",
            level=AdminLevel.ADMIN_2,
        )

    def _need(self, point, text, **kwargs):
        actor = kwargs.pop("actor", None) or make_actor(
            self.event, text, kind=ActorKind.COLLECTION_CENTER
        )
        return make_requirement(
            self.event,
            actor,
            kwargs.pop("resource", self.alimentos),
            make_location(point, text, admin_unit=self.pereira, **kwargs.pop("location", {})),
            **kwargs,
        )

    def test_returns_plain_json_with_the_fields_the_prompt_promises(self):
        self._need(
            PEREIRA,
            "Coliseo Mayor",
            quantity=Decimal(200),
            covered_quantity=Decimal(50),
            unit="mercados",
            free_text="Necesitamos mercados para 40 familias",
        )

        result = match_resource.invoke({"event_id": self.event.id, "offering": True})

        self.assertEqual(result["count"], 1)
        self.assertFalse(result["truncated"])
        row = result["candidates"][0]
        self.assertEqual(row["still_needed"], 150.0)  # 200 asked, 50 already covered
        self.assertEqual(row["unit"], "mercados")
        self.assertEqual(row["municipality"], "Pereira")
        self.assertEqual(row["resource_key"], "alimentos")
        self.assertIn("40 familias", row["note"])
        self.assertNotIn("distance_km", row)  # no reference point was given

    def test_unstated_quantity_is_null_not_zero(self):
        self._need(PEREIRA, "Sin cifra")

        result = match_resource.invoke({"event_id": self.event.id, "offering": True})

        self.assertIsNone(result["candidates"][0]["still_needed"])

    def test_orders_by_distance_and_reports_kilometres(self):
        self._need(DOSQUEBRADAS, "Cerca")
        self._need(QUIBDO, "Lejos")

        result = match_resource.invoke(
            {
                "event_id": self.event.id,
                "offering": True,
                "lat": PEREIRA.y,
                "lon": PEREIRA.x,
            }
        )

        places = [r["place"] for r in result["candidates"]]
        self.assertEqual(places, ["Cerca", "Lejos"])
        self.assertLess(result["candidates"][0]["distance_km"], 10)

    def test_resource_key_walks_the_tree_in_both_directions(self):
        self._need(PEREIRA, "Necesita comida genérica", resource=self.alimentos)

        result = match_resource.invoke(
            {
                "event_id": self.event.id,
                "offering": True,
                "resource_key": "alimentos_mascotas",
            }
        )

        self.assertEqual(result["count"], 1)  # dog food offer reaches a generic food need

    def test_truncation_is_flagged_and_the_cap_is_enforced(self):
        for i in range(6):
            self._need(PEREIRA, f"Centro {i}")

        result = match_resource.invoke({"event_id": self.event.id, "offering": True, "limit": 3})

        self.assertEqual(result["count"], 3)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertTrue(result["truncated"])

    def test_limit_is_capped_rather_than_obeyed(self):
        self._need(PEREIRA, "Uno")

        result = match_resource.invoke({"event_id": self.event.id, "offering": True, "limit": 500})

        self.assertEqual(result["count"], 1)


class MatchResourceFailureTests(TestCase):
    """Failures come back as values. An exception inside a tool call is an opaque trace."""

    def setUp(self):
        self.event = make_event()

    def test_unknown_event_says_so_instead_of_looking_empty(self):
        result = match_resource.invoke({"event_id": 9999, "offering": True})

        self.assertIn("error", result)
        self.assertEqual(result["candidates"], [])

    def test_unknown_resource_key_is_not_a_silent_empty_result(self):
        result = match_resource.invoke(
            {"event_id": self.event.id, "offering": True, "resource_key": "unobtanium"}
        )

        self.assertIn("error", result)
        self.assertIn("hint", result)

    def test_an_english_word_gets_the_catalog_back_instead_of_a_dead_end(self):
        make_resource("agua_potable", name="Agua potable")
        make_resource("alimentos", name="Alimentos")

        result = match_resource.invoke(
            {"event_id": self.event.id, "offering": True, "resource_key": "water"}
        )

        self.assertIn("error", result)
        keys = [row["key"] for row in result["available"]]
        self.assertEqual(keys, ["agua_potable", "alimentos"])  # enough to retry correctly


class UrgencyOrderingTests(TestCase):
    """`urgency` is a CharField, so alphabetical ordering buries `critical` under `low`."""

    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")

    def test_the_most_urgent_row_comes_first(self):
        for urgency in [Urgency.LOW, Urgency.MEDIUM, Urgency.CRITICAL, Urgency.HIGH]:
            make_requirement(
                self.event,
                make_actor(self.event, urgency),
                self.agua,
                make_location(PEREIRA, urgency),
                urgency=urgency,
            )

        result = match_resource.invoke({"event_id": self.event.id, "offering": True})

        self.assertEqual(
            [r["urgency"] for r in result["candidates"]],
            ["critical", "high", "medium", "low"],
        )

    def test_a_critical_row_survives_the_row_limit(self):
        # Four rows the alphabet ranks above 'critical', and a limit that only fits three
        for i, urgency in enumerate([Urgency.LOW, Urgency.MEDIUM, Urgency.MEDIUM]):
            make_requirement(
                self.event,
                make_actor(self.event, f"ruido {i}"),
                self.agua,
                make_location(PEREIRA, f"ruido {i}"),
                urgency=urgency,
            )
        make_requirement(
            self.event,
            make_actor(self.event, "el que importa"),
            self.agua,
            make_location(PEREIRA, "el que importa"),
            urgency=Urgency.CRITICAL,
        )

        result = match_resource.invoke({"event_id": self.event.id, "offering": True, "limit": 1})

        self.assertEqual(result["candidates"][0]["actor"], "el que importa")


class PlaceFilterTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")
        self.choco = AdminUnit.objects.create(
            country_code="CO",
            code="27",
            name="Chocó",
            name_norm="choco",
            level=AdminLevel.ADMIN_1,
        )
        self.quibdo = AdminUnit.objects.create(
            country_code="CO",
            code="27001",
            name="Quibdó",
            name_norm="quibdo",
            level=AdminLevel.ADMIN_2,
            parent=self.choco,
        )
        self.pereira = AdminUnit.objects.create(
            country_code="CO",
            code="66001",
            name="Pereira",
            name_norm="pereira",
            level=AdminLevel.ADMIN_2,
        )

    def _need(self, unit, point, text):
        return make_requirement(
            self.event,
            make_actor(self.event, text),
            self.agua,
            make_location(point, text, admin_unit=unit),
        )

    def _search(self, place):
        return match_resource.invoke({"event_id": self.event.id, "offering": True, "place": place})

    def test_a_municipality_excludes_everywhere_else(self):
        self._need(self.quibdo, QUIBDO, "Quibdó centro")
        self._need(self.pereira, PEREIRA, "Barrio Cuba")

        result = self._search("Quibdó")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["municipality"], "Quibdó")

    def test_a_name_without_its_accent_still_resolves(self):
        self._need(self.quibdo, QUIBDO, "Quibdó centro")
        self.assertEqual(self._search("quibdo")["count"], 1)

    def test_the_divipola_code_works_too(self):
        self._need(self.quibdo, QUIBDO, "Quibdó centro")
        self.assertEqual(self._search("27001")["count"], 1)

    def test_a_department_reaches_its_municipalities(self):
        self._need(self.quibdo, QUIBDO, "Quibdó centro")
        self._need(self.pereira, PEREIRA, "Barrio Cuba")

        result = self._search("Chocó")

        self.assertEqual(result["count"], 1)  # found through the parent, not directly

    def test_an_unknown_place_is_not_a_silent_empty_result(self):
        self._need(self.quibdo, QUIBDO, "Quibdó centro")
        self.assertIn("error", self._search("Narnia"))

    def test_a_same_named_place_in_another_country_is_not_reachable(self):
        # Codes are unique per country, so the gazetteer is full of collisions
        peru_quibdo = AdminUnit.objects.create(
            country_code="PE",
            code="27001",
            name="Quibdó",
            name_norm="quibdo",
            level=AdminLevel.ADMIN_2,
        )
        self._need(peru_quibdo, QUIBDO, "Homónimo peruano")
        self._need(self.quibdo, QUIBDO, "Quibdó centro")

        result = self._search("Quibdó")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["place"], "Quibdó centro")

    def test_a_repeated_name_asks_which_one_instead_of_guessing(self):
        risaralda = AdminUnit.objects.create(
            country_code="CO",
            code="66",
            name="Risaralda",
            name_norm="risaralda",
            level=AdminLevel.ADMIN_1,
        )
        for code, parent in [("27615", self.choco), ("66682", risaralda)]:
            AdminUnit.objects.create(
                country_code="CO",
                code=code,
                name="Santa Rosa",
                name_norm="santa rosa",
                level=AdminLevel.ADMIN_2,
                parent=parent,
            )

        result = self._search("Santa Rosa")

        self.assertIn("error", result)
        codes = sorted(c["code"] for c in result["place_options"])
        self.assertEqual(codes, ["27615", "66682"])
        self.assertEqual(
            sorted(c["parent"] for c in result["place_options"]), ["Chocó", "Risaralda"]
        )


class TextSearchTests(TestCase):
    """The specificity the coarse taxonomy deliberately does not carry."""

    def setUp(self):
        self.event = make_event()
        self.alimentos = make_resource("alimentos", name="Alimentos")

    def _need(self, free_text):
        return make_requirement(
            self.event,
            make_actor(self.event, free_text[:20]),
            self.alimentos,
            make_location(PEREIRA, free_text[:20]),
            free_text=free_text,
        )

    def _search(self, text):
        return match_resource.invoke({"event_id": self.event.id, "offering": True, "text": text})

    def test_finds_wording_that_has_no_resource_of_its_own(self):
        self._need("Necesitamos leche de fórmula y pañales")
        self._need("Se requieren mercados para 40 familias")

        result = self._search("leche de formula")  # no accent, like a hurried typist

        self.assertEqual(result["count"], 1)
        self.assertIn("fórmula", result["candidates"][0]["note"])

    def test_a_misspelling_still_lands(self):
        self._need("Necesitamos colchonetas para el albergue")
        self.assertEqual(self._search("colchonetaz")["count"], 1)

    def test_unrelated_wording_returns_nothing_rather_than_anything(self):
        self._need("Necesitamos mercados para 40 familias")
        self.assertEqual(self._search("motobomba")["count"], 0)

    def test_text_filters_but_does_not_reorder(self):
        make_requirement(
            self.event,
            make_actor(self.event, "urgente"),
            self.alimentos,
            make_location(PEREIRA, "urgente"),
            free_text="mercados",
            urgency=Urgency.CRITICAL,
        )
        make_requirement(
            self.event,
            make_actor(self.event, "verboso"),
            self.alimentos,
            make_location(PEREIRA, "verboso"),
            free_text="mercados mercados mercados",
            urgency=Urgency.LOW,
        )

        result = self._search("mercados")

        # The chattier row matches the query more closely and still comes second
        self.assertEqual(result["candidates"][0]["actor"], "urgente")


class ResourceResolutionTests(TestCase):
    """One resource, reachable by every spelling a model plausibly produces."""

    def setUp(self):
        self.event = make_event()
        make_resource("agua_potable", name="Agua potable")
        make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            ResourceType.objects.get(key="agua_potable"),
            make_location(PEREIRA, "Barrio"),
        )

    def _search(self, resource_key):
        return match_resource.invoke(
            {"event_id": self.event.id, "offering": True, "resource_key": resource_key}
        )

    def test_the_exact_slug_works(self):
        self.assertEqual(self._search("agua_potable")["count"], 1)

    def test_the_display_name_works(self):
        self.assertEqual(self._search("Agua potable")["count"], 1)

    def test_a_name_with_spaces_and_odd_casing_is_slugified(self):
        self.assertEqual(self._search("AGUA POTABLE")["count"], 1)

    def test_a_missing_accent_still_resolves(self):
        make_resource("articulos_aseo", name="Artículos de aseo")
        result = match_resource.invoke(
            {
                "event_id": self.event.id,
                "offering": True,
                "resource_key": "articulos de aseo",
            }
        )
        self.assertNotIn("error", result)

    def test_translation_is_refused_rather_than_guessed(self):
        # 'water' must not resolve to 'agua_potable'; the catalog comes back instead
        self.assertIn("error", self._search("water"))

    def test_half_a_coordinate_is_rejected(self):
        result = match_resource.invoke({"event_id": self.event.id, "offering": True, "lat": 4.81})

        self.assertIn("error", result)

    def test_radius_without_a_point_is_rejected(self):
        result = match_resource.invoke(
            {"event_id": self.event.id, "offering": True, "radius_km": 20}
        )

        self.assertIn("error", result)


class RoutabilityTests(TestCase):
    """What the tool docstring promises the model about which rows it will not see."""

    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")

    def _need(self, actor, text, point=PEREIRA, **kwargs):
        location = make_location(point, text, **kwargs.pop("location", {}))
        return make_requirement(self.event, actor, self.agua, location, **kwargs)

    def _search(self):
        return match_resource.invoke({"event_id": self.event.id, "offering": True})

    def test_a_centre_whose_window_closed_is_gone(self):
        self._need(
            make_actor(self.event, "Ya cerró"),
            "Centro",
            window_end=timezone.now() - timedelta(hours=3),
        )
        self.assertEqual(self._search()["count"], 0)

    def test_a_merged_duplicate_does_not_appear_beside_its_canonical_actor(self):
        canonical = make_actor(self.event, "Coliseo Mayor")
        duplicate = make_actor(self.event, "el coliseo", merged_into=canonical)
        self._need(canonical, "Coliseo Mayor")
        self._need(duplicate, "el coliseo", point=DOSQUEBRADAS)

        result = self._search()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["actor"], "Coliseo Mayor")

    def test_a_saturated_requirement_stops_being_offered(self):
        self._need(
            make_actor(self.event, "Lleno"),
            "Centro",
            quantity=Decimal(20),
            covered_quantity=Decimal(20),
        )
        self.assertEqual(self._search()["count"], 0)
