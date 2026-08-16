"""
Tests for the agent layer, without an LLM.

What is worth testing here is everything except the model: that the prompts carry the rules
the tools deliberately do not, that a run is translated into events a browser can read, and
that the endpoint refuses bad input before it opens a stream it cannot take back.

The model itself is out of scope by design — asserting on generated prose tests the
weather, and any test that called the provider would stop the suite from being hermetic.
"""

import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from psycopg.conninfo import conninfo_to_dict

from agent_tools.agents import Coordinates, render_prompt, translate_chunk
from agent_tools.agents.build import describe_location, load_prompt
from agent_tools.agents.checkpointer import build_connection_string
from agent_tools.agents.llm import LLMNotConfigured, accepts_temperature, build_chat_model
from agent_tools.agents.streaming import (
    MAX_FOCUS_IDS,
    collect_focus,
    stream_agent,
    summarize_tool_result,
)
from agent_tools.registry import TOOLSETS
from ayudagente.radar.tests.factories import ApiTestCase, make_event

# Tools that existed and no longer do. A prompt still naming one is a dead end for the model.
RETIRED_TOOLS = ("find_requirements", "plan_trip_stops")


class PromptTests(TestCase):
    """Prompts carry behaviour. Everything they promise has to be true of the tools."""

    def setUp(self):
        self.event = make_event(name="Sismo demo")

    def test_every_toolset_has_a_prompt(self):
        for toolset in TOOLSETS:
            with self.subTest(toolset=toolset):
                self.assertTrue(load_prompt(toolset).strip())

    def test_a_missing_prompt_fails_loudly_instead_of_running_empty(self):
        with self.assertRaises(FileNotFoundError):
            load_prompt("no_such_agent")

    def test_the_event_is_rendered_into_the_prompt(self):
        prompt = render_prompt("coordination", self.event)

        self.assertIn("Sismo demo", prompt)
        self.assertIn(str(self.event.id), prompt)
        self.assertIn("Colombia", prompt)
        self.assertNotIn("{event_name}", prompt)  # nothing left unfilled

    def test_the_coordination_prompt_states_the_rules_the_tools_cannot_enforce(self):
        prompt = render_prompt("coordination", self.event).lower()

        self.assertIn("confirmed", prompt)  # an unconfirmed lead is never stated as fact
        self.assertIn("get_actor_contacts", prompt)  # a citizen needs a way to check first

        self.assertIn("match_resource", prompt)  # the tool most questions start from
        self.assertIn("do not translate", prompt)  # the catalog is Spanish
        self.assertIn("still_needed", prompt)  # already subtracts what others promised
        self.assertIn("reachable_by_us", prompt)  # a match nobody can be told about
        self.assertIn("road_distance", prompt)  # straight-line distance is a floor

    def test_a_prompt_never_points_at_a_tool_that_was_removed(self):
        # Silent failure: the model calls a dead name and the turn ends for no visible reason
        for toolset in TOOLSETS:
            prompt = render_prompt(toolset, self.event)
            for retired in RETIRED_TOOLS:
                with self.subTest(prompt=toolset, retired=retired):
                    self.assertNotIn(retired, prompt)

    def test_each_prompt_names_the_tools_its_agent_actually_holds(self):
        for toolset, tools in TOOLSETS.items():
            prompt = render_prompt(toolset, self.event)
            named = [tool.name for tool in tools if tool.name in prompt]
            with self.subTest(prompt=toolset):
                self.assertTrue(named, "a prompt that names none of its tools teaches nothing")

    def test_the_frontier_prompt_defends_forced_exploration(self):
        prompt = render_prompt("frontier", self.event).lower()

        self.assertIn("is_unexplored", prompt)
        self.assertIn("rationale", prompt)
        self.assertNotIn("match_resource", prompt)  # not in its world at all

    def test_where_the_coordinator_is_reaches_the_prompt(self):
        # The one fact no tool can look up, and what "cerca de mí" resolves against
        prompt = render_prompt("coordination", self.event, Coordinates(5.6947, -76.6611))

        self.assertIn("5.69470", prompt)
        self.assertIn("-76.66110", prompt)
        self.assertNotIn("{user_location}", prompt)

    def test_a_coordinator_who_shared_nothing_is_said_to_have_shared_nothing(self):
        # Omitting the line reads as "location does not apply", and it stops asking
        prompt = render_prompt("coordination", self.event)

        self.assertIn("has not shared their position", prompt)
        self.assertNotIn("{user_location}", prompt)

    def test_the_frontier_prompt_ignores_a_position_it_has_no_use_for(self):
        # Where the coordinator stands says nothing about where to harvest next
        prompt = render_prompt("frontier", self.event, Coordinates(5.6947, -76.6611))

        self.assertNotIn("5.69470", prompt)

    def test_a_position_is_described_to_the_metre_and_no_further(self):
        described = describe_location(Coordinates(4.8143216789, -75.6946512345))

        self.assertIn("4.81432", described)
        self.assertIn("-75.69465", described)
        self.assertNotIn("4.8143216789", described)


class TranslateChunkTests(TestCase):
    """The mapping from LangGraph's shapes to the frontend's."""

    def test_a_token_becomes_a_token_event(self):
        events = translate_chunk("messages", (AIMessageChunk(content="En Quibdó"), {}))

        self.assertEqual(events, [{"type": "token", "text": "En Quibdó"}])

    def test_a_token_arriving_as_content_blocks_is_still_a_token_event(self):
        # What the Responses API sends; read as a string it is empty and the bubble never fills
        chunk = AIMessageChunk(content=[{"type": "text", "text": "En Quibdó", "index": 0}])

        self.assertEqual(
            translate_chunk("messages", (chunk, {})),
            [{"type": "token", "text": "En Quibdó"}],
        )

    def test_a_reasoning_block_is_not_streamed_as_the_answer(self):
        chunk = AIMessageChunk(
            content=[
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "pensando"}]},
                {"type": "text", "text": "Faltan 2600 L", "index": 1},
            ]
        )

        self.assertEqual(
            translate_chunk("messages", (chunk, {})),
            [{"type": "token", "text": "Faltan 2600 L"}],
        )

    def test_empty_tokens_are_not_sent(self):
        self.assertEqual(translate_chunk("messages", (AIMessageChunk(content=""), {})), [])
        self.assertEqual(translate_chunk("messages", (AIMessageChunk(content=[]), {})), [])
        # An annotation block carries no `text` key at all
        annotation = AIMessageChunk(content=[{"type": "text", "annotations": [], "index": 0}])
        self.assertEqual(translate_chunk("messages", (annotation, {})), [])

    def test_a_tool_call_is_announced_before_it_runs(self):
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": "get_balance", "args": {"event_id": 1}, "id": "call_1"},
            ],
        )

        events = translate_chunk("updates", {"model": {"messages": [message]}})

        self.assertEqual(events[0]["type"], "tool_start")
        self.assertEqual(events[0]["name"], "get_balance")
        self.assertEqual(events[0]["args"], {"event_id": 1})

    def test_a_tool_result_is_summarized_not_replayed(self):
        rows = [{"resource_key": f"recurso_{i}"} for i in range(12)]
        payload = json.dumps({"count": 12, "truncated": True, "balance": rows})
        message = ToolMessage(content=payload, name="get_balance", tool_call_id="call_1")

        events = translate_chunk("updates", {"tools": {"messages": [message]}})

        self.assertEqual(events[0]["type"], "tool_end")
        self.assertEqual(events[0]["result"], {"ok": True, "count": 12, "truncated": True})
        self.assertNotIn("recurso_0", str(events[0]))  # the rows never reach the browser

    def test_a_tool_failure_is_visible_without_parsing_the_payload(self):
        message = ToolMessage(
            content=json.dumps({"error": "unknown resource 'water'", "available": []}),
            name="find_requirements",
            tool_call_id="call_2",
        )

        result = translate_chunk("updates", {"tools": {"messages": [message]}})[0]["result"]

        self.assertFalse(result["ok"])
        self.assertIn("water", result["error"])

    def test_chunks_carrying_nothing_a_user_needs_produce_no_events(self):
        self.assertEqual(translate_chunk("updates", {"model": {"messages": []}}), [])
        self.assertEqual(translate_chunk("updates", "not a dict"), [])
        self.assertEqual(translate_chunk("values", {"anything": 1}), [])
        self.assertEqual(
            translate_chunk("updates", {"model": {"messages": [HumanMessage(content="hi")]}}),
            [],
        )

    def test_a_non_json_tool_result_is_still_reported(self):
        self.assertEqual(summarize_tool_result("plain text"), {"ok": True, "preview": "plain text"})

    def test_the_ids_a_tool_returned_are_streamed_so_a_map_can_point_at_them(self):
        # The prose never names a row; without this the map has nothing to resolve
        payload = json.dumps(
            {
                "count": 2,
                "candidates": [
                    {"requirement_id": 7, "actor_id": 3, "actor": "Coliseo Mayor"},
                    {"requirement_id": 9, "actor_id": 5, "actor": "JAC Niño Jesús"},
                ],
            }
        )
        message = ToolMessage(content=payload, name="match_resource", tool_call_id="c1")

        events = translate_chunk("updates", {"tools": {"messages": [message]}})

        self.assertEqual(events[0]["type"], "tool_end")
        self.assertEqual(
            events[1],
            {"type": "focus", "name": "match_resource", "actors": [3, 5], "requirements": [7, 9]},
        )

    def test_a_result_naming_nothing_drawable_sends_no_focus(self):
        message = ToolMessage(
            content=json.dumps({"count": 3, "balance": [{"resource_key": "agua"}]}),
            name="get_balance",
            tool_call_id="c1",
        )

        kinds = [e["type"] for e in translate_chunk("updates", {"tools": {"messages": [message]}})]

        self.assertEqual(kinds, ["tool_end"])

    def test_ids_are_found_however_deep_the_payload_buries_them(self):
        # find_gaps returns four lists; check_coverage nests carriers inside a row
        focus = collect_focus(
            {
                "unattended": [{"actor_id": 3}],
                "cut_off": [{"actor_id": 8, "carriers": [{"actor_id": 11}]}],
            }
        )

        self.assertEqual(focus["actors"], [3, 8, 11])

    def test_the_same_actor_named_twice_is_one_id(self):
        focus = collect_focus({"rows": [{"actor_id": 3}, {"actor_id": 3}, {"actor_id": 4}]})

        self.assertEqual(focus["actors"], [3, 4])

    def test_a_flag_that_happens_to_be_a_bool_is_not_an_id(self):
        # `True` is an `int` in Python, and a `1` in the actors list is a real node
        self.assertEqual(collect_focus({"actor_id": True, "requirement_id": 4})["actors"], [])

    def test_more_ids_than_a_camera_can_frame_are_capped(self):
        rows = [{"actor_id": i} for i in range(50)]

        self.assertEqual(len(collect_focus({"rows": rows})["actors"]), MAX_FOCUS_IDS)

    def test_a_result_that_is_not_json_focuses_nothing(self):
        self.assertEqual(collect_focus("plain text"), {})


class FakeGraph:
    """A compiled agent's `stream`, without a model behind it."""

    def __init__(self, chunks=None, raises=None):
        self.chunks = chunks or []
        self.raises = raises
        self.config: dict = {}

    def stream(self, _input, config=None, stream_mode=None):
        self.config = config or {}
        if self.raises:
            raise self.raises
        yield from self.chunks


def streamed_body(response) -> str:
    """Drain a `StreamingHttpResponse` into text."""
    return b"".join(response.streaming_content).decode()


class StreamAgentTests(TestCase):
    def _events(self, graph):
        return [
            json.loads(line.removeprefix("data: ")) for line in stream_agent(graph, "hola", "t1")
        ]

    def test_a_run_is_bracketed_by_start_and_done(self):
        events = self._events(FakeGraph())

        self.assertEqual(events[0], {"type": "start", "thread_id": "t1"})
        self.assertEqual(events[-1], {"type": "done", "thread_id": "t1"})

    def test_the_thread_id_reaches_the_graph_so_the_conversation_continues(self):
        graph = FakeGraph()
        list(stream_agent(graph, "hola", "t42"))

        self.assertEqual(graph.config["configurable"]["thread_id"], "t42")

    def test_a_failure_mid_run_is_streamed_rather_than_raised(self):
        # The status code is long gone by now; raising would just drop the connection
        events = self._events(FakeGraph(raises=RuntimeError("the provider said no")))

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("the provider said no", events[-1]["error"])

    def test_a_full_turn_streams_tools_then_prose(self):
        graph = FakeGraph(
            chunks=[
                (
                    "updates",
                    {
                        "model": {
                            "messages": [
                                AIMessage(
                                    content="",
                                    tool_calls=[{"name": "get_balance", "args": {}, "id": "c1"}],
                                )
                            ]
                        }
                    },
                ),
                (
                    "updates",
                    {
                        "tools": {
                            "messages": [
                                ToolMessage(
                                    content=json.dumps({"count": 3}),
                                    name="get_balance",
                                    tool_call_id="c1",
                                )
                            ]
                        }
                    },
                ),
                ("messages", (AIMessageChunk(content="Faltan 2600 L"), {})),
            ]
        )

        kinds = [event["type"] for event in self._events(graph)]

        self.assertEqual(kinds, ["start", "tool_start", "tool_end", "token", "done"])


class CheckpointerTests(TestCase):
    def test_a_password_with_url_metacharacters_survives(self):
        # As a URL this reparses into a different host and database, and says neither
        db = {
            "USER": "hackaton",
            "PASSWORD": "p@ss/w:rd#1",
            "HOST": "localhost",
            "PORT": "5433",
            "NAME": "hackaton",
        }
        with self.settings(DATABASES={"default": db}):
            conninfo = build_connection_string()

        parsed = conninfo_to_dict(conninfo)
        self.assertEqual(parsed["password"], "p@ss/w:rd#1")
        self.assertEqual(parsed["host"], "localhost")
        self.assertEqual(parsed["dbname"], "hackaton")


class LLMConfigTests(TestCase):
    """One source of truth for which model runs: the role map the rest of the system uses."""

    def _build(self, model_name="gpt-5.6-sol", **overrides) -> ChatOpenAI:
        """Build with a key present and one role mapped, narrowed to the concrete class."""
        models = {**settings.OPENAI_MODELS, "reasoning": model_name}
        with self.settings(OPENAI_API_KEY="sk-test", OPENAI_MODELS=models, **overrides):
            model = build_chat_model()
        assert isinstance(model, ChatOpenAI)
        return model

    def test_a_missing_key_names_the_variable_to_set(self):
        with self.settings(OPENAI_API_KEY=""), self.assertRaises(LLMNotConfigured) as ctx:
            build_chat_model()

        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_a_role_with_no_model_configured_is_refused(self):
        models = {**settings.OPENAI_MODELS, "reasoning": ""}
        with (
            self.settings(OPENAI_API_KEY="sk-test", OPENAI_MODELS=models),
            self.assertRaises(LLMNotConfigured) as ctx,
        ):
            build_chat_model()

        self.assertIn("reasoning", str(ctx.exception))

    def test_the_agent_runs_the_model_the_reasoning_role_names(self):
        # Not its own variable: a second one would drift from OPENAI_MODEL_REASONING
        self.assertEqual(self._build(model_name="gpt-5.6-sol").model_name, "gpt-5.6-sol")
        self.assertEqual(self._build(model_name="gpt-4o").model_name, "gpt-4o")

    def test_tool_calls_go_through_the_responses_api(self):
        # chat/completions refuses function tools on a reasoning model: 400 on the first call
        self.assertTrue(self._build(model_name="gpt-5.6-sol").use_responses_api)

    def test_a_reasoning_model_is_sent_no_temperature(self):
        # Reasoning models reject `temperature` outright — sending it is a 400 every call
        self.assertIsNone(self._build(model_name="gpt-5.6-sol").temperature)

    def test_a_classic_model_still_gets_one(self):
        self.assertEqual(self._build(model_name="gpt-4o").temperature, 0.2)

    def test_the_capability_split_matches_the_model_families(self):
        for name in ("gpt-5.6-sol", "gpt-5.6-luna", "o3-mini"):
            self.assertFalse(accepts_temperature(name), name)
        for name in ("gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"):
            self.assertTrue(accepts_temperature(name), name)


class AgentEndpointTests(ApiTestCase):
    """Everything the endpoint must settle before a single byte is streamed."""

    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.url = reverse("radar:agent-coordination")

    def _post(self, payload):
        return self.client.post(self.url, data=payload, content_type="application/json")

    def test_a_missing_message_is_a_400_not_a_stream(self):
        response = self._post({"event_id": self.event.id})

        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.json()["error"])

    def test_an_unknown_event_is_refused_before_the_model_runs(self):
        response = self._post({"event_id": 99999, "message": "hola"})

        self.assertEqual(response.status_code, 400)

    def test_an_oversized_message_is_refused(self):
        response = self._post({"event_id": self.event.id, "message": "x" * 5000})

        self.assertEqual(response.status_code, 400)

    def test_a_broken_body_is_reported_as_such(self):
        response = self.client.post(self.url, data="{[", content_type="application/json")

        self.assertEqual(response.status_code, 400)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_missing_credentials_are_a_503_with_the_reason(self):
        # Overridden through settings, not the environment: that is where the key is read
        with self.settings(OPENAI_API_KEY=""):
            response = self._post({"event_id": self.event.id, "message": "hola"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENAI_API_KEY", response.json()["error"])

    def test_the_stream_is_event_stream_and_unbuffered(self):
        graph = FakeGraph(chunks=[("messages", (AIMessageChunk(content="hola"), {}))])
        with patch("ayudagente.radar.agent_views.build_agent", return_value=graph):
            response = self._post({"event_id": self.event.id, "message": "hola"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["X-Accel-Buffering"], "no")  # or a proxy buffers it all

        body = streamed_body(response)
        self.assertIn('"type": "start"', body)
        self.assertIn("hola", body)
        self.assertIn('"type": "done"', body)

    def test_a_thread_id_round_trips_so_follow_ups_continue(self):
        graph = FakeGraph()
        with patch("ayudagente.radar.agent_views.build_agent", return_value=graph):
            response = self._post(
                {"event_id": self.event.id, "message": "hola", "thread_id": "mine"}
            )
            body = streamed_body(response)

        self.assertIn('"thread_id": "mine"', body)
        self.assertEqual(graph.config["configurable"]["thread_id"], "mine")

    def test_the_browsers_position_reaches_the_agent(self):
        graph = FakeGraph()
        with patch("ayudagente.radar.agent_views.build_agent", return_value=graph) as build:
            response = self._post(
                {
                    "event_id": self.event.id,
                    "message": "¿qué falta cerca de mí?",
                    "location": {"lat": 5.6947, "lon": -76.6611},
                }
            )
            streamed_body(response)

        self.assertEqual(build.call_args.args[2], Coordinates(5.6947, -76.6611))

    def test_a_turn_without_a_position_still_runs(self):
        # The usual case: the permission is denied, or the fix has not arrived yet
        graph = FakeGraph()
        with patch("ayudagente.radar.agent_views.build_agent", return_value=graph) as build:
            response = self._post({"event_id": self.event.id, "message": "hola"})
            streamed_body(response)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(build.call_args.args[2])

    def test_an_impossible_position_is_refused_instead_of_dropped(self):
        # Dropped, it would answer "cerca de mí" from nowhere and never say so
        for location in (
            {"lat": 91, "lon": 0},
            {"lat": 0, "lon": 181},
            {"lat": "norte", "lon": 0},
            {"lat": 5.69},
            "5.69,-76.66",
        ):
            with self.subTest(location=location):
                response = self._post(
                    {"event_id": self.event.id, "message": "hola", "location": location}
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("location", response.json()["error"])

    def test_the_frontier_agent_has_its_own_endpoint(self):
        graph = FakeGraph()
        with patch("ayudagente.radar.agent_views.build_agent", return_value=graph) as build:
            self.client.post(
                reverse("radar:agent-frontier"),
                data={"event_id": self.event.id, "message": "¿dónde busco?"},
                content_type="application/json",
            )

        self.assertEqual(build.call_args.args[0], "frontier")
