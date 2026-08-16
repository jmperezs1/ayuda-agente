"""
Tests for the graph never being served behind.

These exist because of a live failure with no error in it. An event reported 803 requirements
through its summary and none through its graph, and neither endpoint was wrong by its own
logic: the summary counts rows, the graph served a cache that only ever rebuilt when no cache
existed at all. The rebuild had been delegated to a worker the deployment deliberately did not
run, so nothing rebuilt it and nothing recorded that it needed rebuilding.

The fix separates the two acts. Marking is a database write that cannot fail; rebuilding is
expensive and may be queued. Whoever reads next closes the loop.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from ayudagente.radar.choices import Direction
from ayudagente.radar.models import GraphSnapshot
from ayudagente.radar.services import refresh_graph
from ayudagente.radar.tests.factories import (
    DOSQUEBRADAS,
    PEREIRA,
    ApiTestCase,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class FreshnessBase(TestCase):
    def setUp(self):
        self.event = make_event()
        self.water = make_resource("water", "Agua")

    def _requirement(self, direction=Direction.NEEDS, point=PEREIRA):
        actor = make_actor(self.event, f"Actor {direction} {point.x}")
        return make_requirement(
            self.event,
            actor,
            self.water,
            make_location(point, f"sitio {actor.pk}"),
            direction=direction,
        )


class MarkingTests(FreshnessBase):
    """
    Marking is synchronous and cannot depend on a broker.

    Note:
        `captureOnCommitCallbacks` is required, not decoration. The signal defers to
        `transaction.on_commit`, and a `TestCase` wraps each test in a transaction that never
        commits — without it the callback never runs and the test passes for the wrong reason.
    """

    def _write_with_no_worker(self):
        """Create a requirement while the broker refuses, and let the commit hook run."""
        with (
            patch("ayudagente.radar.tasks.rebuild_graph.delay", side_effect=OSError("no redis")),
            self.captureOnCommitCallbacks(execute=True),
        ):
            return self._requirement()

    def test_a_write_marks_the_snapshot_behind_even_with_no_worker(self):
        refresh_graph(self.event.pk, force=True)

        self._write_with_no_worker()

        self.assertTrue(GraphSnapshot.objects.get(event=self.event).stale)

    def test_a_broker_that_is_down_does_not_raise(self):
        refresh_graph(self.event.pk, force=True)

        self._write_with_no_worker()  # must not raise

    def test_rebuilding_clears_the_mark(self):
        refresh_graph(self.event.pk, force=True)
        GraphSnapshot.objects.filter(event=self.event).update(stale=True)

        refresh_graph(self.event.pk, force=True)

        self.assertFalse(GraphSnapshot.objects.get(event=self.event).stale)

    def test_an_unchanged_graph_still_clears_a_mark_it_did_not_need(self):
        # The fingerprint says nothing changed, but a snapshot left marked is served forever
        refresh_graph(self.event.pk, force=True)
        GraphSnapshot.objects.filter(event=self.event).update(stale=True)

        _snapshot, rebuilt = refresh_graph(self.event.pk)

        self.assertFalse(rebuilt)
        self.assertFalse(GraphSnapshot.objects.get(event=self.event).stale)


class ReadTests(ApiTestCase):
    """The read path closes the loop it used to delegate."""

    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.water = make_resource("water", "Agua")

    def _requirement(self, direction=Direction.NEEDS, point=PEREIRA):
        actor = make_actor(self.event, f"Actor {direction} {point.x}")
        return make_requirement(
            self.event,
            actor,
            self.water,
            make_location(point, f"sitio {actor.pk}"),
            direction=direction,
        )

    def _graph(self) -> dict:
        return self.client.get(reverse("radar:event-graph", args=[self.event.pk])).json()

    def test_a_first_read_builds_what_never_existed(self):
        self._requirement()

        self.assertEqual(len(self._graph()["nodes"]), 1)

    def _write_with_no_worker(self, *requirements):
        """Write while the broker refuses, and let the commit hook run."""
        with (
            patch("ayudagente.radar.tasks.rebuild_graph.delay", side_effect=OSError("no redis")),
            self.captureOnCommitCallbacks(execute=True),
        ):
            for direction, point in requirements:
                self._requirement(direction, point)

    def test_a_read_after_a_write_with_no_worker_shows_the_write(self):
        # The exact failure: summary said 803 requirements, graph said none
        self._graph()  # a snapshot now exists, which is what used to freeze the answer
        self._write_with_no_worker((Direction.NEEDS, PEREIRA), (Direction.OFFERS, DOSQUEBRADAS))

        payload = self._graph()

        self.assertEqual(len(payload["nodes"]), 2)
        self.assertFalse(GraphSnapshot.objects.get(event=self.event).stale)

    def test_matching_runs_on_that_rebuild_so_edges_appear(self):
        self._graph()
        self._write_with_no_worker((Direction.NEEDS, PEREIRA), (Direction.OFFERS, DOSQUEBRADAS))

        self.assertEqual(len(self._graph()["edges"]), 1)

    def test_a_fresh_snapshot_is_served_without_rebuilding(self):
        self._requirement()
        self._graph()

        with patch("ayudagente.radar.views.events.refresh_graph") as rebuilt:
            self._graph()

        rebuilt.assert_not_called()
