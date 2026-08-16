"""
Tests for executing a harvest job and feeding the result back to the frontier.

Apify is never called: a fake client returns whatever the test needs. What is worth asserting
is everything around the call — that a run is never billed twice, that a broken scraper is not
mistaken for a quiet place, and that the scoreboard the agent reads actually moves. That last
one is the whole point: without it a perpetual agent reads identical rows forever.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from ayudagente.radar.choices import DecisionSource, EventStatus, JobStatus, Platform, Zone
from ayudagente.radar.models import (
    AdminUnit,
    Event,
    FrontierNode,
    HarvestJob,
    Media,
    Observation,
)
from ayudagente.radar.services.harvest import (
    ACTOR_DOWN_STREAK,
    HarvestNotConfigured,
    build_client,
    run_harvest_job,
)
from ayudagente.radar.tests.factories import PEREIRA, make_event


@dataclass
class FakeRun:
    """What `ActorClient.call` returns, reduced to the three fields the service reads."""

    id: str = "run-1"
    default_dataset_id: str = "dataset-1"
    usage_total_usd: float | None = 0.42


@dataclass
class FakeClient:
    """An Apify client that answers from a list instead of the network."""

    items: list[dict] = field(default_factory=list)
    run: FakeRun | None = field(default_factory=FakeRun)
    raises: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    def actor(self, actor_id: str):
        self.calls.append({"actor": actor_id})
        return self

    def call(self, **kwargs):
        self.calls[-1].update(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.run

    def dataset(self, dataset_id: str):
        return self

    def iterate_items(self):
        return iter(self.items)


def tweet(platform_id: str, text: str = "necesitamos agua en Quibdó") -> dict:
    """One item shaped the way the X scraper returns them."""
    return {
        "id": platform_id,
        "url": f"https://x.com/vecino/status/{platform_id}",
        "createdAt": "2026-08-10T14:00:00.000Z",
        "text": text,
        "author": {"userName": "vecino", "name": "Vecino", "followers": 120},
    }


class HarvestBase(TestCase):
    def setUp(self):
        # Before the fixture tweets, or every one of them counts as predating the emergency
        self.event = make_event(occurred_at=datetime(2026, 8, 10, tzinfo=UTC))
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
            "actor_input": {"searchQuery": '"Quibdó"'},
            "decided_by": DecisionSource.AGENT,
            "rationale": "unexplored, worth a first pass",
            "status": JobStatus.PENDING,
        }
        defaults.update(kwargs)
        return HarvestJob.objects.create(**defaults)


class RunHarvestJobTests(HarvestBase):
    def test_items_become_observations_and_the_job_records_the_run(self):
        job = self._job()
        client = FakeClient(items=[tweet("1"), tweet("2")])

        result = run_harvest_job(job.pk, client=client)

        job.refresh_from_db()
        self.assertEqual(result.items_new, 2)
        self.assertEqual(Observation.objects.filter(event=self.event).count(), 2)
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertEqual(job.run_id, "run-1")
        self.assertEqual(job.dataset_id, "dataset-1")
        self.assertEqual(job.items_returned, 2)
        self.assertEqual(job.actual_cost_usd, Decimal("0.42"))
        self.assertIsNotNone(job.finished_at)

    def test_the_actor_input_is_sent_exactly_as_the_agent_wrote_it(self):
        job = self._job(actor_input={"searchQuery": '"Quibdó" -"Perú"'})
        client = FakeClient(items=[tweet("1")])

        run_harvest_job(job.pk, client=client)

        self.assertEqual(client.calls[0]["actor"], "apidojo/tweet-scraper")
        self.assertEqual(client.calls[0]["run_input"], {"searchQuery": '"Quibdó" -"Perú"'})

    def test_a_post_already_held_is_reused_not_duplicated(self):
        run_harvest_job(self._job().pk, client=FakeClient(items=[tweet("1"), tweet("2")]))

        second = run_harvest_job(self._job().pk, client=FakeClient(items=[tweet("2"), tweet("3")]))

        self.assertEqual(second.items_returned, 2)
        self.assertEqual(second.items_new, 1)  # only the post we did not have
        self.assertEqual(Observation.objects.count(), 3)

    def test_an_item_with_no_id_or_timestamp_is_skipped_rather_than_stored(self):
        client = FakeClient(items=[tweet("1"), {"text": "sin id ni fecha"}])

        result = run_harvest_job(self._job().pk, client=client)

        self.assertEqual(result.items_new, 1)
        self.assertEqual(result.skipped, 1)

    def test_a_job_that_is_not_pending_is_refused(self):
        job = self._job(status=JobStatus.RUNNING)

        with self.assertRaises(ValueError):
            run_harvest_job(job.pk, client=FakeClient(items=[tweet("1")]))

    def test_a_failure_is_recorded_on_the_job_and_re_raised(self):
        job = self._job()

        with self.assertRaises(RuntimeError):
            run_harvest_job(job.pk, client=FakeClient(raises=RuntimeError("apify is down")))

        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("apify is down", job.error)
        self.assertIsNotNone(job.finished_at)

    def test_a_run_that_never_finishes_is_a_failure_not_an_empty_result(self):
        job = self._job()

        with self.assertRaises(RuntimeError):
            run_harvest_job(job.pk, client=FakeClient(run=None))

        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.FAILED)

    def test_a_missing_token_names_itself(self):
        with self.settings(APIFY_TOKEN=""), self.assertRaises(HarvestNotConfigured):
            build_client()

    def test_media_travels_with_the_post(self):
        item = tweet("1")
        item["extendedEntities"] = {
            "media": [{"type": "photo", "media_url_https": "https://pbs.twimg.com/a.jpg"}]
        }

        run_harvest_job(self._job().pk, client=FakeClient(items=[item]))

        media = Media.objects.get()
        self.assertEqual(media.source_url, "https://pbs.twimg.com/a.jpg")
        self.assertEqual(media.observation.platform_id, "1")


class ResilienceTests(HarvestBase):
    """
    One bad row must never cost the batch, and a job that dies must say it died.

    Note:
        Both come from the same live failure. A Facebook comment carried an identifier longer
        than its column; the write raised, the batch was lost, and the job stayed `running`
        with no error. A running job is in flight, so nothing ever retried it and that target
        went quiet permanently — the worst shape a bug can take here, because it looks like
        the platform having nothing to say.
    """

    def test_an_item_the_database_rejects_does_not_cost_the_batch(self):
        job = self._job()
        overlong = tweet("2")
        overlong["id"] = "x" * 300  # longer than platform_id can hold

        result = run_harvest_job(
            job.pk, client=FakeClient(items=[tweet("1"), overlong, tweet("3")])
        )

        self.assertEqual(result.items_new, 2)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(Observation.objects.filter(event=self.event).count(), 2)

    def test_that_job_still_finishes_rather_than_hanging_in_running(self):
        job = self._job()
        overlong = tweet("2")
        overlong["id"] = "x" * 300

        run_harvest_job(job.pk, client=FakeClient(items=[overlong]))

        job.refresh_from_db()
        self.assertNotEqual(job.status, JobStatus.RUNNING)
        self.assertIsNotNone(job.finished_at)

    def test_a_failure_while_storing_is_recorded_on_the_job(self):
        job = self._job()

        with (
            patch(
                "ayudagente.radar.services.harvest.persist_items",
                side_effect=RuntimeError("disk on fire"),
            ),
            self.assertRaises(RuntimeError),
        ):
            run_harvest_job(job.pk, client=FakeClient(items=[tweet("1")]))

        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("disk on fire", job.error)


class StalenessTests(HarvestBase):
    """Only X honours a date filter, so the others hand back whatever the platform has."""

    def test_a_post_from_before_the_emergency_is_dropped(self):
        old = tweet("1")
        old["createdAt"] = "2024-03-02T10:00:00.000Z"

        result = run_harvest_job(self._job().pk, client=FakeClient(items=[old, tweet("2")]))

        self.assertEqual(result.items_new, 1)
        self.assertEqual(result.stale, 1)
        self.assertEqual(Observation.objects.count(), 1)

    def test_a_post_from_just_before_it_survives_the_grace(self):
        early = tweet("1")
        early["createdAt"] = "2026-08-09T20:00:00.000Z"

        result = run_harvest_job(self._job().pk, client=FakeClient(items=[early]))

        self.assertEqual(result.items_new, 1)
        self.assertEqual(result.stale, 0)

    def test_an_item_with_no_timestamp_is_skipped_rather_than_called_stale(self):
        undated = tweet("1")
        undated.pop("createdAt")

        result = run_harvest_job(self._job().pk, client=FakeClient(items=[undated]))

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.stale, 0)


class ActorDownTests(HarvestBase):
    """A scraper returning success with zero results is not the same as a quiet place."""

    def _finished_empty(self, count: int) -> None:
        for index in range(count):
            HarvestJob.objects.create(
                event=self.event,
                platform=Platform.X,
                apify_actor="apidojo/tweet-scraper",
                actor_input={},
                decided_by=DecisionSource.AGENT,
                rationale="previous pass",
                status=JobStatus.EMPTY,
                items_returned=0,
                finished_at=timezone.now() - timedelta(minutes=index + 1),
            )

    def test_a_first_empty_result_means_the_place_is_quiet(self):
        result = run_harvest_job(self._job().pk, client=FakeClient(items=[]))

        self.assertEqual(self._latest().status, JobStatus.EMPTY)
        self.assertEqual(result.items_new, 0)

    def test_a_streak_of_empty_runs_blames_the_scraper(self):
        self._finished_empty(ACTOR_DOWN_STREAK)

        run_harvest_job(self._job().pk, client=FakeClient(items=[]))

        self.assertEqual(self._latest().status, JobStatus.ACTOR_DOWN)

    def test_a_broken_scraper_does_not_count_against_the_place(self):
        self._finished_empty(ACTOR_DOWN_STREAK)

        run_harvest_job(self._job().pk, client=FakeClient(items=[]))

        self.node.refresh_from_db()
        self.assertEqual(self.node.passes, 0)  # the pass told us nothing about this place
        self.assertIsNotNone(self.node.last_harvest_at)

    def _latest(self) -> HarvestJob:
        """The job the test just ran, which is always the newest row."""
        return HarvestJob.objects.order_by("-id")[0]


@override_settings(HARVEST_SPEND_CEILING_USD=0, HARVEST_SPEND_TOTAL_CEILING_USD=0)
class SpendGateTests(HarvestBase):
    """
    What the last gate before Apify refuses, and what refusing leaves behind.

    Note:
        The status and the per-event ceiling were only ever consulted where jobs are created,
        so a job queued while an event was active still billed after it was paused. Every
        case here queues the job first and changes the world second, because that ordering is
        the bug and asserting on it is the point.
    """

    def test_a_paused_event_does_not_spend_on_a_job_queued_while_it_was_active(self):
        job = self._job()
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])
        client = FakeClient(items=[tweet("1")])

        result = run_harvest_job(job.pk, client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(result.items_returned, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.PENDING)  # untimely, not failed

    @override_settings(HARVEST_SPEND_TOTAL_CEILING_USD=5)
    def test_the_global_ceiling_stops_a_job_the_per_event_one_would_allow(self):
        Event.objects.filter(pk=self.event.pk).update(spent_usd=Decimal("3.00"))
        make_event(name="another emergency", spent_usd=Decimal("2.50"))
        job = self._job()
        client = FakeClient(items=[tweet("1")])

        run_harvest_job(job.pk, client=client)

        self.assertEqual(client.calls, [])
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.PENDING)

    @override_settings(HARVEST_SPEND_CEILING_USD=5)
    def test_the_per_event_ceiling_stops_the_job(self):
        Event.objects.filter(pk=self.event.pk).update(spent_usd=Decimal("5.00"))
        job = self._job()
        client = FakeClient(items=[tweet("1")])

        run_harvest_job(job.pk, client=client)

        self.assertEqual(client.calls, [])
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.PENDING)

    def test_raising_the_global_ceiling_resumes_the_job_that_was_refused(self):
        Event.objects.filter(pk=self.event.pk).update(spent_usd=Decimal("6.00"))
        job = self._job()

        with override_settings(HARVEST_SPEND_TOTAL_CEILING_USD=5):
            run_harvest_job(job.pk, client=FakeClient(items=[tweet("1")]))
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.PENDING)

        with override_settings(HARVEST_SPEND_TOTAL_CEILING_USD=50):
            result = run_harvest_job(job.pk, client=FakeClient(items=[tweet("1")]))

        self.assertEqual(result.items_new, 1)  # the same job, no requeue needed
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.DONE)
