"""
Tests for the endpoints that say whether the machine is still working.

The one that matters is `next_round.reason`. A loop deliberately waiting and a loop that
crashed look identical from outside — both are quiet — and a dashboard that cannot tell them
apart is worse than no dashboard, because it makes silence look like health.
"""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from ayudagente.radar.choices import (
    DecisionSource,
    EventStatus,
    HarvestTarget,
    JobStatus,
    Platform,
    Zone,
)
from ayudagente.radar.models import AdminUnit, FrontierNode, HarvestJob
from ayudagente.radar.services.pacing import MAX_PENDING_JOBS
from ayudagente.radar.tests.factories import PEREIRA, ApiTestCase, make_event


class OperationsBase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
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
            "target_kind": HarvestTarget.SEARCH,
            "apify_actor": "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest",
            "actor_input": {"searchTerms": ['("Quibdó") ("necesitamos")'], "maxItems": 200},
            "decided_by": DecisionSource.AGENT,
            "rationale": "Quibdó sin explorar, primera pasada",
            "status": JobStatus.DONE,
            "items_returned": 200,
            "items_new": 180,
            "actual_cost_usd": Decimal("0.05"),
        }
        defaults.update(kwargs)
        job = HarvestJob.objects.create(**defaults)
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            job.finished_at = timezone.now() - timedelta(minutes=1)
            job.save(update_fields=["finished_at"])
        return job


class JobListTests(OperationsBase):
    def test_a_job_carries_the_reasoning_that_produced_it(self):
        self._job()

        row = self.client.get(reverse("radar:job-list", args=[self.event.pk])).json()["results"][0]

        self.assertEqual(row["rationale"], "Quibdó sin explorar, primera pasada")
        self.assertEqual(row["target"], str(self.unit))
        self.assertEqual(row["decided_by"], DecisionSource.AGENT)

    def test_the_payload_sent_to_apify_is_visible(self):
        # Three Actors once ignored an invented field and nothing could show what was asked
        self._job()

        row = self.client.get(reverse("radar:job-list", args=[self.event.pk])).json()["results"][0]

        self.assertIn("searchTerms", row["actor_input"])
        self.assertEqual(row["items_returned"], 200)
        self.assertEqual(row["cost_usd"], 0.05)

    def test_a_sweep_with_no_node_reports_no_target_rather_than_failing(self):
        self._job(node=None, decided_by=DecisionSource.RULE)

        row = self.client.get(reverse("radar:job-list", args=[self.event.pk])).json()["results"][0]

        self.assertIsNone(row["target"])

    def test_status_and_platform_narrow_the_list(self):
        self._job(status=JobStatus.FAILED, error="apify is down")
        self._job(platform=Platform.TIKTOK, node=None)
        url = reverse("radar:job-list", args=[self.event.pk])

        self.assertEqual(self.client.get(url).json()["count"], 2)
        self.assertEqual(self.client.get(url, {"status": "failed"}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"platform": "tiktok"}).json()["count"], 1)

    def test_a_failure_reports_what_went_wrong(self):
        self._job(status=JobStatus.FAILED, error="Input is not valid: hashtags.1")

        row = self.client.get(
            reverse("radar:job-list", args=[self.event.pk]), {"status": "failed"}
        ).json()["results"][0]

        self.assertIn("hashtags", row["error"])

    def test_an_unknown_status_is_a_400(self):
        response = self.client.get(
            reverse("radar:job-list", args=[self.event.pk]), {"status": "nonsense"}
        )

        self.assertEqual(response.status_code, 400)


class LoopStatusTests(OperationsBase):
    def _status(self) -> dict:
        return self.client.get(reverse("radar:loop-status", args=[self.event.pk])).json()

    def test_a_healthy_loop_says_it_will_run_and_why(self):
        self._job(items_returned=100, items_new=80)

        payload = self._status()

        self.assertTrue(payload["next_round"]["will_run"])
        self.assertIn("new", payload["next_round"]["reason"])
        self.assertAlmostEqual(payload["novelty"], 0.8)

    def test_a_loop_waiting_on_purpose_is_told_apart_from_a_broken_one(self):
        self._job(items_returned=200, items_new=2)

        payload = self._status()

        self.assertFalse(payload["next_round"]["will_run"])
        self.assertIn("exhausted", payload["next_round"]["reason"])

    def test_a_paused_event_says_so(self):
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])

        payload = self._status()

        self.assertFalse(payload["harvestable"])
        self.assertIn("paused", payload["next_round"]["reason"])

    def test_the_job_counts_show_where_the_work_is_stuck(self):
        for _ in range(MAX_PENDING_JOBS):
            self._job(status=JobStatus.PENDING)

        payload = self._status()

        self.assertEqual(payload["jobs"]["pending"], MAX_PENDING_JOBS)
        self.assertIn("bottleneck", payload["next_round"]["reason"])

    def test_an_untouched_event_reports_no_novelty_rather_than_zero(self):
        payload = self._status()

        self.assertIsNone(payload["novelty"])
        self.assertIsNone(payload["last_harvest_at"])
        self.assertTrue(payload["next_round"]["will_run"])

    def test_spend_is_reported_so_the_breaker_is_visible(self):
        self.event.spent_usd = Decimal("3.40")
        self.event.save(update_fields=["spent_usd"])

        self.assertEqual(self._status()["spent_usd"], 3.40)
