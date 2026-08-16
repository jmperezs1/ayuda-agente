"""
Tests for the perpetual loop: one beat, one round, and the reasons a round does not happen.

The model is never called. What is worth asserting is the shape of the loop — that a round
starts a fresh conversation, that a skipped round says why, and that the beat only touches
events a human has left running.
"""

from unittest.mock import patch

from django.test import TestCase

from ayudagente.radar.choices import (
    DecisionSource,
    EventStatus,
    HarvestTarget,
    JobStatus,
    Platform,
    Zone,
)
from ayudagente.radar.models import AdminUnit, Event, FrontierNode, HarvestJob
from ayudagente.radar.services.pacing import MAX_PENDING_JOBS
from ayudagente.radar.tasks import run_round, run_tick
from ayudagente.radar.tests.factories import PEREIRA, make_event


class FakeGraph:
    """A compiled agent that queues jobs instead of thinking."""

    def __init__(self, queues: int = 0, event=None, node=None):
        self.queues = queues
        self.event = event
        self.node = node
        self.configs: list[dict] = []

    def invoke(self, _input, config=None):
        self.configs.append(config or {})
        for index in range(self.queues):
            HarvestJob.objects.create(
                event=self.event,
                node=self.node,
                platform=Platform.X,
                apify_actor="apidojo/tweet-scraper",
                actor_input={"searchQuery": f"q{index}"},
                decided_by=DecisionSource.AGENT,
                rationale="the agent said so",
                target_kind=HarvestTarget.SEARCH,
                status=JobStatus.PENDING,
            )
        return {"messages": []}


class LoopBase(TestCase):
    def setUp(self):
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

    def _round(self, graph: FakeGraph, **kwargs) -> dict:
        with patch("ayudagente.radar.tasks.build_agent", return_value=graph):
            return run_round(self.event.pk, **kwargs)


class FrontierRoundTests(LoopBase):
    def test_a_round_reports_what_the_agent_queued(self):
        graph = FakeGraph(queues=3, event=self.event, node=self.node)

        result = self._round(graph)

        self.assertTrue(result["ran"])
        self.assertEqual(result["queued"], 3)
        self.assertEqual(HarvestJob.objects.count(), 3)

    def test_every_round_starts_a_fresh_conversation(self):
        # One thread would carry every previous round's tool calls into the next prompt
        graph = FakeGraph(event=self.event)

        self._round(graph)
        self._round(graph)

        threads = [config["configurable"]["thread_id"] for config in graph.configs]
        self.assertEqual(len(set(threads)), 2)
        self.assertTrue(all(thread.startswith(f"frontier-{self.event.pk}-") for thread in threads))

    def test_a_paused_event_is_skipped_with_the_reason(self):
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])
        graph = FakeGraph(queues=2, event=self.event)

        result = self._round(graph)

        self.assertFalse(result["ran"])
        self.assertIn("paused", result["reason"])
        self.assertEqual(graph.configs, [])  # the model was never reached

    def test_a_deep_queue_skips_the_round(self):
        for index in range(MAX_PENDING_JOBS):
            HarvestJob.objects.create(
                event=self.event,
                platform=Platform.X,
                apify_actor="a",
                actor_input={},
                decided_by=DecisionSource.AGENT,
                rationale=f"waiting {index}",
                status=JobStatus.PENDING,
            )

        result = self._round(FakeGraph(event=self.event))

        self.assertFalse(result["ran"])
        self.assertIn("bottleneck", result["reason"])

    def test_a_human_can_force_a_round_past_the_pacing_rules(self):
        self.event.status = EventStatus.PAUSED
        self.event.save(update_fields=["status"])
        graph = FakeGraph(event=self.event)

        result = self._round(graph, force=True)

        self.assertTrue(result["ran"])
        self.assertEqual(len(graph.configs), 1)

    def test_an_unconfigured_model_is_reported_not_raised(self):
        from agent_tools.agents import LLMNotConfigured

        with patch("ayudagente.radar.tasks.build_agent", side_effect=LLMNotConfigured("no key")):
            result = run_round(self.event.pk)

        self.assertFalse(result["ran"])
        self.assertIn("no key", result["reason"])


class TickTests(LoopBase):
    def test_the_beat_covers_every_active_event(self):
        second = make_event(name="Otro sismo")
        graph = FakeGraph(event=self.event)

        with patch("ayudagente.radar.tasks.build_agent", return_value=graph):
            result = run_tick()

        self.assertEqual(set(result["events"]), {self.event.pk, second.pk})

    def test_an_archived_event_is_left_alone(self):
        make_event(name="Terminado", status=EventStatus.ARCHIVED)
        graph = FakeGraph(event=self.event)

        with patch("ayudagente.radar.tasks.build_agent", return_value=graph):
            result = run_tick()

        self.assertEqual(set(result["events"]), {self.event.pk})

    def test_the_beat_dispatches_pending_work_before_deciding_more(self):
        HarvestJob.objects.create(
            event=self.event,
            node=self.node,
            platform=Platform.X,
            apify_actor="a",
            actor_input={},
            decided_by=DecisionSource.AGENT,
            rationale="queued earlier",
            status=JobStatus.PENDING,
        )

        with (
            patch("ayudagente.radar.tasks.build_agent", return_value=FakeGraph(event=self.event)),
            patch("ayudagente.radar.tasks.harvest.delay") as dispatched,
        ):
            run_tick()

        self.assertEqual(dispatched.call_count, 1)

    def test_a_tick_with_no_events_is_not_an_error(self):
        Event.objects.all().delete()

        self.assertEqual(run_tick(), {"events": {}})
