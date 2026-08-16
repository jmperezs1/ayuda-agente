"""
The read API: plain Django views returning hand-shaped JSON.

Split by the entity each endpoint is about, the way `models/` is split by layer. No DRF —
these are cache-friendly reads whose payloads are designed for one frontend, and a serializer
layer would add indirection without removing a line of the shaping that actually matters.

One endpoint writes: , which records the click that sends a message. It is
the only thing a human does, and the only state change this API owns.

See:
    `agent_views.py` for the streaming conversations, which have nothing in common with these.
    `docs/api.md` for the contract this file group implements.
"""

from ayudagente.radar.views.actors import actor_detail, actor_list
from ayudagente.radar.views.catalog import resource_type_list
from ayudagente.radar.views.dispatch import dispatch_outreach
from ayudagente.radar.views.events import event_detail, event_graph, event_list
from ayudagente.radar.views.observations import observation_detail, observation_list
from ayudagente.radar.views.operations import job_list, loop_status
from ayudagente.radar.views.proposals import match_list, outreach_list
from ayudagente.radar.views.requirements import requirement_detail, requirement_list

__all__ = [
    "actor_detail",
    "actor_list",
    "dispatch_outreach",
    "event_detail",
    "event_graph",
    "event_list",
    "job_list",
    "loop_status",
    "match_list",
    "observation_detail",
    "observation_list",
    "outreach_list",
    "requirement_detail",
    "requirement_list",
    "resource_type_list",
]
