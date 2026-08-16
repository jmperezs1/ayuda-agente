"""
Tests for the frontier reshaping itself from what the harvest learned.

The frontier starts as a list of places because at minute zero that is all anyone knows. What
the sweep then discovers is accounts, and until this existed nothing turned that discovery into
a target — the agent could allocate depth to an account, but no account was ever added.

The retirement half is what keeps the list readable, and its reversibility is what keeps it
honest: a municipality nobody was posting about at midnight can be the story at dawn.
"""

from django.test import TestCase

from ayudagente.radar.choices import (
    ContactKind,
    Direction,
    ExtractionClass,
    NodeStatus,
    Platform,
    Zone,
)
from ayudagente.radar.models import AdminUnit, ContactPoint, Extraction, FrontierNode
from ayudagente.radar.services.promotion import (
    EXHAUST_AFTER_PASSES,
    PROVEN_POSTS,
    promote_accounts,
    retire_exhausted,
)
from ayudagente.radar.tests.factories import (
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_observation,
    make_requirement,
    make_resource,
)


class PromotionBase(TestCase):
    def setUp(self):
        self.event = make_event()
        self.water = make_resource("water", "Agua")

    def _actor_with_handle(self, handle: str, platform: str = Platform.TIKTOK):
        actor = make_actor(self.event, f"Colectivo {handle}")
        ContactPoint.objects.create(
            actor=actor, kind=ContactKind.HANDLE, platform=platform, value=handle
        )
        return actor

    def _posts(self, handle: str, count: int, platform: str = Platform.TIKTOK):
        for index in range(count):
            observation = make_observation(
                self.event,
                f"post {handle} {index}",
                platform=platform,
                platform_id=f"{handle}-{index}",
                author_handle=handle,
            )
            Extraction.objects.create(
                observation=observation,
                model="test",
                prompt_version="v8",
                classification=ExtractionClass.OFFER,
                confidence=0.9,
                payload={},
            )


class PromotionTests(PromotionBase):
    def test_an_account_that_keeps_producing_earns_its_own_node(self):
        actor = self._actor_with_handle("acopiopereira")
        self._posts("acopiopereira", PROVEN_POSTS)

        self.assertEqual(promote_accounts(self.event), 1)

        node = FrontierNode.objects.get(actor=actor)
        self.assertIsNone(node.admin_unit)
        self.assertEqual(node.platform, Platform.TIKTOK)

    def test_one_good_post_is_not_a_pattern(self):
        self._actor_with_handle("unavez")
        self._posts("unavez", PROVEN_POSTS - 1)

        self.assertEqual(promote_accounts(self.event), 0)

    def test_a_handle_with_no_actor_behind_it_is_left_alone(self):
        # Inventing an Actor would put an aggregator on the map as a place to go
        self._posts("prensaquenoesactor", PROVEN_POSTS + 2)

        self.assertEqual(promote_accounts(self.event), 0)
        self.assertFalse(FrontierNode.objects.exists())

    def test_promoting_twice_does_not_duplicate_the_node(self):
        self._actor_with_handle("acopiopereira")
        self._posts("acopiopereira", PROVEN_POSTS)
        promote_accounts(self.event)

        self.assertEqual(promote_accounts(self.event), 0)
        self.assertEqual(FrontierNode.objects.count(), 1)

    def test_an_account_that_mostly_offers_lands_on_the_supply_side(self):
        actor = self._actor_with_handle("bodega")
        self._posts("bodega", PROVEN_POSTS)
        make_requirement(
            self.event,
            actor,
            self.water,
            make_location(PEREIRA, "bodega"),
            direction=Direction.OFFERS,
        )

        promote_accounts(self.event)

        self.assertEqual(FrontierNode.objects.get(actor=actor).zone, Zone.SUPPORT)

    def test_an_account_with_no_requirements_yet_defaults_to_impact(self):
        # Missing a need costs more than missing an offer, so the tie breaks that way
        actor = self._actor_with_handle("nuevo")
        self._posts("nuevo", PROVEN_POSTS)

        promote_accounts(self.event)

        self.assertEqual(FrontierNode.objects.get(actor=actor).zone, Zone.IMPACT)

    def test_discarded_posts_do_not_prove_an_account(self):
        self._actor_with_handle("ruido")
        for index in range(PROVEN_POSTS + 2):
            observation = make_observation(
                self.event,
                f"ruido {index}",
                platform=Platform.TIKTOK,
                platform_id=f"ruido-{index}",
                author_handle="ruido",
            )
            Extraction.objects.create(
                observation=observation,
                model="test",
                prompt_version="v8",
                classification=ExtractionClass.DISCARD,
                confidence=0.9,
                payload={},
            )

        self.assertEqual(promote_accounts(self.event), 0)


class RetirementTests(PromotionBase):
    def _place(self, name: str, **counters) -> FrontierNode:
        defaults = {"passes": EXHAUST_AFTER_PASSES, "total_items": 100, "actionable_items": 0}
        unit = AdminUnit.objects.create(
            country_code="CO",
            code=f"27{abs(hash(name)) % 900 + 100}",
            name=name,
            name_norm=name.lower(),
            level="admin_2",
            centroid=PEREIRA,
        )
        return FrontierNode.objects.create(
            event=self.event,
            admin_unit=unit,
            actor=None,
            platform=Platform.TIKTOK,
            zone=Zone.IMPACT,
            **{**defaults, **counters},
        )

    def test_a_place_that_answered_nothing_is_retired(self):
        node = self._place("Silencio")

        self.assertEqual(retire_exhausted(self.event), 1)

        node.refresh_from_db()
        self.assertEqual(node.status, NodeStatus.EXHAUSTED)

    def test_a_place_still_producing_is_left_active(self):
        self._place("Activo", actionable_items=3)

        self.assertEqual(retire_exhausted(self.event), 0)

    def test_a_place_given_too_few_passes_is_left_active(self):
        self._place("Joven", passes=EXHAUST_AFTER_PASSES - 1)

        self.assertEqual(retire_exhausted(self.event), 0)

    def test_a_node_that_fetched_nothing_at_all_is_a_bug_not_an_answer(self):
        # A dead Actor or a rejected query, and calling it exhausted hides the cause
        self._place("Roto", total_items=0)

        self.assertEqual(retire_exhausted(self.event), 0)

    def test_a_promoted_account_is_never_retired_for_going_quiet(self):
        actor = self._actor_with_handle("alcaldia")
        FrontierNode.objects.create(
            event=self.event,
            admin_unit=None,
            actor=actor,
            platform=Platform.TIKTOK,
            zone=Zone.IMPACT,
            passes=EXHAUST_AFTER_PASSES + 5,
            total_items=100,
            actionable_items=0,
        )

        self.assertEqual(retire_exhausted(self.event), 0)
