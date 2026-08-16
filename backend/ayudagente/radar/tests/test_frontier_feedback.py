"""
Tests for the half of the frontier that closes the loop: the counters and the duplicate guard.

Without these the agent reads a board that never changes. It would run at midnight, queue five
targets, run again at half past against identical rows, and queue the same five — all night,
learning nothing. Every assertion here is about the board actually moving.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from ayudagente.radar.choices import (
    DecisionSource,
    HarvestTarget,
    JobStatus,
    NodeStatus,
    Platform,
    Zone,
)
from ayudagente.radar.models import AdminUnit, FrontierNode, HarvestJob, Observation
from ayudagente.radar.services.apify_inputs import APIFY_ACTOR_BY_PLATFORM
from ayudagente.radar.services.frontier import (
    COOLDOWN,
    create_harvest_job,
    record_actionable_find,
    record_harvest,
)
from ayudagente.radar.tests.factories import (
    CALI,
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class FrontierBase(TestCase):
    def setUp(self):
        self.event = make_event()
        self.event.lexicon = {"hashtags": ["#SismoChocó"], "negatives": ["Perú"]}
        self.event.save(update_fields=["lexicon"])
        self.unit = AdminUnit.objects.create(
            country_code="CO",
            code="27001",
            name="Quibdó",
            name_norm="quibdó",
            level="admin_2",
            centroid=PEREIRA,
        )
        self.node = FrontierNode.objects.create(
            event=self.event, admin_unit=self.unit, platform=Platform.X, zone=Zone.IMPACT
        )

    def _job(self, **kwargs) -> HarvestJob:
        defaults = {
            "event": self.event,
            "node": self.node,
            "platform": Platform.X,
            "apify_actor": "apidojo/tweet-scraper",
            "actor_input": {},
            "decided_by": DecisionSource.AGENT,
            "rationale": "why not",
            "status": JobStatus.DONE,
            "actual_cost_usd": Decimal("0.05"),
        }
        defaults.update(kwargs)
        return HarvestJob.objects.create(**defaults)

    def _observation(self, job: HarvestJob, platform_id: str = "1") -> Observation:
        return Observation.objects.create(
            event=self.event,
            job=job,
            platform=Platform.X,
            platform_id=platform_id,
            permalink=f"https://x.com/u/status/{platform_id}",
            posted_at=timezone.now(),
            raw={},
        )


class RecordHarvestTests(FrontierBase):
    def test_a_finished_run_moves_the_counters_the_agent_reads(self):
        job = self._job()

        record_harvest(job, items_new=40)

        self.node.refresh_from_db()
        self.assertEqual(self.node.passes, 1)
        self.assertEqual(self.node.total_items, 40)
        self.assertIsNotNone(self.node.last_harvest_at)
        self.assertEqual(float(self.node.observed_cost_usd), 0.05)

    def test_the_yield_denominator_is_new_posts_not_returned_ones(self):
        # Re-harvesting returns the same posts; counting them again would sink a good target
        record_harvest(self._job(), items_new=50)
        record_harvest(self._job(), items_new=0)  # a second pass, nothing new

        self.node.refresh_from_db()
        self.assertEqual(self.node.total_items, 50)
        self.assertEqual(self.node.passes, 2)

    def test_a_broken_scraper_leaves_the_quality_record_untouched(self):
        record_harvest(self._job(), items_new=0, counts_as_evidence=False)

        self.node.refresh_from_db()
        self.assertEqual(self.node.passes, 0)
        self.assertEqual(self.node.total_items, 0)
        self.assertIsNotNone(self.node.last_harvest_at)  # still enough to not retry at once

    def test_a_manual_job_with_no_node_is_ignored(self):
        record_harvest(self._job(node=None), items_new=10)  # must not raise


class RecordActionableFindTests(FrontierBase):
    """Credit follows where the requirement landed, not which job fetched the post."""

    def setUp(self):
        super().setUp()
        self.water = make_resource("water", "Agua")
        self.actor = make_actor(self.event, "Barrio")

    def _requirements(self, count: int, point=PEREIRA, admin_unit=None) -> list:
        location = make_location(point, f"sitio {count} {point.x}", admin_unit=admin_unit)
        return [
            make_requirement(self.event, self.actor, self.water, location) for _ in range(count)
        ]

    def test_requirements_credit_the_place_they_landed_in(self):
        job = self._job()
        record_harvest(job, items_new=100)

        record_actionable_find(self._observation(job), self._requirements(3))

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 3)
        self.assertEqual(self.node.yield_rate, 3.0)  # 3 per 100 harvested
        self.assertIsNotNone(self.node.last_useful_find_at)

    def test_a_retired_place_that_produces_again_comes_back(self):
        # An emergency moves: a municipality nobody posted about at midnight is dawn's story
        self.node.status = NodeStatus.EXHAUSTED
        self.node.save(update_fields=["status"])
        job = self._job()
        record_harvest(job, items_new=100)

        record_actionable_find(self._observation(job), self._requirements(1))

        self.node.refresh_from_db()
        self.assertEqual(self.node.status, NodeStatus.ACTIVE)

    def test_an_exact_administrative_match_wins(self):
        job = self._job()

        record_actionable_find(self._observation(job), self._requirements(2, admin_unit=self.unit))

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 2)

    def test_a_sweep_with_no_node_still_credits_the_place(self):
        # The broadest and most valuable pass carries no node; crediting the job loses it all
        sweep = self._job(node=None)

        record_actionable_find(self._observation(sweep), self._requirements(4))

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 4)

    def test_finds_accumulate_across_posts(self):
        job = self._job()
        record_harvest(job, items_new=100)

        record_actionable_find(self._observation(job, "1"), self._requirements(2))
        record_actionable_find(self._observation(job, "2"), self._requirements(4))

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 6)

    def test_a_requirement_far_from_every_watched_place_credits_nobody(self):
        job = self._job()

        record_actionable_find(self._observation(job), self._requirements(3, point=CALI))

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 0)

    def test_a_post_that_produced_nothing_changes_nothing(self):
        job = self._job()

        record_actionable_find(self._observation(job), [])

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 0)
        self.assertIsNone(self.node.last_useful_find_at)

    def test_a_seeded_post_with_no_job_is_ignored(self):
        observation = self._observation(self._job())
        observation.job = None
        observation.save(update_fields=["job"])

        record_actionable_find(observation, self._requirements(5))  # must not raise

        self.node.refresh_from_db()
        self.assertEqual(self.node.actionable_items, 0)


class DuplicateGuardTests(FrontierBase):
    def test_a_target_already_being_harvested_is_refused(self):
        for status in (JobStatus.PENDING, JobStatus.RUNNING):
            with self.subTest(status=status):
                HarvestJob.objects.all().delete()
                self._job(status=status)

                with self.assertRaises(ValueError) as caught:
                    create_harvest_job(self.event.id, self.node.id, "otra pasada")

                self.assertIn(str(status), str(caught.exception))

    def test_a_different_kind_of_pass_on_the_same_target_is_allowed(self):
        self._job(status=JobStatus.PENDING, target_kind=HarvestTarget.SEARCH)

        job = create_harvest_job(
            self.event.id,
            self.node.id,
            "ya rindió, vale la pena el hilo",
            target_kind=HarvestTarget.COMMENTS,
        )

        self.assertEqual(job.target_kind, HarvestTarget.COMMENTS)

    def test_a_target_harvested_moments_ago_is_refused_with_when_it_frees_up(self):
        self.node.last_harvest_at = timezone.now() - timedelta(minutes=2)
        self.node.save(update_fields=["last_harvest_at"])

        with self.assertRaises(ValueError) as caught:
            create_harvest_job(self.event.id, self.node.id, "otra vez")

        self.assertIn("minutes", str(caught.exception))

    def test_the_cooldown_expires(self):
        self.node.last_harvest_at = timezone.now() - COOLDOWN - timedelta(minutes=1)
        self.node.save(update_fields=["last_harvest_at"])

        job = create_harvest_job(self.event.id, self.node.id, "ya se enfrió")

        self.assertEqual(job.status, JobStatus.PENDING)

    def test_a_finished_job_does_not_block_the_next_round(self):
        self._job(status=JobStatus.DONE)

        job = create_harvest_job(self.event.id, self.node.id, "la anterior ya terminó")

        self.assertEqual(job.status, JobStatus.PENDING)

    def test_the_query_still_carries_the_toponym(self):
        job = create_harvest_job(self.event.id, self.node.id, "primera pasada")

        query = job.actor_input["searchTerms"][0]
        self.assertIn("Quibdó", query)
        self.assertIn('-"Perú"', query)

    def test_the_payload_is_the_one_this_platform_accepts(self):
        # The first live run shipped an invented field; three Actors ignored it in silence
        job = create_harvest_job(self.event.id, self.node.id, "primera pasada")

        self.assertEqual(job.apify_actor, APIFY_ACTOR_BY_PLATFORM[Platform.X])
        self.assertIn("maxItems", job.actor_input)
        self.assertNotIn("searchQuery", job.actor_input)
