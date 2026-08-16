"""
Tests for the whole tool surface: the contracts that must hold for every tool.

Per-tool behaviour lives beside each tool. This file covers the properties that only make
sense across the set — that failures are values, that the toolsets stay separated, and that
the output of one tool is accepted as the input of the next.
"""

from decimal import Decimal

from django.test import TestCase
from langchain_core.tools import tool as tool_decorator

from agent_tools.create_harvest_job import create_harvest_job
from agent_tools.draft_outreach import draft_outreach
from agent_tools.get_actor_contacts import get_actor_contacts
from agent_tools.get_balance import get_balance
from agent_tools.get_frontier import get_frontier
from agent_tools.match_resource import match_resource
from agent_tools.propose_match import propose_match
from agent_tools.registry import (
    ALL_TOOLS,
    COORDINATION_TOOLS,
    EVENT_ARG,
    FRONTIER_TOOLS,
    bind_event,
    get_toolset,
)
from agent_tools.road_distance import road_distance
from ayudagente.radar.choices import (
    ActorKind,
    AdminLevel,
    ContactKind,
    Direction,
    MatchStatus,
    NodeStatus,
    OutreachPurpose,
    Platform,
    UnreachableReason,
    Urgency,
    Zone,
)
from ayudagente.radar.models import (
    AdminUnit,
    ContactPoint,
    FrontierNode,
    HarvestJob,
    Match,
)
from ayudagente.radar.tests.factories import (
    CALI,
    DOSQUEBRADAS,
    PEREIRA,
    make_actor,
    make_event,
    make_location,
    make_requirement,
    make_resource,
)


class RegistryTests(TestCase):
    def test_every_tool_is_registered_exactly_once(self):
        names = [tool.name for tool in ALL_TOOLS]
        self.assertEqual(len(names), len(set(names)))

    def test_the_frontier_agent_cannot_reach_requirement_data(self):
        # The separation is the guarantee: no shared tool means no path to a post
        frontier = {tool.name for tool in FRONTIER_TOOLS}
        coordination = {tool.name for tool in COORDINATION_TOOLS}
        self.assertEqual(frontier & coordination, set())

    def test_the_batch_matching_pass_is_not_callable_by_an_agent(self):
        self.assertNotIn("run_matching_pass", {tool.name for tool in ALL_TOOLS})

    def test_an_unknown_toolset_raises_rather_than_returning_nothing(self):
        with self.assertRaises(KeyError):
            get_toolset("does_not_exist", 1)

    def test_every_tool_describes_itself_for_the_model(self):
        for tool in ALL_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertGreater(len(tool.description), 80, "docstring is the prompt")
                for field in tool.args.values():
                    self.assertTrue(field.get("description"), "every argument is explained")

    def test_every_tool_can_be_scoped_to_an_event(self):
        # A tool that skips the argument would silently answer about every emergency
        for tool in ALL_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertIn(EVENT_ARG, tool.args)


class EventBindingTests(TestCase):
    """The event is not something the model gets to choose."""

    def setUp(self):
        self.event = make_event()
        self.other = make_event(name="Otro sismo")
        self.agua = make_resource("agua")

    def _need(self, event):
        return make_requirement(
            event,
            make_actor(event, f"Barrio {event.id}"),
            self.agua,
            make_location(PEREIRA, f"Barrio {event.id}"),
            quantity=Decimal(100),
        )

    def test_the_model_never_sees_the_event_argument(self):
        for tool in get_toolset("coordination", self.event.id):
            with self.subTest(tool=tool.name):
                self.assertNotIn(EVENT_ARG, tool.args)

    def test_a_bound_tool_keeps_its_name_and_description(self):
        # The name is what the prompt refers to and what the frontend labels the step with
        bound = {tool.name: tool for tool in get_toolset("coordination", self.event.id)}
        for tool in COORDINATION_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertEqual(bound[tool.name].description, tool.description)

    def test_a_bound_tool_answers_from_its_own_event(self):
        self._need(self.event)
        self._need(self.other)
        balance = {tool.name: tool for tool in get_toolset("coordination", self.event.id)}[
            "get_balance"
        ]

        rows = balance.invoke({})["balance"]

        self.assertEqual(len(rows), 1)  # one event's need, not both

    def test_a_row_from_another_emergency_is_refused_not_answered(self):
        foreign = self._need(self.other)
        coverage = {tool.name: tool for tool in get_toolset("coordination", self.event.id)}[
            "check_coverage"
        ]

        result = coverage.invoke({"requirement_id": foreign.id})

        self.assertIn("error", result)
        self.assertIn("another emergency", result["error"])

    def test_a_tool_without_the_argument_is_refused_at_build_time(self):
        @tool_decorator("unscoped")
        def unscoped(actor_id: int) -> dict:
            """A tool that forgot to declare which emergency it reads."""
            return {"actor_id": actor_id}

        with self.assertRaises(KeyError):
            bind_event(unscoped, self.event.id)


class FailuresAreValuesTests(TestCase):
    """No tool raises. An exception reaches the model as a trace it cannot act on."""

    def setUp(self):
        self.event = make_event()

    def test_reads_report_a_missing_event(self):
        cases = [
            (match_resource, {"event_id": 99999, "offering": True}),
            (get_balance, {"event_id": 99999}),
            (get_frontier, {"event_id": 99999}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool.name):
                self.assertIn("error", tool.invoke(args))

    def test_writes_report_missing_rows(self):
        cases = [
            (
                propose_match,
                {"event_id": self.event.id, "need_id": 1, "offer_id": 2, "rationale": "x" * 30},
            ),
            (
                draft_outreach,
                {
                    "event_id": self.event.id,
                    "contact_point_id": 1,
                    "purpose": "answer",
                    "body": "y" * 60,
                },
            ),
            (create_harvest_job, {"event_id": 1, "node_id": 1, "rationale": "z" * 30}),
            (
                road_distance,
                {"event_id": self.event.id, "from_requirement_id": 1, "to_requirement_id": 2},
            ),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool.name):
                self.assertIn("error", tool.invoke(args))

    def test_a_read_that_fails_still_returns_its_empty_list(self):
        # A caller reading the list without checking `error` must see nothing, not crash
        self.assertEqual(
            match_resource.invoke({"event_id": 99999, "offering": True})["candidates"],
            [],
        )
        self.assertEqual(get_balance.invoke({"event_id": 99999})["balance"], [])


class BalanceToolTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua", name="Agua")
        self.unit = AdminUnit.objects.create(
            country_code="CO",
            code="66001",
            name="Pereira",
            name_norm="pereira",
            level=AdminLevel.ADMIN_2,
        )

    def _req(self, direction, quantity, unit="litros"):
        return make_requirement(
            self.event,
            make_actor(self.event, f"{direction}-{quantity}-{unit}"),
            self.agua,
            make_location(PEREIRA, f"{direction}{quantity}{unit}", admin_unit=self.unit),
            direction=direction,
            quantity=quantity,
            unit=unit,
        )

    def test_net_is_offered_minus_needed(self):
        self._req(Direction.NEEDS, Decimal(300))
        self._req(Direction.OFFERS, Decimal(100))

        row = get_balance.invoke({"event_id": self.event.id})["balance"][0]

        self.assertEqual(row["net"], -200.0)
        self.assertEqual(row["resource_key"], "agua")  # chains into match_resource

    def test_unstated_quantities_are_counted_not_summed_as_zero(self):
        self._req(Direction.NEEDS, None)

        row = get_balance.invoke({"event_id": self.event.id})["balance"][0]

        self.assertEqual(row["needed"], 0)
        self.assertEqual(row["unknown_quantity"], 1)  # not readable as covered

    def test_different_units_are_never_added_together(self):
        self._req(Direction.NEEDS, Decimal(300), unit="litros")
        self._req(Direction.OFFERS, Decimal(50), unit="botellas")

        rows = {r["unit"]: r for r in get_balance.invoke({"event_id": self.event.id})["balance"]}

        self.assertEqual(rows["litros"]["net"], -300.0)
        self.assertEqual(rows["botellas"]["net"], 50.0)

    def test_balance_and_detail_agree_on_what_is_live(self):
        saturated = self._req(Direction.NEEDS, Decimal(20))
        saturated.covered_quantity = Decimal(20)
        saturated.save()

        balance = get_balance.invoke({"event_id": self.event.id})
        detail = match_resource.invoke({"event_id": self.event.id, "offering": True})

        self.assertEqual(balance["balance"], [])
        self.assertEqual(detail["count"], 0)

    def test_only_deficits_drops_the_covered_rows(self):
        self._req(Direction.OFFERS, Decimal(500))

        result = get_balance.invoke({"event_id": self.event.id, "only_deficits": True})

        self.assertEqual(result["balance"], [])


class ActorContactsToolTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.actor = make_actor(self.event, "ONG Pereira", kind=ActorKind.NONPROFIT)

    def _contact(self, kind, value, **kwargs):
        return ContactPoint.objects.create(
            actor=self.actor, kind=kind, value=value, raw_value=value, **kwargs
        )

    def test_the_contact_value_reaches_the_model(self):
        # The reader is a member of the public: a fact about a phone is not a phone
        self._contact(ContactKind.EMAIL, "ong@pereira.org")

        row = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": self.actor.id})[
            "contacts"
        ][0]

        self.assertEqual(row["value"], "ong@pereira.org")
        self.assertIn("contact_point_id", row)  # the id is what other tools take

    def test_how_trustworthy_a_detail_is_travels_with_it(self):
        self._contact(ContactKind.PHONE, "+573001112233", times_seen=1)

        row = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": self.actor.id})[
            "contacts"
        ][0]

        self.assertEqual(row["times_seen"], 1)
        self.assertFalse(row["verified"])

    def test_channels_come_back_in_preference_order(self):
        self._contact(ContactKind.PHONE, "+573001112233")
        self._contact(ContactKind.EMAIL, "ong@pereira.org")

        kinds = [
            c["kind"]
            for c in get_actor_contacts.invoke(
                {"event_id": self.event.id, "actor_id": self.actor.id}
            )["contacts"]
        ]

        self.assertEqual(kinds, ["email", "phone"])

    def test_a_payment_account_is_not_offered_as_a_channel(self):
        self._contact(ContactKind.PAYMENT, "3001112233", payment_network="nequi")

        usable = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": self.actor.id})
        everything = get_actor_contacts.invoke(
            {"event_id": self.event.id, "actor_id": self.actor.id, "include_unusable": True}
        )

        self.assertEqual(usable["count"], 0)
        self.assertEqual(everything["count"], 1)

    def test_a_merged_actor_resolves_to_the_one_that_absorbed_it(self):
        duplicate = make_actor(self.event, "ong pereira", merged_into=self.actor)

        result = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": duplicate.id})

        self.assertEqual(result["actor_id"], self.actor.id)

    def test_a_chain_of_merges_resolves_all_the_way(self):
        # Merges arrive one at a time: A into B, then B into C
        middle = make_actor(self.event, "ong", merged_into=self.actor)
        oldest = make_actor(self.event, "la ong", merged_into=middle)

        result = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": oldest.id})

        self.assertEqual(result["actor_id"], self.actor.id)

    def test_a_merge_cycle_does_not_hang_the_request(self):
        other = make_actor(self.event, "Otra ONG", kind=ActorKind.NONPROFIT)
        self.actor.merged_into = other
        self.actor.save()
        other.merged_into = self.actor
        other.save()

        result = get_actor_contacts.invoke({"event_id": self.event.id, "actor_id": self.actor.id})

        self.assertIn(result["actor_id"], {self.actor.id, other.id})  # returns, wrong or not


class ProposeMatchToolTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.agua = make_resource("agua")
        self.need = make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            self.agua,
            make_location(PEREIRA, "Barrio"),
            quantity=Decimal(100),
            urgency=Urgency.CRITICAL,
        )
        self.offer = make_requirement(
            self.event,
            make_actor(self.event, "Vecino"),
            self.agua,
            make_location(DOSQUEBRADAS, "Vecino"),
            direction=Direction.OFFERS,
            quantity=Decimal(100),
        )

    def _propose(self, **kwargs):
        args = {
            "event_id": self.event.id,
            "need_id": self.need.id,
            "offer_id": self.offer.id,
            "rationale": "Vecino ofrece agua a 3 km de una necesidad crítica del Barrio.",
        }
        args.update(kwargs)
        return propose_match.invoke(args)

    def test_a_valid_pairing_is_written(self):
        result = self._propose()

        self.assertEqual(result["status"], MatchStatus.PROPOSED)
        self.assertEqual(Match.objects.get(id=result["match_id"]).need_id, self.need.id)

    def test_a_vague_rationale_is_refused(self):
        self.assertIn("error", self._propose(rationale="ok"))

    def test_a_pairing_a_human_already_touched_is_not_overwritten(self):
        match_id = self._propose()["match_id"]
        Match.objects.filter(id=match_id).update(status=MatchStatus.CONTACTED)

        result = self._propose(rationale="Otro texto completamente distinto para el match.")

        self.assertIn("error", result)
        self.assertEqual(Match.objects.get(id=match_id).status, MatchStatus.CONTACTED)

    def test_crossing_events_is_refused(self):
        other = make_event(name="Otro sismo")
        foreign = make_requirement(
            other,
            make_actor(other, "Ajeno"),
            self.agua,
            make_location(CALI, "Ajeno"),
            direction=Direction.OFFERS,
        )
        self.assertIn("error", self._propose(offer_id=foreign.id))

    def test_a_need_for_transport_cannot_be_the_bridge(self):
        transporte = make_resource("transporte")
        transport_need = make_requirement(
            self.event,
            make_actor(self.event, "Necesita camión"),
            transporte,
            make_location(PEREIRA, "Necesita camión"),
        )
        self.assertIn("error", self._propose(via_transport_id=transport_need.id))


class DraftOutreachToolTests(TestCase):
    def setUp(self):
        self.event = make_event()
        agua = make_resource("agua")
        self.need = make_requirement(
            self.event,
            make_actor(self.event, "Barrio"),
            agua,
            make_location(PEREIRA, "Barrio"),
        )
        self.offer_actor = make_actor(self.event, "ONG Pereira", kind=ActorKind.NONPROFIT)
        self.offer = make_requirement(
            self.event,
            self.offer_actor,
            agua,
            make_location(DOSQUEBRADAS, "ONG"),
            direction=Direction.OFFERS,
        )
        self.contact = ContactPoint.objects.create(
            actor=self.offer_actor,
            kind=ContactKind.WHATSAPP,
            value="+573001112233",
            raw_value="300 111 2233",
            confidence=0.9,
        )
        self.body = (
            "Hola, somos AyudAgente. Encontramos una necesidad de agua en el Barrio, "
            "a 3 km de ustedes. ¿Pueden ayudar?"
        )

    def _draft(self, **kwargs):
        args = {
            "event_id": self.event.id,
            "contact_point_id": self.contact.id,
            "purpose": OutreachPurpose.ANSWER,
            "body": self.body,
        }
        args.update(kwargs)
        return draft_outreach.invoke(args)

    def test_a_draft_carries_a_clickable_prefilled_link(self):
        result = self._draft()

        self.assertTrue(result["target_url"].startswith("https://wa.me/573001112233"))
        self.assertTrue(result["text_is_prefilled"])
        self.assertEqual(result["status"], "draft")  # nothing was sent

    def test_drafting_twice_returns_the_same_proposal(self):
        first = self._draft()
        second = self._draft(body=self.body.replace("Hola", "Buenas"))

        self.assertEqual(first["outreach_id"], second["outreach_id"])

    def test_a_retry_is_reported_as_one_even_when_the_text_is_identical(self):
        # The retry that matters resends the same body; comparing bodies gets it backwards
        self.assertFalse(self._draft()["already_existed"])
        self.assertTrue(self._draft()["already_existed"])

    def test_an_actor_who_opted_out_is_refused(self):
        self.contact.reachable = False
        self.contact.unreachable_reason = UnreachableReason.OPTED_OUT
        self.contact.save()

        self.assertIn("error", self._draft())

    def test_a_stranger_cannot_be_written_to_about_someone_elses_match(self):
        match = Match.objects.create(need=self.need, offer=self.offer, score=0.5)
        stranger = make_actor(self.event, "Ajeno", kind=ActorKind.NONPROFIT)
        stranger_contact = ContactPoint.objects.create(
            actor=stranger,
            kind=ContactKind.WHATSAPP,
            value="+573009998877",
            raw_value="300 999 8877",
        )

        result = self._draft(contact_point_id=stranger_contact.id, match_id=match.id)

        self.assertIn("error", result)

    def test_a_body_too_short_to_be_a_message_is_refused(self):
        self.assertIn("error", self._draft(body="hola"))

    def test_an_unknown_purpose_is_refused(self):
        self.assertIn("error", self._draft(purpose="saludar"))


class FrontierToolsTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.unit = AdminUnit.objects.create(
            country_code="CO",
            code="27001",
            name="Quibdó",
            name_norm="quibdo",
            level=AdminLevel.ADMIN_2,
        )
        self.event.lexicon = {
            "hashtags": ["#SismoChoco"],
            "nicknames": ["sismo del Chocó"],
            "negatives": ["Turquía"],
        }
        self.event.save()

    def _node(self, **kwargs):
        defaults = {
            "event": self.event,
            "admin_unit": self.unit,
            "platform": Platform.X,
            "zone": Zone.IMPACT,
        }
        defaults.update(kwargs)
        return FrontierNode.objects.create(**defaults)

    def test_unexplored_targets_are_flagged_not_left_to_be_inferred(self):
        self._node()

        result = get_frontier.invoke({"event_id": self.event.id})

        self.assertEqual(result["unexplored"], 1)
        self.assertTrue(result["nodes"][0]["is_unexplored"])

    def test_nodes_come_back_best_yield_first(self):
        self._node(yield_rate=5, platform=Platform.X)
        self._node(yield_rate=40, platform=Platform.FACEBOOK)

        rates = [n["yield_rate"] for n in get_frontier.invoke({"event_id": self.event.id})["nodes"]]

        self.assertEqual(rates, [40.0, 5.0])

    def test_exhausted_targets_are_not_offered_as_decisions(self):
        self._node(status=NodeStatus.EXHAUSTED)

        self.assertEqual(get_frontier.invoke({"event_id": self.event.id})["count"], 0)

    def test_the_query_is_built_from_the_lexicon_not_by_the_caller(self):
        node = self._node()

        result = create_harvest_job.invoke(
            {
                "event_id": self.event.id,
                "node_id": node.id,
                "rationale": "Nodo sin explorar en la zona de impacto, vale una pasada.",
            }
        )

        job = HarvestJob.objects.get(id=result["job_id"])
        query = job.actor_input["searchTerms"][0]
        self.assertIn("Quibdó", query)  # invariant: always anchored to a real toponym
        self.assertIn("#SismoChoco", query)
        self.assertIn('-"Turquía"', query)  # other emergencies excluded
        self.assertEqual(job.decided_by, "agent")

    def test_the_toponym_survives_a_lexicon_long_enough_to_hit_the_cap(self):
        self.event.lexicon = {
            "hashtags": [f"#tag{i}" for i in range(30)],
            "nicknames": ["sismo del Chocó"],
            "negatives": ["Turquía"],
        }
        self.event.save()
        node = self._node()

        result = create_harvest_job.invoke(
            {
                "event_id": self.event.id,
                "node_id": node.id,
                "rationale": "Léxico largo: el topónimo no puede caer fuera del recorte.",
            }
        )

        query = HarvestJob.objects.get(id=result["job_id"]).actor_input["searchTerms"][0]
        self.assertIn("Quibdó", query)  # the anchor, without which the query pulls the world
        self.assertIn('-"Turquía"', query)

    def test_an_empty_rationale_is_refused(self):
        node = self._node()

        result = create_harvest_job.invoke(
            {"event_id": self.event.id, "node_id": node.id, "rationale": "   "}
        )

        self.assertIn("error", result)
        self.assertEqual(HarvestJob.objects.count(), 0)

    def test_a_node_from_another_event_is_refused(self):
        other = make_event(name="Otro")
        foreign = FrontierNode.objects.create(
            event=other, admin_unit=self.unit, platform=Platform.X, zone=Zone.IMPACT
        )

        result = create_harvest_job.invoke(
            {
                "event_id": self.event.id,
                "node_id": foreign.id,
                "rationale": "Intento de cruzar el límite del evento a propósito.",
            }
        )

        self.assertIn("error", result)

    def test_a_paused_event_accepts_no_new_jobs(self):
        node = self._node()
        self.event.status = "paused"
        self.event.save()

        result = create_harvest_job.invoke(
            {
                "event_id": self.event.id,
                "node_id": node.id,
                "rationale": "El evento está pausado, esto no debería crear nada.",
            }
        )

        self.assertIn("error", result)
        self.assertEqual(HarvestJob.objects.count(), 0)
