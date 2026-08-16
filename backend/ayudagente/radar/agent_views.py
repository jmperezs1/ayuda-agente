"""
The agent endpoints: one streaming conversation per toolset.

Kept apart from `views.py` because the two answer different kinds of question. Those are
cache-friendly snapshot reads; this one holds a connection open for half a minute while a
model works, and the failure modes have nothing in common.

Note:
    Validation happens before the stream opens. Once the first byte is sent the status code
    is fixed, so a bad event id has to be a 400 and not an `error` event nobody checks for.
"""

import json
import uuid

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agent_tools.agents import Coordinates, LLMNotConfigured, build_agent, stream_agent
from agent_tools.registry import TOOLSETS
from ayudagente.radar.models import Event

MAX_MESSAGE_CHARS = 2000


class BadRequest(Exception):
    """A request that is wrong before any model runs. Never escapes the view."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status

    def as_response(self) -> JsonResponse:
        """The JSON body the client gets."""
        return JsonResponse({"error": self.message}, status=self.status)


def parse_body(request) -> dict:
    """
    Read the JSON body of a request.

    Raises:
        BadRequest: On malformed JSON or a non-object body.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError as exc:
        raise BadRequest("body must be JSON") from exc
    if not isinstance(payload, dict):
        raise BadRequest("body must be a JSON object")
    return payload


def parse_location(payload: dict) -> Coordinates | None:
    """
    Read the optional position of whoever is asking.

    Returns:
        Coordinates | None: None when the browser sent nothing. That is the normal case —
            the permission is denied or still pending — and never an error.

    Raises:
        BadRequest: On a malformed or impossible point. Refused rather than dropped: a
            location that is quietly discarded answers "¿qué hay cerca de mí?" from
            nowhere in particular, and nothing in the reply says the distances are made up.
    """
    raw = payload.get("location")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BadRequest("location must be an object with lat and lon")

    try:
        lat, lon = float(raw["lat"]), float(raw["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequest("location needs a numeric lat and lon") from exc

    # NaN fails both comparisons, which is the answer wanted for it too
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise BadRequest(f"location {lat}, {lon} is not a point on Earth")
    return Coordinates(lat, lon)


def agent_stream(request, toolset: str):
    """
    Run one conversational turn against an agent and stream it back.

    Expects `{"event_id": int, "message": str, "thread_id": str?, "location": {lat, lon}?}`
    and responds with `text/event-stream`. Omit `thread_id` to start a conversation; the
    first event carries the one to reuse for follow-ups.

    Returns:
        StreamingHttpResponse: `start`, then `tool_start` / `tool_end` / `token` events, and
            a final `done` or `error`.

    Note:
        `location` is read per turn rather than stored on the thread. The coordinator is
        the one thing in this conversation that moves.
    """
    if toolset not in TOOLSETS:
        return JsonResponse(
            {"error": f"unknown agent {toolset!r}", "available": sorted(TOOLSETS)}, status=404
        )

    try:
        payload = parse_body(request)

        message = str(payload.get("message", "")).strip()
        if not message:
            raise BadRequest("message is required")
        if len(message) > MAX_MESSAGE_CHARS:
            raise BadRequest(f"message exceeds {MAX_MESSAGE_CHARS} characters")

        event = Event.objects.filter(id=payload.get("event_id")).first()
        if event is None:
            raise BadRequest("event_id does not exist")

        location = parse_location(payload)

        try:
            graph = build_agent(toolset, event, location)
        except LLMNotConfigured as exc:
            raise BadRequest(f"agent is not configured: {exc}", status=503) from exc
    except BadRequest as exc:
        return exc.as_response()

    thread_id = str(payload.get("thread_id") or uuid.uuid4())

    response = StreamingHttpResponse(
        stream_agent(graph, message, thread_id),
        content_type="text/event-stream",
    )
    # Without these a proxy buffers the whole stream and the point of streaming is lost
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@csrf_exempt
@require_POST
def coordination_agent(request):
    """Answer a coordinator's question over needs, offers, matches and outreach."""
    return agent_stream(request, "coordination")


@csrf_exempt
@require_POST
def frontier_agent(request):
    """Decide where to harvest next. Reaches no observation through any tool it holds."""
    return agent_stream(request, "frontier")
