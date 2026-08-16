"""
Tests for the cold-start pass: loading a country's places, then covering them in few queries.

The assertion that matters most is the batching one. One query per municipality is eleven
hundred queries for Colombia; the pilot covered the country in ten. Everything else here
protects the invariant that no query ever leaves without a toponym.
"""

from django.contrib.gis.geos import Point
from django.test import TestCase

from ayudagente.radar.choices import AdminLevel, DecisionSource, JobStatus, Zone
from ayudagente.radar.models import AdminUnit, FrontierNode, HarvestJob
from ayudagente.radar.services.apify_inputs import build_input
from ayudagente.radar.services.gazetteer import (
    administrative_words,
    load_country,
    search_name,
)
from ayudagente.radar.services.sweep import (
    TOPONYMS_PER_QUERY,
    bootstrap_event,
    places_by_zone,
    sweep_query,
)
from ayudagente.radar.tests.factories import QUIBDO, make_event


# GeoNames columns, filled only where the loader reads them
def geoname(
    geonames_id: str,
    name: str,
    feature: str,
    admin1: str,
    admin2: str = "",
    population: str = "0",
    latitude: str = "4.81",
    longitude: str = "-75.69",
) -> list[str]:
    """One row of a GeoNames country dump."""
    row = [""] * 19
    row[0], row[1] = geonames_id, name
    row[4], row[5] = latitude, longitude
    row[7] = feature
    row[10], row[11] = admin1, admin2
    row[14] = population
    return row


DUMP = [
    geoname("1", "Risaralda", "ADM1", "66", population="961055"),
    geoname("2", "Chocó", "ADM1", "27", population="544764", latitude="5.69", longitude="-76.66"),
    geoname(
        "3", "Bogotá D.C.", "ADM1", "11", population="7968095", latitude="4.71", longitude="-74.07"
    ),
    geoname("4", "Pereira", "ADM2", "66", "66001", population="481128"),
    geoname(
        "5",
        "Quibdó",
        "ADM2",
        "27",
        "27001",
        population="130825",
        latitude="5.69",
        longitude="-76.66",
    ),
    geoname(
        "6",
        "Bogotá",
        "ADM2",
        "11",
        "11001",
        population="7968095",
        latitude="4.71",
        longitude="-74.07",
    ),
    geoname("7", "Cerro Tatamá", "MT", "66"),  # not an administrative division
    geoname("8", "Huérfano", "ADM2", "99", "99001"),  # its department is not in the dump
]


class GazetteerTests(TestCase):
    def test_it_loads_both_levels_and_parents_the_second_to_the_first(self):
        result = load_country("CO", rows=DUMP)

        self.assertEqual(result.created, 6)
        pereira = AdminUnit.objects.get(country_code="CO", code="66001")
        self.assertEqual(pereira.level, AdminLevel.ADMIN_2)
        assert pereira.parent is not None
        self.assertEqual(pereira.parent.name, "Risaralda")

    def test_features_that_are_not_administrative_divisions_are_left_out(self):
        load_country("CO", rows=DUMP)

        self.assertFalse(AdminUnit.objects.filter(name="Cerro Tatamá").exists())

    def test_a_municipality_whose_department_is_missing_is_skipped_not_orphaned(self):
        result = load_country("CO", rows=DUMP)

        self.assertEqual(result.skipped, 1)
        self.assertFalse(AdminUnit.objects.filter(name="Huérfano").exists())

    def test_reloading_updates_in_place_rather_than_duplicating(self):
        load_country("CO", rows=DUMP)
        renamed = [row[:] for row in DUMP]
        renamed[3][1] = "Pereira (Risaralda)"

        result = load_country("CO", rows=renamed)

        self.assertEqual(result.created, 0)
        self.assertEqual(AdminUnit.objects.count(), 6)
        self.assertEqual(AdminUnit.objects.get(code="66001").name, "Pereira (Risaralda)")

    def test_population_and_centroid_are_stored_for_cold_start_ranking(self):
        load_country("CO", rows=DUMP)

        bogota = AdminUnit.objects.get(code="11001")
        self.assertEqual(bogota.population, 7968095)
        self.assertIsNotNone(bogota.centroid)


class SearchNameTests(TestCase):
    """Every sweep query is built from these strings, so a bad one finds nothing."""

    def test_the_administrative_long_form_is_shortened_to_what_people_write(self):
        self.assertEqual(
            search_name("Departamento del Huila", "Huila,Departamento del Huila"), "Huila"
        )
        self.assertEqual(search_name("Distrito Capital de Bogotá", "Bogotá,Bogota"), "Bogotá")

    def test_a_shorter_alternate_that_is_not_a_whole_word_run_is_refused(self):
        # "Uila" sits inside "Huila" and is a real GeoNames alternate
        self.assertEqual(search_name("Departamento del Huila", "Uila,Huila"), "Huila")

    def test_stripping_the_edges_keeps_a_qualifier_the_name_needs(self):
        # "Valle" and "Cauca" are each a different place; only the whole phrase is right
        result = search_name(
            "Departamento del Valle del Cauca", "Valle,Cauca", {"departamento", "del", "de"}
        )

        self.assertEqual(result, "Valle del Cauca")

    def test_a_prefix_run_works_as_well_as_a_suffix_one(self):
        self.assertEqual(search_name("Quindío Department", "Quindío,Quindio"), "Quindío")

    def test_administrative_words_are_discovered_not_listed(self):
        # The same code has to work in a language nobody here reads
        indonesian = ["Kota Tebing Tinggi", "Kabupaten Tapanuli", "Kota Medan", "Kabupaten Deli"]

        common = administrative_words(indonesian)

        self.assertEqual(common, {"kota", "kabupaten"})
        self.assertEqual(search_name("Kota Tebing Tinggi", "", common), "Tebing Tinggi")

    def test_a_word_that_names_a_place_survives_the_frequency_test(self):
        philippine = [f"Province of {name}" for name in ("Zambales", "Tawi-Tawi", "Cebu", "Bohol")]

        common = administrative_words(philippine)

        self.assertEqual(common, {"province", "of"})
        self.assertNotIn("zambales", common)

    def test_a_name_with_no_usable_alternate_is_left_alone(self):
        self.assertEqual(search_name("Amazonas", "Amazonas"), "Amazonas")
        self.assertEqual(search_name("Zipaquirá", ""), "Zipaquirá")

    def test_stray_whitespace_is_collapsed(self):
        self.assertEqual(search_name("Bogotá  D.C.", ""), "Bogotá D.C.")


class ZoneTests(TestCase):
    def setUp(self):
        load_country("CO", rows=DUMP)
        self.event = make_event(epicenter=QUIBDO, country_code="CO")

    def test_places_near_the_epicenter_are_the_impact_zone(self):
        names = {unit.name for unit in places_by_zone(self.event, Zone.IMPACT)}

        self.assertIn("Pereira", names)
        self.assertIn("Risaralda", names)
        self.assertNotIn("Bogotá", names)

    def test_the_rest_of_the_country_is_where_supply_comes_from(self):
        names = {unit.name for unit in places_by_zone(self.event, Zone.SUPPORT)}

        self.assertIn("Bogotá", names)
        self.assertNotIn("Pereira", names)

    def test_the_impact_zone_is_ranked_by_proximity_not_size(self):
        # A set, because the two share a centroid and their order is a coin flip
        names = [unit.name for unit in places_by_zone(self.event, Zone.IMPACT)]

        self.assertEqual(set(names[:2]), {"Chocó", "Quibdó"})

    def test_the_support_zone_is_ranked_by_size(self):
        units = list(places_by_zone(self.event, Zone.SUPPORT))

        populations = [unit.population or 0 for unit in units]
        self.assertEqual(populations, sorted(populations, reverse=True))

    def test_with_no_epicenter_everything_is_impact(self):
        event = make_event(name="Sin epicentro", epicenter=None, country_code="CO")

        self.assertTrue(places_by_zone(event, Zone.IMPACT).exists())
        self.assertFalse(places_by_zone(event, Zone.SUPPORT).exists())

    def test_a_declared_impact_area_beats_the_radius(self):
        # A circle is a guess; affected_units is a statement about this disaster
        risaralda = AdminUnit.objects.get(code="66")
        self.event.affected_units.set([risaralda])

        impact = {unit.name for unit in places_by_zone(self.event, Zone.IMPACT)}
        support = {unit.name for unit in places_by_zone(self.event, Zone.SUPPORT)}

        self.assertEqual(impact, {"Risaralda", "Pereira"})
        self.assertIn("Chocó", support)  # inside the radius, but not declared

    def test_declaring_a_department_carries_its_municipalities(self):
        self.event.affected_units.set([AdminUnit.objects.get(code="27")])

        names = {unit.name for unit in places_by_zone(self.event, Zone.IMPACT)}

        self.assertEqual(names, {"Chocó", "Quibdó"})

    def test_the_country_is_the_outer_bound_either_way(self):
        AdminUnit.objects.create(
            country_code="PE", code="15", name="Lima", name_norm="lima", level=AdminLevel.ADMIN_1
        )

        for zone in (Zone.IMPACT, Zone.SUPPORT):
            with self.subTest(zone=zone):
                codes = {unit.country_code for unit in places_by_zone(self.event, zone)}
                self.assertNotIn("PE", codes)


class SweepQueryTests(TestCase):
    def setUp(self):
        load_country("CO", rows=DUMP)
        self.event = make_event(epicenter=QUIBDO, country_code="CO")
        self.event.lexicon = {
            "hashtags": ["#SismoChocó"],
            "demand": ["necesitamos"],
            "supply": ["punto de acopio"],
            "negatives": ["Perú", "Indonesia"],
        }
        self.event.save(update_fields=["lexicon"])

    def _query(self, zone: str) -> str:
        units = list(places_by_zone(self.event, zone)[:TOPONYMS_PER_QUERY])
        return build_input("x", sweep_query(self.event, units, zone))["searchTerms"][0]

    def test_one_query_carries_many_toponyms(self):
        query = self._query(Zone.IMPACT)

        self.assertIn('"Pereira"', query)
        self.assertIn('"Risaralda"', query)
        self.assertGreaterEqual(query.count(" OR "), 2)

    def test_the_axis_vocabulary_follows_the_zone(self):
        self.assertIn('"necesitamos"', self._query(Zone.IMPACT))
        self.assertIn('"punto de acopio"', self._query(Zone.SUPPORT))
        self.assertNotIn('"necesitamos"', self._query(Zone.SUPPORT))

    def test_other_emergencies_are_excluded(self):
        query = self._query(Zone.IMPACT)

        self.assertIn('-"Perú"', query)
        self.assertIn('-"Indonesia"', query)

    def test_a_query_never_leaves_without_a_toponym(self):
        # Invariant 9: without one it pulls in every other country's disaster
        for zone in (Zone.IMPACT, Zone.SUPPORT):
            with self.subTest(zone=zone):
                units = list(places_by_zone(self.event, zone)[:TOPONYMS_PER_QUERY])
                query = build_input("x", sweep_query(self.event, units, zone))["searchTerms"][0]
                self.assertTrue(any(f'"{unit.name}"' in query for unit in units))


class BootstrapTests(TestCase):
    def setUp(self):
        load_country("CO", rows=DUMP)
        self.event = make_event(epicenter=QUIBDO, country_code="CO")

    def test_a_place_sitting_on_the_epicentre_still_gets_a_node(self):
        epicentre_unit = AdminUnit.objects.filter(country_code="CO").order_by("id")[0]
        self.event.epicenter = epicentre_unit.centroid
        self.event.save(update_fields=["epicenter"])

        bootstrap_event(self.event, platforms=["x"])

        node = FrontierNode.objects.get(admin_unit=epicentre_unit, platform="x")
        self.assertEqual(node.distance_km, 0)  # zero is a distance, not a missing one

    def test_it_creates_watch_targets_and_queues_the_sweep(self):
        counts = bootstrap_event(self.event, platforms=["x", "facebook"])

        self.assertEqual(counts["nodes"], FrontierNode.objects.count())
        self.assertTrue(counts["nodes"] > 0)
        self.assertEqual(counts["jobs"], 4)  # two platforms, two zones

    def test_the_whole_country_costs_a_handful_of_queries_not_one_per_place(self):
        places = AdminUnit.objects.filter(country_code="CO").count()

        bootstrap_event(self.event, platforms=["x"])

        jobs = HarvestJob.objects.count()
        self.assertLess(jobs, places)
        self.assertEqual(jobs, 2)  # one per zone

    def test_sweep_jobs_carry_no_node_because_one_query_spans_many_places(self):
        bootstrap_event(self.event, platforms=["x"])

        for job in HarvestJob.objects.all():
            self.assertIsNone(job.node)
            self.assertEqual(job.decided_by, DecisionSource.RULE)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertTrue(job.rationale)

    def test_nodes_carry_the_zone_and_the_distance_to_the_epicenter(self):
        bootstrap_event(self.event, platforms=["x"])

        pereira = FrontierNode.objects.get(admin_unit__code="66001", platform="x")
        bogota = FrontierNode.objects.get(admin_unit__code="11001", platform="x")
        self.assertEqual(pereira.zone, Zone.IMPACT)
        self.assertEqual(bogota.zone, Zone.SUPPORT)
        self.assertIsNotNone(pereira.distance_km)

    def test_running_it_twice_creates_nothing(self):
        bootstrap_event(self.event, platforms=["x"])

        counts = bootstrap_event(self.event, platforms=["x"])

        self.assertEqual(counts, {"nodes": 0, "jobs": 0})

    def test_a_country_with_no_gazetteer_is_refused_rather_than_swept_blind(self):
        event = make_event(name="Sismo Perú", country_code="PE", epicenter=Point(-77.0, -12.0))

        with self.assertRaises(ValueError) as caught:
            bootstrap_event(event)

        self.assertIn("load_gazetteer", str(caught.exception))
