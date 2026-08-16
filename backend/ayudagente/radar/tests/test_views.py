"""Endpoint tests for the graph snapshot API."""

from decimal import Decimal

from django.urls import reverse

from ayudagente.radar.choices import ActorKind, Direction, MatchStatus, RequirementStatus
from ayudagente.radar.services import propose_match, refresh_graph, run_matching_pass
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


class EventListTests(ApiTestCase):
    def test_lists_active_events_only(self):
        make_event(name="Sismo activo")
        make_event(name="Viejo", status="archived")

        response = self.client.get(reverse("radar:event-list"))

        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        self.assertEqual([e["name"] for e in events], ["Sismo activo"])
        self.assertEqual(events[0]["epicenter"], {"lat": PEREIRA.y, "lon": PEREIRA.x})


class EventGraphTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.agua = make_resource("agua")

    def test_graph_contains_nodes_edges_and_requirements(self):
        need_actor = make_actor(self.event, "Barrio Cuba")
        make_requirement(
            self.event,
            need_actor,
            self.agua,
            make_location(PEREIRA, "Barrio Cuba"),
            quantity=Decimal(100),
            covered_quantity=Decimal(25),
        )
        offer_actor = make_actor(
            self.event, "Centro Dosquebradas", kind=ActorKind.COLLECTION_CENTER
        )
        make_requirement(
            self.event,
            offer_actor,
            self.agua,
            make_location(DOSQUEBRADAS, "Centro"),
            direction=Direction.OFFERS,
            quantity=Decimal(200),
        )
        run_matching_pass(self.event.id)

        response = self.client.get(reverse("radar:event-graph", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        nodes = {n["id"]: n for n in payload["nodes"]}
        self.assertIn(need_actor.id, nodes)
        self.assertIn(offer_actor.id, nodes)

        node = nodes[need_actor.id]
        self.assertEqual(node["location"], {"lat": PEREIRA.y, "lon": PEREIRA.x})
        req = node["requirements"][0]
        self.assertEqual(req["direction"], Direction.NEEDS)
        self.assertEqual(req["outstanding"], 75.0)

        self.assertEqual(len(payload["edges"]), 1)
        edge = payload["edges"][0]
        self.assertEqual(edge["from_actor"], offer_actor.id)
        self.assertEqual(edge["to_actor"], need_actor.id)
        self.assertEqual(edge["resource"], "Agua")
        self.assertTrue(edge["rationale"])

    def test_graph_hides_merged_actors_covered_requirements_discarded_matches(self):
        actor = make_actor(self.event, "Actor")
        make_requirement(
            self.event,
            actor,
            self.agua,
            make_location(PEREIRA, "x"),
            status=RequirementStatus.COVERED,
        )
        ghost = make_actor(self.event, "Duplicado", merged_into=actor)

        need = make_requirement(
            self.event,
            actor,
            self.agua,
            make_location(PEREIRA, "y"),
        )
        offer = make_requirement(
            self.event,
            make_actor(self.event, "Oferente"),
            self.agua,
            make_location(DOSQUEBRADAS, "z"),
            direction=Direction.OFFERS,
        )
        match = propose_match(need, offer)
        assert match is not None
        match.status = MatchStatus.DISCARDED
        match.save()

        payload = self.client.get(reverse("radar:event-graph", args=[self.event.id])).json()

        node_ids = {n["id"] for n in payload["nodes"]}
        self.assertNotIn(ghost.id, node_ids)
        actor_node = next(n for n in payload["nodes"] if n["id"] == actor.id)
        covered = [r for r in actor_node["requirements"] if r["status"] == "covered"]
        self.assertEqual(covered, [])
        self.assertEqual(payload["edges"], [])

    def test_unknown_event_404(self):
        response = self.client.get(reverse("radar:event-graph", args=[999]))
        self.assertEqual(response.status_code, 404)

    def test_graph_query_count_is_constant(self):
        # N+1 guard: query count must not grow with graph size.
        for i in range(12):
            actor = make_actor(self.event, f"Actor {i}")
            make_requirement(
                self.event,
                actor,
                self.agua,
                make_location(PEREIRA, f"lugar {i}"),
                direction=Direction.NEEDS if i % 2 else Direction.OFFERS,
                quantity=Decimal(10),
            )
        run_matching_pass(self.event.id)
        refresh_graph(self.event.id)  # persist the snapshot the view will serve

        with self.assertNumQueries(2):  # event + snapshot row; size-independent
            self.client.get(reverse("radar:event-graph", args=[self.event.id]))
