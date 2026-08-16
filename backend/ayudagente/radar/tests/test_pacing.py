"""
Tests for what stops the loop when nobody is watching.

An emergency has no natural end, so every one of these is about a reason to wait. The case
that matters most is the deadlock: low novelty pauses rounds, and the measurement is taken
over recent jobs, so without an escape a quiet hour would keep the loop asleep for good.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from ayudagente.radar.choices import DecisionSource, EventStatus, JobStatus, Platform
from ayudagente.radar.models import Event, HarvestJob
from ayudagente.radar.services.pacing import (
    MAX_PENDING_JOBS,
    MIN_NOVELTY,
    PROBE_AFTER,
    recent_novelty,
    should_decide,
    trip_ceiling,
)
from ayudagente.radar.tests.factories import make_event


class PacingBase(TestCase):
    def setUp(self):
        self.event = make_event()

    def _job(self, *, returned=100, new=50, status=JobStatus.DONE, minutes_ago=1) -> HarvestJob:
        job = HarvestJob.objects.create(
            event=self.event,
            platform=Platform.X,
            apify_actor="apidojo/tweet-scraper",
            actor_input={},
            decided_by=DecisionSource.AGENT,
            rationale="a pass",
            status=status,
            items_returned=returned,
            items_new=new,
        )
        if status not in (JobStatus.PENDING, JobStatus.RUNNING):
            job.finished_at = timezone.now() - timedelta(minutes=minutes_ago)
            job.save(update_fields=["finished_at"])
        return job


class NoveltyTests(PacingBase):
    def test_nothing_harvested_is_not_the_same_as_nothing_new(self):
        self.assertIsNone(recent_novelty(self.event))

        verdict = should_decide(self.event)

        self.assertTrue(verdict.proceed)
        self.assertIn("nothing harvested", verdict.reason)

    def test_a_productive_window_keeps_the_loop_going(self):
        self._job(returned=100, new=60)

        verdict = should_decide(self.event)

        self.assertTrue(verdict.proceed)
        self.assertIn("new", verdict.reason)

    def test_a_window_of_duplicates_stops_it_deciding_more(self):
        self._job(returned=200, new=5)

        verdict = should_decide(self.event)

        self.assertFalse(verdict.proceed)
        self.assertIn("exhausted", verdict.reason)

    def test_novelty_is_measured_across_the_window_not_the_last_job(self):
        self._job(returned=100, new=0)
        self._job(returned=100, new=80)

        self.assertAlmostEqual(recent_novelty(self.event) or 0, 0.4)
        self.assertTrue(should_decide(self.event).proceed)

    def test_an_empty_harvest_counts_against_novelty(self):
        self._job(returned=0, new=0, status=JobStatus.EMPTY)
        self._job(returned=100, new=2)

        self.assertLess(recent_novelty(self.event) or 1, MIN_NOVELTY)

    def test_a_failed_job_is_not_evidence_either_way(self):
        self._job(returned=0, new=0, status=JobStatus.FAILED)

        self.assertIsNone(recent_novelty(self.event))


class DeadlockTests(PacingBase):
    """Low novelty is a wait, not an end. Without a probe the wait never lifts."""

    def test_a_quiet_stretch_earns_another_look(self):
        self._job(returned=200, new=1, minutes_ago=int(PROBE_AFTER.total_seconds() // 60) + 10)

        verdict = should_decide(self.event)

        self.assertTrue(verdict.proceed)
        self.assertIn("nothing harvested in a while", verdict.reason)

    def test_the_probe_does_not_fire_while_the_loop_is_busy(self):
        self._job(returned=200, new=1, minutes_ago=2)

        self.assertFalse(should_decide(self.event).proceed)


class BackpressureTests(PacingBase):
    def test_a_deep_queue_means_the_harvest_is_the_bottleneck(self):
        for _ in range(MAX_PENDING_JOBS):
            self._job(status=JobStatus.PENDING)

        verdict = should_decide(self.event)

        self.assertFalse(verdict.proceed)
        self.assertIn("bottleneck", verdict.reason)

    def test_a_shallow_queue_does_not_block_anything(self):
        self._job(status=JobStatus.PENDING)

        self.assertTrue(should_decide(self.event).proceed)


class KillSwitchTests(PacingBase):
    def test_a_paused_event_decides_nothing(self):
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])

        verdict = should_decide(self.event)

        self.assertFalse(verdict.proceed)
        self.assertIn("paused", verdict.reason)

    def test_the_ceiling_pauses_the_event_rather_than_only_refusing(self):
        self.event.spent_usd = Decimal("40")
        self.event.save(update_fields=["spent_usd"])

        self.assertTrue(trip_ceiling(self.event))
        self.assertEqual(Event.objects.get(pk=self.event.pk).status, EventStatus.PAUSED)

    def test_spending_under_the_ceiling_changes_nothing(self):
        self.event.spent_usd = Decimal("1.20")
        self.event.save(update_fields=["spent_usd"])

        self.assertFalse(trip_ceiling(self.event))
        self.assertTrue(should_decide(self.event).proceed)

    def test_a_ceiling_of_zero_disables_the_breaker(self):
        self.event.spent_usd = Decimal("9999")
        self.event.save(update_fields=["spent_usd"])

        with self.settings(HARVEST_SPEND_CEILING_USD=0):
            self.assertFalse(trip_ceiling(self.event))
            self.assertTrue(should_decide(self.event).proceed)

    def test_tripping_twice_is_a_no_op(self):
        self.event.spent_usd = Decimal("40")
        self.event.save(update_fields=["spent_usd"])
        trip_ceiling(self.event)

        self.assertFalse(trip_ceiling(self.event))
