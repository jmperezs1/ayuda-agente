"""
Tests for translating a search into what each Apify Actor actually accepts.

These exist because the first live run cost money and returned nothing. The code sent
`{"searchQuery": ...}` to four Actors that between them accept `searchTerms`, `hashtags`,
`query` and `searchQueries`; three ignored it and the fourth returned ten rows of
`{"noResults": true}`. The job was billed and marked `done`.

Every assertion here is a field name checked against a real Actor schema. They are dull on
purpose — a typo in one of them is a silent, paid-for, empty night.
"""

from datetime import date

from django.test import SimpleTestCase

from ayudagente.radar.choices import Platform
from ayudagente.radar.services.apify_inputs import (
    APIFY_ACTOR_BY_PLATFORM,
    MIN_ITEMS_PER_TERM,
    Query,
    build_input,
)

QUERY = Query(
    toponyms=["Quibdó", "Chocó", "Atrato", "Lloró", "Cértegui", "Río Quito"],
    axis_terms=["necesitamos", "urgente"],
    hashtags=["#SismoColombia"],
    negatives=["Perú", "Indonesia"],
    limit=100,
    language="es",
    since=date(2026, 8, 15),
)


class ContractTests(SimpleTestCase):
    """Every platform gets the fields its Actor declares, and none it does not."""

    def test_x_batches_into_one_search_term(self):
        payload = build_input(Platform.X, QUERY)

        self.assertEqual(len(payload["searchTerms"]), 1)
        self.assertIn('"Quibdó" OR "Chocó"', payload["searchTerms"][0])
        self.assertIn('-"Perú"', payload["searchTerms"][0])
        self.assertEqual(payload["lang"], "es")
        self.assertEqual(payload["queryType"], "Latest")

    def test_instagram_terms_are_stripped_of_punctuation_it_refuses(self):
        # "Bogotá D.C." failed validation and took the whole support-zone sweep with it
        payload = build_input(Platform.INSTAGRAM, Query(toponyms=["Bogotá D.C.", "Chocó"]))

        self.assertEqual(payload["hashtags"], ["Bogotá DC", "Chocó"])

    def test_instagram_refuses_a_query_no_term_survives(self):
        with self.assertRaises(ValueError):
            build_input(Platform.INSTAGRAM, Query(toponyms=["...", "---"]))

    def test_instagram_searches_keywords_because_toponyms_are_multi_word(self):
        payload = build_input(Platform.INSTAGRAM, QUERY)

        self.assertTrue(payload["keywordSearch"])
        self.assertEqual(payload["resultsType"], "posts")
        self.assertIn("Quibdó", payload["hashtags"])

    def test_facebook_gets_one_fuzzy_string_and_no_operators(self):
        payload = build_input(Platform.FACEBOOK, QUERY)

        self.assertIsInstance(payload["query"], str)
        self.assertIn("Quibdó", payload["query"])
        self.assertNotIn("OR", payload["query"])
        self.assertNotIn("Perú", payload["query"])  # no exclusion syntax; it would search it
        self.assertEqual(payload["startDate"], "2026-08-15")

    def test_tiktok_runs_one_search_per_toponym(self):
        payload = build_input(Platform.TIKTOK, QUERY)

        self.assertGreater(len(payload["searchQueries"]), 1)
        self.assertTrue(all("OR" not in term for term in payload["searchQueries"]))
        self.assertIn("necesitamos", payload["searchQueries"][0])

    def test_no_platform_receives_the_field_that_failed_in_production(self):
        for platform in Platform.values:
            with self.subTest(platform=platform):
                self.assertNotIn("searchQuery", build_input(platform, QUERY))


class BudgetTests(SimpleTestCase):
    """The limit is per search term on three of four, so it has to be divided."""

    def test_a_batched_platform_divides_its_budget(self):
        instagram = build_input(Platform.INSTAGRAM, QUERY)
        tiktok = build_input(Platform.TIKTOK, QUERY)

        self.assertLess(instagram["resultsLimit"] * len(instagram["hashtags"]), QUERY.limit + 1)
        self.assertLess(tiktok["resultsPerPage"] * len(tiktok["searchQueries"]), QUERY.limit + 1)

    def test_a_single_term_platform_spends_the_whole_budget(self):
        self.assertEqual(build_input(Platform.FACEBOOK, QUERY)["resultsCount"], QUERY.limit)

    def test_x_never_asks_for_less_than_its_actor_accepts(self):
        payload = build_input(Platform.X, Query(toponyms=["Quibdó"], limit=5))

        self.assertEqual(payload["maxItems"], MIN_ITEMS_PER_TERM)

    def test_a_limit_is_never_rounded_down_to_nothing(self):
        payload = build_input(Platform.TIKTOK, Query(toponyms=["a", "b", "c", "d"], limit=2))

        self.assertGreaterEqual(payload["resultsPerPage"], 1)


class AnchorTests(SimpleTestCase):
    def test_a_query_with_no_toponym_is_refused(self):
        # Invariant 9: it would pull in every other country's disaster
        for platform in Platform.values:
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                build_input(platform, Query(toponyms=[]))

    def test_every_platform_carries_the_toponym(self):
        for platform in Platform.values:
            with self.subTest(platform=platform):
                payload = build_input(platform, QUERY)
                self.assertIn("Quibdó", str(payload))

    def test_an_unknown_platform_is_refused(self):
        with self.assertRaises(ValueError):
            build_input("myspace", QUERY)


class ActorTests(SimpleTestCase):
    def test_every_platform_points_at_an_actor_the_pilot_proved(self):
        self.assertEqual(set(APIFY_ACTOR_BY_PLATFORM), set(Platform.values))

    def test_facebook_does_not_point_at_the_actor_that_cannot_search(self):
        # `apify/facebook-posts-scraper` requires startUrls and reads pages, never searches
        self.assertNotEqual(
            APIFY_ACTOR_BY_PLATFORM[Platform.FACEBOOK], "apify/facebook-posts-scraper"
        )
