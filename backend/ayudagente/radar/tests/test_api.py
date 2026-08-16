"""
Endpoint tests for the resource API: the lists a map and a feed are built from.

The filters get most of the attention here. A filter that is silently ignored returns a
plausible page of the wrong rows, which is the failure nobody notices until a coordinator acts
on it — so every one of them is asserted to narrow the result, and every bad value to be a 400
rather than a default.
"""

from decimal import Decimal

from django.urls import reverse

from ayudagente.radar.choices import (
    ActorKind,
    ContactKind,
    Direction,
    LocationPrecision,
    MatchStatus,
    OutreachStatus,
    Platform,
    RequirementStatus,
    Urgency,
)
from ayudagente.radar.services import propose_match
from ayudagente.radar.tests.factories import (
    CALI,
    DOSQUEBRADAS,
    PEREIRA,
    QUIBDO,
    ApiTestCase,
    make_actor,
    make_contact,
    make_event,
    make_location,
    make_media,
    make_observation,
    make_outreach,
    make_requirement,
    make_resource,
)


class EventDetailTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")

    def _get(self):
        return self.client.get(reverse("radar:event-detail", args=[self.event.id])).json()

    def test_the_summary_counts_open_work_not_history(self):
        actor = make_actor(self.event, "Barrio Cuba")
        make_requirement(self.event, actor, self.water, make_location(PEREIRA, "cuba"))
        make_requirement(
            self.event,
            actor,
            self.water,
            make_location(DOSQUEBRADAS, "centro"),
            direction=Direction.OFFERS,
        )
        make_requirement(
            self.event,
            actor,
            self.water,
            make_location(CALI, "cali"),
            status=RequirementStatus.COVERED,
        )

        summary = self._get()["summary"]

        self.assertEqual(summary["needs"], 1)
        self.assertEqual(summary["offers"], 1)
        self.assertEqual(summary["requirements"], 3)  # the covered one still counts here
        self.assertEqual(summary["actors"], 1)

    def test_unread_observations_report_the_pipeline_backlog(self):
        make_observation(self.event)
        make_observation(self.event)

        summary = self._get()["summary"]

        self.assertEqual(summary["observations"], 2)
        self.assertEqual(summary["unread_observations"], 2)

    def test_an_unknown_event_is_a_404(self):
        response = self.client.get(reverse("radar:event-detail", args=[9999]))

        self.assertEqual(response.status_code, 404)


class RequirementListTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")
        self.food = make_resource("food", "Alimentos")
        self.actor = make_actor(self.event, "Barrio Cuba")

        self.need = make_requirement(
            self.event,
            self.actor,
            self.water,
            make_location(PEREIRA, "barrio cuba"),
            urgency=Urgency.CRITICAL,
        )
        self.offer = make_requirement(
            self.event,
            make_actor(self.event, "Centro", kind=ActorKind.COLLECTION_CENTER),
            self.food,
            make_location(DOSQUEBRADAS, "centro"),
            direction=Direction.OFFERS,
            urgency=Urgency.LOW,
        )
        self.url = reverse("radar:requirement-list", args=[self.event.id])

    def _get(self, **params):
        return self.client.get(self.url, params)

    def test_it_returns_open_work_with_the_actor_and_the_location(self):
        payload = self._get().json()

        self.assertEqual(payload["count"], 2)
        row = next(r for r in payload["results"] if r["id"] == self.need.id)
        self.assertEqual(row["actor"]["name"], "Barrio Cuba")
        self.assertEqual(row["location"]["point"], {"lat": PEREIRA.y, "lon": PEREIRA.x})
        self.assertEqual(row["location"]["precision"], LocationPrecision.NEIGHBORHOOD)

    def test_covered_requirements_are_hidden_unless_asked_for(self):
        make_requirement(
            self.event,
            self.actor,
            self.water,
            make_location(QUIBDO, "quibdó"),
            status=RequirementStatus.COVERED,
        )

        self.assertEqual(self._get().json()["count"], 2)
        self.assertEqual(self._get(status="covered").json()["count"], 1)

    def test_the_default_order_puts_the_most_urgent_first(self):
        # Critical must beat low, which sorting the raw string would get backwards
        results = self._get().json()["results"]

        self.assertEqual([r["urgency"] for r in results], ["critical", "low"])

    def test_direction_resource_and_actor_kind_each_narrow_the_result(self):
        for params, expected in (
            ({"direction": "needs"}, self.need.id),
            ({"resource": "food"}, self.offer.id),
            ({"actor_kind": "collection_center"}, self.offer.id),
            ({"urgency": "critical"}, self.need.id),
            ({"q": "Barrio"}, self.need.id),
        ):
            with self.subTest(params=params):
                results = self._get(**params).json()["results"]
                self.assertEqual([r["id"] for r in results], [expected])

    def test_a_bbox_keeps_only_what_falls_inside_it(self):
        response = self._get(bbox="-75.71,4.80,-75.68,4.82")

        self.assertEqual([r["id"] for r in response.json()["results"]], [self.need.id])

    def test_a_radius_finds_what_is_near_a_pin(self):
        response = self._get(near=f"{PEREIRA.y},{PEREIRA.x}", radius_km="1")

        self.assertEqual([r["id"] for r in response.json()["results"]], [self.need.id])

    def test_a_minimum_precision_excludes_a_point_that_is_a_whole_country(self):
        make_requirement(
            self.event,
            self.actor,
            self.water,
            make_location(CALI, "colombia", precision=LocationPrecision.COUNTRY),
        )

        payload = self._get(min_precision="neighborhood").json()

        self.assertEqual(payload["count"], 2)

    def test_paging_reports_the_total_not_the_page(self):
        payload = self._get(limit="1").json()

        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["limit"], 1)

    def test_a_bad_filter_is_a_400_that_names_what_was_expected(self):
        for params in (
            {"status": "nonsense"},
            {"direction": "wants"},
            {"order": "sideways"},
            {"limit": "-3"},
            {"bbox": "1,2,3"},
            {"bbox": "-75,4.9,-76,4.8"},  # corners reversed
            {"near": "4.8"},
            {"near": "4.8,-75.6", "radius_km": "0"},
        ):
            with self.subTest(params=params):
                response = self._get(**params)
                self.assertEqual(response.status_code, 400, params)
                self.assertIn("error", response.json())

    def test_bbox_and_near_together_are_refused_rather_than_intersected(self):
        response = self._get(bbox="-76,4,-75,5", near="4.8,-75.6")

        self.assertEqual(response.status_code, 400)

    def test_the_query_count_does_not_grow_with_the_page(self):
        for index in range(10):
            make_requirement(
                self.event,
                make_actor(self.event, f"Actor {index}"),
                self.water,
                make_location(PEREIRA, f"sitio {index}"),
            )

        with self.assertNumQueries(3):  # the event, the page with its joins, the total
            self.client.get(self.url, {"limit": "50"})


class RequirementDetailTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")
        self.requirement = make_requirement(
            self.event,
            make_actor(self.event, "Barrio Cuba"),
            self.water,
            make_location(PEREIRA, "barrio cuba"),
        )

    def test_the_evidence_carries_the_permalink_and_the_image(self):
        observation = make_observation(self.event, "no hay agua en el barrio")
        make_media(observation)
        self.requirement.evidence.add(observation)

        payload = self.client.get(
            reverse("radar:requirement-detail", args=[self.requirement.id])
        ).json()

        evidence = payload["evidence"][0]
        self.assertEqual(evidence["permalink"], observation.permalink)
        self.assertEqual(evidence["author"]["handle"], "@vecino")
        self.assertEqual(evidence["media"][0]["url"], "/media/pilot/photo.jpg")
        self.assertEqual(evidence["media"][0]["alt_text"], "Puente colapsado")

    def test_several_posts_can_back_one_requirement(self):
        for index in range(3):
            self.requirement.evidence.add(make_observation(self.event, f"post {index}"))

        payload = self.client.get(
            reverse("radar:requirement-detail", args=[self.requirement.id])
        ).json()

        self.assertEqual(len(payload["evidence"]), 3)

    def test_a_requirement_sees_the_matches_on_both_of_its_sides(self):
        offer = make_requirement(
            self.event,
            make_actor(self.event, "Centro", kind=ActorKind.COLLECTION_CENTER),
            self.water,
            make_location(DOSQUEBRADAS, "centro"),
            direction=Direction.OFFERS,
        )
        propose_match(self.requirement, offer)

        for requirement_id in (self.requirement.id, offer.id):
            with self.subTest(requirement=requirement_id):
                payload = self.client.get(
                    reverse("radar:requirement-detail", args=[requirement_id])
                ).json()
                self.assertEqual(len(payload["matches"]), 1)


class ActorTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")
        self.actor = make_actor(self.event, "Coliseo Mayor", kind=ActorKind.COLLECTION_CENTER)

    def test_the_list_says_whether_an_actor_can_be_reached_without_listing_its_numbers(self):
        make_contact(self.actor)

        row = self.client.get(reverse("radar:actor-list", args=[self.event.id])).json()["results"][
            0
        ]

        self.assertEqual(row["contact_count"], 1)
        self.assertTrue(row["can_be_reached"])
        self.assertNotIn("contacts", row)

    def test_a_payment_account_is_not_a_way_to_be_reached(self):
        make_contact(self.actor, "3002377012", kind=ContactKind.PAYMENT, payment_network="nequi")

        row = self.client.get(reverse("radar:actor-list", args=[self.event.id])).json()["results"][
            0
        ]

        self.assertEqual(row["contact_count"], 1)
        self.assertFalse(row["can_be_reached"])

    def test_the_kind_filter_narrows_and_organizations_excludes_people(self):
        make_actor(self.event, "Vecina", kind=ActorKind.PERSON)
        url = reverse("radar:actor-list", args=[self.event.id])

        self.assertEqual(self.client.get(url).json()["count"], 2)
        self.assertEqual(self.client.get(url, {"kind": "person"}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"organizations": "true"}).json()["count"], 1)

    def test_the_detail_ranks_contacts_by_how_a_human_should_use_them(self):
        make_contact(self.actor, "centro@example.org", kind=ContactKind.EMAIL)
        make_contact(self.actor, "+573002377012", kind=ContactKind.WHATSAPP)

        payload = self.client.get(reverse("radar:actor-detail", args=[self.actor.id])).json()

        self.assertEqual([c["kind"] for c in payload["contacts"]], ["email", "whatsapp"])
        self.assertTrue(payload["contacts"][0]["can_carry_a_message"])

    def test_a_merged_actor_resolves_to_the_one_it_was_merged_into(self):
        # Chained on purpose: merges chain, and following one hop lands on a retired actor
        middle = make_actor(self.event, "Coliseo", merged_into=self.actor)
        oldest = make_actor(self.event, "el coliseo", merged_into=middle)

        payload = self.client.get(reverse("radar:actor-detail", args=[oldest.id])).json()

        self.assertEqual(payload["id"], self.actor.id)
        self.assertEqual(payload["requested_id"], oldest.id)

    def test_the_detail_lists_what_the_actor_needs_or_offers(self):
        make_requirement(self.event, self.actor, self.water, make_location(PEREIRA, "coliseo"))

        payload = self.client.get(reverse("radar:actor-detail", args=[self.actor.id])).json()

        self.assertEqual(payload["requirements"][0]["resource_key"], "water")


class ProposalTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")
        self.need_actor = make_actor(self.event, "Barrio Cuba")
        self.need = make_requirement(
            self.event, self.need_actor, self.water, make_location(PEREIRA, "cuba")
        )
        self.offer = make_requirement(
            self.event,
            make_actor(self.event, "Centro", kind=ActorKind.COLLECTION_CENTER),
            self.water,
            make_location(DOSQUEBRADAS, "centro"),
            direction=Direction.OFFERS,
            quantity=Decimal(200),
        )

    def test_a_match_row_names_both_sides_without_a_second_request(self):
        propose_match(self.need, self.offer)

        row = self.client.get(reverse("radar:match-list", args=[self.event.id])).json()["results"][
            0
        ]

        self.assertEqual(row["need"]["actor"]["name"], "Barrio Cuba")
        self.assertEqual(row["offer"]["actor"]["name"], "Centro")
        self.assertEqual(row["offer"]["resource_key"], "water")

    def test_a_discarded_match_is_not_shown_unless_asked_for(self):
        match = propose_match(self.need, self.offer)
        assert match is not None
        match.status = MatchStatus.DISCARDED
        match.save()
        url = reverse("radar:match-list", args=[self.event.id])

        self.assertEqual(self.client.get(url).json()["count"], 0)
        self.assertEqual(self.client.get(url, {"status": "discarded"}).json()["count"], 1)

    def test_outreach_defaults_to_the_drafts_still_waiting_for_a_person(self):
        contact = make_contact(self.need_actor)
        make_outreach(self.need_actor, contact)
        make_outreach(self.need_actor, contact, status=OutreachStatus.DISPATCHED)
        url = reverse("radar:outreach-list", args=[self.event.id])

        payload = self.client.get(url).json()

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["status"], "draft")
        self.assertEqual(self.client.get(url, {"status": "dispatched"}).json()["count"], 1)

    def test_every_draft_carries_the_link_that_is_the_send_button(self):
        make_outreach(self.need_actor, make_contact(self.need_actor))

        row = self.client.get(reverse("radar:outreach-list", args=[self.event.id])).json()[
            "results"
        ][0]

        self.assertTrue(row["target_url"].startswith("https://wa.me/"))
        self.assertTrue(row["text_is_prefilled"])
        self.assertEqual(row["target_actor"]["id"], self.need_actor.id)


class ObservationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.url = reverse("radar:observation-list", args=[self.event.id])

    def test_the_feed_is_newest_first_and_carries_the_media(self):
        older = make_observation(self.event, "primero")
        newer = make_observation(self.event, "segundo")
        newer.posted_at = older.posted_at.replace(year=older.posted_at.year + 1)
        newer.save()
        make_media(newer)

        results = self.client.get(self.url).json()["results"]

        self.assertEqual([r["id"] for r in results], [newer.id, older.id])
        self.assertEqual(results[0]["media"][0]["alt_text"], "Puente colapsado")

    def test_the_platform_media_and_text_filters_each_narrow_the_feed(self):
        with_photo = make_observation(self.event, "hay un derrumbe")
        make_media(with_photo)
        make_observation(self.event, "sin foto", platform=Platform.FACEBOOK)

        for params, expected in (
            ({"has_media": "true"}, 1),
            ({"platform": "facebook"}, 1),
            ({"q": "derrumbe"}, 1),
            ({"unread": "true"}, 2),
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.url, params).json()["count"], expected)

    def test_an_unknown_platform_is_a_400(self):
        response = self.client.get(self.url, {"platform": "myspace"})

        self.assertEqual(response.status_code, 400)

    def test_the_detail_shows_what_was_read_and_what_it_produced(self):
        observation = make_observation(self.event, "necesitamos agua en cuba")
        requirement = make_requirement(
            self.event,
            make_actor(self.event, "Barrio Cuba"),
            make_resource("water", "Agua"),
            make_location(PEREIRA, "cuba"),
        )
        requirement.evidence.add(observation)

        payload = self.client.get(reverse("radar:observation-detail", args=[observation.id])).json()

        self.assertEqual(payload["text"], "necesitamos agua en cuba")
        self.assertIsNone(payload["extraction"])  # never read by the pipeline in this test
        self.assertEqual(payload["requirements"][0]["id"], requirement.id)


class ResourceCatalogTests(ApiTestCase):
    def test_it_lists_the_keys_a_filter_menu_needs(self):
        food = make_resource("food", "Alimentos")
        make_resource("water_drinking", "Agua potable", parent=food, default_unit="L")

        payload = self.client.get(reverse("radar:resource-type-list")).json()

        by_key = {r["key"]: r for r in payload["resource_types"]}
        self.assertEqual(by_key["water_drinking"]["parent"], "food")
        self.assertEqual(by_key["water_drinking"]["default_unit"], "L")
