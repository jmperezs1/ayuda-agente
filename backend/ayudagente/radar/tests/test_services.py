"""Behavioral tests for the deterministic service layer (no network, no LLM)."""

from decimal import Decimal

from django.test import TestCase

from ayudagente.radar.choices import (
    ActorKind,
    ContactKind,
    Direction,
    LocationPrecision,
    MatchStatus,
)
from ayudagente.radar.models import ContactPoint, Match
from ayudagente.radar.services import (
    draft_outreach,
    find_requirements,
    propose_match,
    resource_family,
    run_matching_pass,
)
from ayudagente.radar.tests.factories import (
    CALI,
    DOSQUEBRADAS,
    PEREIRA,
    QUIBDO,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class ResourceFamilyTests(TestCase):
    def test_family_spans_ancestors_and_descendants(self):
        alimentos = make_resource("alimentos")
        mascotas = make_resource("alimentos_mascotas", parent=alimentos)
        agua = make_resource("agua")

        family = resource_family(mascotas)
        self.assertIn(alimentos.id, family)  # dog food can go to a generic food need
        self.assertIn(mascotas.id, family)
        self.assertNotIn(agua.id, family)

        family_up = resource_family(alimentos)
        self.assertIn(mascotas.id, family_up)  # generic offer covers the specific need


class FindRequirementsTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.alimentos = make_resource("alimentos")
        self.mascotas = make_resource("alimentos_mascotas", parent=self.alimentos)

    def test_orders_by_real_proximity_and_walks_the_tree(self):
        near_actor = make_actor(self.event, "Centro Pereira", kind=ActorKind.COLLECTION_CENTER)
        far_actor = make_actor(self.event, "Centro Quibdó", kind=ActorKind.COLLECTION_CENTER)
        near = make_requirement(
            self.event,
            near_actor,
            self.alimentos,
            make_location(DOSQUEBRADAS, "Centro Dosquebradas"),
        )
        far = make_requirement(
            self.event,
            far_actor,
            self.alimentos,
            make_location(QUIBDO, "Centro Quibdó"),
        )

        # Searching with the SPECIFIC resource (dog food) finds GENERIC food needs
        results = list(
            find_requirements(self.event.id, Direction.NEEDS, resource=self.mascotas, near=PEREIRA)
        )
        self.assertEqual([r.id for r in results], [near.id, far.id])

    def test_saturated_requirements_are_excluded(self):
        actor = make_actor(self.event, "Coliseo")
        make_requirement(
            self.event,
            actor,
            self.alimentos,
            make_location(PEREIRA, "Coliseo Mayor"),
            quantity=Decimal(20),
            covered_quantity=Decimal(20),
        )
        results = find_requirements(self.event.id, Direction.NEEDS, resource=self.alimentos)
        self.assertEqual(len(results), 0)

    def test_min_precision_filters_department_dots(self):
        actor = make_actor(self.event, "Chocó genérico")
        make_requirement(
            self.event,
            actor,
            self.alimentos,
            make_location(QUIBDO, "Chocó", precision=LocationPrecision.ADMIN_1),
        )
        results = find_requirements(
            self.event.id,
            Direction.NEEDS,
            min_precision=LocationPrecision.NEIGHBORHOOD,
        )
        self.assertEqual(len(results), 0)


class MatchingPassTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")
        self.transporte = make_resource("transporte")

    def test_allocation_prefers_near_offer_and_flags_unreachable(self):
        need_actor = make_actor(self.event, "Barrio Cuba")
        need = make_requirement(
            self.event,
            need_actor,
            self.agua,
            make_location(PEREIRA, "Barrio Cuba"),
            quantity=Decimal(100),
        )
        offer_near = make_requirement(
            self.event,
            make_actor(self.event, "Vecino Dosquebradas"),
            self.agua,
            make_location(DOSQUEBRADAS, "Dosquebradas"),
            direction=Direction.OFFERS,
            quantity=Decimal(100),
        )
        # Far offer with no transport: not a candidate
        make_requirement(
            self.event,
            make_actor(self.event, "Donante Cali"),
            self.agua,
            make_location(CALI, "Cali"),
            direction=Direction.OFFERS,
            quantity=Decimal(500),
        )
        # A need nobody can serve (no compatible offer)
        arena = make_resource("arena")
        unreachable = make_requirement(
            self.event,
            make_actor(self.event, "Sin suerte"),
            arena,
            make_location(QUIBDO, "Quibdó"),
        )

        result = run_matching_pass(self.event.id)

        self.assertEqual(len(result["proposed"]), 1)
        match = Match.objects.get(id=result["proposed"][0])
        self.assertEqual(match.need_id, need.id)
        self.assertEqual(match.offer_id, offer_near.id)
        self.assertEqual(match.committed_quantity, Decimal(100))
        self.assertIn(unreachable.id, result["unreachable_need_ids"])

    def test_far_pair_matches_only_through_transport(self):
        make_requirement(
            self.event,
            make_actor(self.event, "Quibdó centro"),
            self.agua,
            make_location(QUIBDO, "Quibdó centro"),
            quantity=Decimal(500),
        )
        make_requirement(
            self.event,
            make_actor(self.event, "Bodega Cali"),
            self.agua,
            make_location(CALI, "Bodega Cali"),
            direction=Direction.OFFERS,
            quantity=Decimal(500),
        )

        first = run_matching_pass(self.event.id)
        self.assertEqual(first["proposed"], [])  # far apart, nothing to move it

        # Truck based near the offer, destination open → solver may bridge them
        make_requirement(
            self.event,
            make_actor(self.event, "Transportador"),
            self.transporte,
            make_location(CALI, "Cali salida"),
            direction=Direction.OFFERS,
            quantity=Decimal(1),
        )
        second = run_matching_pass(self.event.id)
        self.assertEqual(len(second["proposed"]), 1)
        match = Match.objects.get(id=second["proposed"][0])
        self.assertIsNotNone(match.via_transport)

    def test_frozen_matches_survive_recomputation(self):
        need = make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            self.agua,
            make_location(PEREIRA, "Barrio"),
            quantity=Decimal(10),
        )
        offer = make_requirement(
            self.event,
            make_actor(self.event, "Vecino"),
            self.agua,
            make_location(DOSQUEBRADAS, "Vecino"),
            direction=Direction.OFFERS,
            quantity=Decimal(10),
        )
        match = propose_match(need, offer)
        assert match is not None
        match.status = MatchStatus.CONTACTED
        match.rationale = "texto que un humano ya vio"
        match.save()

        run_matching_pass(self.event.id)

        match.refresh_from_db()
        self.assertEqual(match.rationale, "texto que un humano ya vio")  # untouched
        self.assertEqual(match.status, MatchStatus.CONTACTED)  # not deleted as stale

    def test_propose_match_validates_directions(self):
        need = make_requirement(
            self.event,
            make_actor(self.event, "A"),
            self.agua,
            make_location(PEREIRA, "A"),
        )
        other_need = make_requirement(
            self.event,
            make_actor(self.event, "B"),
            self.agua,
            make_location(PEREIRA, "B"),
        )
        with self.assertRaises(ValueError):
            propose_match(need, other_need)


class OutreachTests(TestCase):
    def setUp(self):
        self.event = make_event()
        agua = make_resource("agua")
        self.need = make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            agua,
            make_location(PEREIRA, "Barrio"),
        )
        self.offer = make_requirement(
            self.event,
            make_actor(self.event, "ONG Pereira", kind=ActorKind.NONPROFIT),
            agua,
            make_location(DOSQUEBRADAS, "ONG"),
            direction=Direction.OFFERS,
        )
        self.match = propose_match(self.need, self.offer)

    def test_draft_is_idempotent(self):
        contact = ContactPoint.objects.create(
            actor=self.offer.actor,
            kind=ContactKind.EMAIL,
            value="ong@pereira.org",
            raw_value="ong@pereira.org",
            confidence=0.9,
        )
        first = draft_outreach(self.match, contact, body="hola")
        second = draft_outreach(self.match, contact, body="OTRO TEXTO")
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.body, "hola")  # retry did not overwrite

    def test_email_is_offered_before_whatsapp(self):
        org_email = ContactPoint.objects.create(
            actor=self.offer.actor,
            kind=ContactKind.EMAIL,
            value="ong@pereira.org",
            raw_value="ong@pereira.org",
            confidence=0.9,
        )
        person_whatsapp = ContactPoint.objects.create(
            actor=self.need.actor,
            kind=ContactKind.WHATSAPP,
            value="+573001112233",
            raw_value="300 111 2233",
            confidence=0.95,
        )
        email_draft = draft_outreach(self.match, org_email, body="x")
        whatsapp_draft = draft_outreach(self.match, person_whatsapp, body="x")
        self.assertLess(
            email_draft.contact_point.preference_rank(),
            whatsapp_draft.contact_point.preference_rank(),
        )
        # Both are links a human clicks; neither is dispatched by the system.
        self.assertTrue(email_draft.target_url.startswith("mailto:"))
        self.assertTrue(whatsapp_draft.target_url.startswith("https://wa.me/"))

    def test_non_messaging_contact_kind_rejected(self):
        nequi = ContactPoint.objects.create(
            actor=self.offer.actor,
            kind=ContactKind.PAYMENT,
            value="3001112233",
            raw_value="3001112233",
        )
        with self.assertRaises(ValueError):
            draft_outreach(self.match, nequi, body="x")
