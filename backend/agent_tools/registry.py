"""
Which tools each agent gets, and the event every one of them is fixed to.

Grouping is not organization, it is enforcement. The frontier agent must be structurally
incapable of reading a post, and that is guaranteed by what is absent from a list rather
than by a sentence in a prompt asking it not to.

The event is bound the same way. `event_id` is filled in from the request and removed from
the schema the model sees, so answering about the emergency the coordinator has open does
not depend on the model copying a number out of its prompt into every call. An agent built
for one event has no argument left with which to name another — the same guarantee, applied
to a second axis.

Note:
    `run_matching_pass` is deliberately in no toolset. It rewrites every proposal for an
    event, so an agent calling it mid-conversation would invalidate the pairings it had
    just reasoned about. It runs on a schedule; the agent reads its output.
"""

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, create_model

from agent_tools.check_coverage import check_coverage
from agent_tools.create_harvest_job import create_harvest_job
from agent_tools.draft_outreach import draft_outreach
from agent_tools.find_gaps import find_gaps
from agent_tools.get_actor_contacts import get_actor_contacts
from agent_tools.get_balance import get_balance
from agent_tools.get_frontier import get_frontier
from agent_tools.match_resource import match_resource
from agent_tools.propose_match import propose_match
from agent_tools.road_distance import road_distance

# The argument every tool declares and no model ever fills in
EVENT_ARG = "event_id"

# Reads `FrontierNode`, writes `HarvestJob`. Reaches no Observation through either.
FRONTIER_TOOLS = [
    get_frontier,
    create_harvest_job,
]

# Ordered the way an operator works: connect first, then check, then act.
COORDINATION_TOOLS = [
    match_resource,
    check_coverage,
    find_gaps,
    get_balance,
    get_actor_contacts,
    road_distance,
    propose_match,
    draft_outreach,
]

TOOLSETS = {
    "frontier": FRONTIER_TOOLS,
    "coordination": COORDINATION_TOOLS,
}

ALL_TOOLS = FRONTIER_TOOLS + COORDINATION_TOOLS


def bind_event(base: BaseTool, event_id: int) -> BaseTool:
    """
    Fix one tool to one event, and hide the argument from the model.

    Args:
        base (BaseTool): A tool declaring `event_id`.
        event_id (int): The event every call to it is answered from.

    Returns:
        BaseTool: The same tool with the same name and description, minus that argument.

    Raises:
        KeyError: When the tool does not declare `event_id`. Refused rather than passed
            through unbound, because a tool that silently escapes the scoping is exactly
            the bug this exists to make impossible.
    """
    schema = base.args_schema
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise KeyError(f"{base.name} has no argument model to bind an event to")
    if EVENT_ARG not in schema.model_fields:
        raise KeyError(f"{base.name} does not declare {EVENT_ARG} and cannot be scoped")

    fields = {
        name: (field.annotation, field)
        for name, field in schema.model_fields.items()
        if name != EVENT_ARG
    }
    scoped = create_model(f"{schema.__name__}Scoped", **fields)  # type: ignore[call-overload]

    def run(**kwargs):
        return base.func(**kwargs, event_id=event_id)  # type: ignore[attr-defined]

    return StructuredTool.from_function(
        func=run,
        name=base.name,
        description=base.description,
        args_schema=scoped,
    )


def get_toolset(name: str, event_id: int) -> list[BaseTool]:
    """
    Look up a toolset and bind it to one emergency.

    Args:
        name (str): A key of `TOOLSETS`.
        event_id (int): The event this conversation is about.

    Returns:
        list[BaseTool]: The tools, ready to hand to `create_deep_agent`.

    Raises:
        KeyError: On an unknown name, rather than returning an empty list. An agent built
            with no tools fails much later and much less obviously.

    Note:
        Binding is per call and the tools are rebuilt with the agent, which is what keeps a
        second emergency from inheriting the first one's scope. Building ten small pydantic
        models costs nothing next to the model call that follows.
    """
    if name not in TOOLSETS:
        raise KeyError(f"unknown toolset {name!r}; expected one of {sorted(TOOLSETS)}")
    return [bind_event(tool, event_id) for tool in TOOLSETS[name]]
