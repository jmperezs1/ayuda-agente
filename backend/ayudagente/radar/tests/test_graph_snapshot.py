"""The persisted graph: rebuilt when inputs change, served as-is when they did not."""

from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from ayudagente.radar.choices import Direction, MatchStatus, RequirementStatus
from ayudagente.radar.models import Match
from ayudagente.radar.services.graph import refresh_graph
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


class GraphSnapshotTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.agua = make_resource("agua")
        make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            self.agua,
            make_location(PEREIRA, "Barrio"),
            quantity=Decimal(100),
        )
        make_requirement(
            self.event,
            make_actor(self.event, "Vecino"),
            self.agua,
            make_location(DOSQUEBRADAS, "Vecino"),
            direction=Direction.OFFERS,
            quantity=Decimal(100),
        )

    def test_refresh_builds_once_and_skips_when_nothing_changed(self):
        snapshot, rebuilt = refresh_graph(self.event.id)
        self.assertTrue(rebuilt)
        self.assertEqual(len(snapshot.payload["edges"]), 1)

        again, rebuilt_again = refresh_graph(self.event.id)
        self.assertFalse(rebuilt_again)  # identical inputs: hash comparison, no pass
        self.assertEqual(again.id, snapshot.id)
        self.assertEqual(again.input_fingerprint, snapshot.input_fingerprint)
        self.assertEqual(again.payload, snapshot.payload)

    def test_new_requirement_changes_fingerprint_and_rebuilds(self):
        first, _rebuilt = refresh_graph(self.event.id)

        make_requirement(
            self.event,
            make_actor(self.event, "Otro barrio"),
            self.agua,
            make_location(PEREIRA, "Otro barrio"),
            quantity=Decimal(50),
        )
        second, rebuilt = refresh_graph(self.event.id)

        self.assertTrue(rebuilt)
        self.assertNotEqual(second.input_fingerprint, first.input_fingerprint)

    def test_agent_action_on_a_match_rebuilds(self):
        refresh_graph(self.event.id)
        match = Match.objects.get(need__event=self.event)
        match.status = MatchStatus.CONTACTED
        match.save()

        snapshot, rebuilt = refresh_graph(self.event.id)

        self.assertTrue(rebuilt)
        self.assertEqual(snapshot.payload["edges"][0]["status"], MatchStatus.CONTACTED)

    def test_writes_queue_one_rebuild_per_event_per_transaction(self):
        # A fresh event, because setUp already queued a rebuild for self.event here
        other = make_event(name="Otro evento")
        with (
            patch("ayudagente.radar.tasks.rebuild_graph.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            actor = make_actor(other, "Nuevo")
            make_requirement(other, actor, self.agua, make_location(PEREIRA, "Nuevo"))
            make_requirement(other, actor, self.agua, make_location(DOSQUEBRADAS, "Nuevo 2"))

        delay.assert_called_once_with(other.id)  # three writes, one enqueue

    def test_the_rebuild_does_not_retrigger_itself(self):
        # The pass writes matches; those writes must not queue another rebuild.
        with (
            patch("ayudagente.radar.tasks.rebuild_graph.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            refresh_graph(self.event.id)
        delay.assert_not_called()

    def test_view_serves_the_persisted_snapshot(self):
        refresh_graph(self.event.id)

        with self.assertNumQueries(2):  # event + snapshot, nothing derived
            response = self.client.get(reverse("radar:event-graph", args=[self.event.id]))

        payload = response.json()
        self.assertEqual(len(payload["nodes"]), 2)
        self.assertEqual(len(payload["edges"]), 1)
        self.assertIn("built_at", payload)


class GraphShowsWhatTheListShowsTests(ApiTestCase):
    """
    The map and the list have to agree on which requirements are live.

    Note:
        They did not. `services/graph.py` kept its own copy of the status set, never gained
        `unverified` when the policy did, and a live event drew 64 of 512 requirements on the
        map while the list endpoint returned all of them. Neither endpoint looked broken.
    """

    def test_the_graph_uses_the_same_statuses_the_policy_declares(self):
        from ayudagente.radar.views.policy import OPEN_REQUIREMENT_STATUSES

        self.assertIn(RequirementStatus.UNVERIFIED, OPEN_REQUIREMENT_STATUSES)

    def test_an_unverified_requirement_reaches_its_node(self):
        event = make_event()
        actor = make_actor(event, "Barrio Cuba")
        make_requirement(
            event,
            actor,
            make_resource("agua"),
            make_location(PEREIRA, "Pereira"),
            direction=Direction.NEEDS,
            status=RequirementStatus.UNVERIFIED,
        )

        snapshot, _ = refresh_graph(event.pk, force=True)

        node = next(n for n in snapshot.payload["nodes"] if n["id"] == actor.pk)
        self.assertEqual(len(node["requirements"]), 1)
        self.assertEqual(node["requirements"][0]["status"], RequirementStatus.UNVERIFIED)
