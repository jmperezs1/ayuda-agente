"""
Tests for what a requirement is allowed to do before anything has confirmed it.

The rule these protect is asymmetric on purpose. A false need wastes a delivery; a false offer
makes a real need look covered so it stops being proposed, and nobody notices. The silent
failure is the expensive one, so offers carry the higher bar.

The other rule worth stating: nothing here waits for a person. Every route out of quarantine
is one the world can walk on its own.
"""

from decimal import Decimal

from django.test import TestCase

from ayudagente.radar.choices import ActorKind, Direction, LocationPrecision, RequirementStatus
from ayudagente.radar.models import Requirement
from ayudagente.radar.services import verification
from ayudagente.radar.services.requirements import routable
from ayudagente.radar.tests.factories import (
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_observation,
    make_requirement,
    make_resource,
)


class VerificationBase(TestCase):
    def setUp(self):
        self.event = make_event()
        self.water = make_resource("water", "Agua")

    def _requirement(self, *, direction=Direction.NEEDS, precision=None, **actor_kwargs):
        actor = make_actor(self.event, "Alguien", **actor_kwargs)
        location = make_location(
            PEREIRA,
            f"sitio {actor.pk}",
            precision=precision or LocationPrecision.NEIGHBORHOOD,
        )
        return make_requirement(self.event, actor, self.water, location, direction=direction)


class CorroborationTests(VerificationBase):
    """The route out that needs nobody: the world saying it twice."""

    def test_a_single_uncorroborated_post_is_quarantined(self):
        requirement = self._requirement()

        verification.apply(requirement)

        requirement.refresh_from_db()
        self.assertEqual(requirement.status, RequirementStatus.UNVERIFIED)

    def test_a_second_independent_post_releases_it(self):
        requirement = self._requirement()
        verification.apply(requirement)

        for index in range(2):
            requirement.evidence.add(make_observation(self.event, f"post {index}"))
        verification.apply(requirement)

        requirement.refresh_from_db()
        self.assertEqual(requirement.status, RequirementStatus.OPEN)

    def test_the_reason_is_reported_either_way(self):
        requirement = self._requirement()

        cleared, reason = verification.verdict(requirement)

        self.assertFalse(cleared)
        self.assertIn("single post", reason)


class TrustTests(VerificationBase):
    """Routes out that depend on who is speaking."""

    def test_a_platform_verified_account_stands_alone(self):
        requirement = self._requirement(verified=True)

        verification.apply(requirement)

        requirement.refresh_from_db()
        self.assertEqual(requirement.status, RequirementStatus.OPEN)

    def test_an_offer_carries_a_higher_bar_than_a_need(self):
        # A false offer makes a real need look covered, which fails silently
        need = self._requirement(direction=Direction.NEEDS, credibility=0.65)
        offer = self._requirement(direction=Direction.OFFERS, credibility=0.65)

        self.assertTrue(verification.verdict(need)[0])
        self.assertFalse(verification.verdict(offer)[0])

    def test_an_organisation_reporting_a_need_is_believed(self):
        requirement = self._requirement(kind=ActorKind.PUBLIC_ENTITY)

        self.assertTrue(verification.verdict(requirement)[0])

    def test_an_organisation_offering_is_not_believed_on_that_alone(self):
        requirement = self._requirement(
            direction=Direction.OFFERS, kind=ActorKind.COLLECTION_CENTER
        )

        self.assertFalse(verification.verdict(requirement)[0])


class RefusalTests(VerificationBase):
    """What no amount of credibility can override."""

    def test_a_place_too_coarse_to_act_on_stays_in_quarantine(self):
        requirement = self._requirement(verified=True, precision=LocationPrecision.COUNTRY)

        cleared, reason = verification.verdict(requirement)

        self.assertFalse(cleared)
        self.assertIn("country", reason)

    def test_a_photo_contradicting_the_text_quarantines_a_verified_account(self):
        requirement = self._requirement(verified=True)
        observation = make_observation(self.event, "hay un derrumbe")
        requirement.evidence.add(observation)
        _extraction(observation, conflict=True)

        cleared, reason = verification.verdict(requirement)

        self.assertFalse(cleared)
        self.assertIn("photo", reason)


class ConsequenceTests(VerificationBase):
    """What the label does, and what it deliberately does not."""

    def test_a_weakly_backed_requirement_is_still_matched(self):
        # It marks, it does not block: nothing is sent without a human clicking
        requirement = self._requirement()
        verification.apply(requirement)

        routed = routable(Requirement.objects.filter(event=self.event))

        self.assertIn(requirement, routed)

    def test_the_label_is_what_tells_a_coordinator_how_well_backed_it_is(self):
        requirement = self._requirement()
        verification.apply(requirement)

        requirement.refresh_from_db()
        self.assertEqual(requirement.status, RequirementStatus.UNVERIFIED)

    def test_a_decision_somebody_already_made_is_not_reopened(self):
        requirement = self._requirement(verified=True)
        requirement.status = RequirementStatus.COVERED
        requirement.covered_quantity = Decimal(10)
        requirement.save()

        self.assertFalse(verification.apply(requirement))
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, RequirementStatus.COVERED)

    def test_applying_twice_reports_no_second_change(self):
        requirement = self._requirement()

        self.assertTrue(verification.apply(requirement))
        self.assertFalse(verification.apply(requirement))


def _extraction(observation, conflict: bool):
    """Attach a reading to a post, which is where the image conflict flag lives."""
    from ayudagente.radar.models import Extraction

    return Extraction.objects.create(
        observation=observation,
        model="test",
        prompt_version="v6",
        classification="need",
        confidence=0.9,
        payload={},
        text_image_conflict=conflict,
    )
