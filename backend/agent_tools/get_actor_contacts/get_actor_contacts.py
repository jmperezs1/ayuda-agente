"""
`get_actor_contacts` as an agent tool.

The one rule that shapes it: the model never sees the phone number or the email address.
It gets the channel, how confident we are and how to refer to it, and that is enough to
decide which channel to use. `draft_outreach` takes the id and reads the value itself.

Note:
    Keeping the value out of the context window is the cheapest privacy control available.
    A phone number that never enters a prompt cannot be echoed into a message body, logged
    by a provider, or recalled into an unrelated answer.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import ToolInputError, failure, require_same_event
from ayudagente.radar.services import get_actor, get_contact_points
from ayudagente.radar.services.outreach import contact_link


class GetActorContactsInput(BaseModel):
    """Arguments for `get_actor_contacts`."""

    event_id: int = Field(description="The emergency this conversation is bound to.")
    actor_id: int = Field(
        description="Actor to look up, as returned in `actor_id` by match_resource."
    )
    include_unusable: bool = Field(
        default=False,
        description=(
            "Also list details that cannot carry a message, such as payment accounts. "
            "Useful to answer 'how can I donate to them', not to write to them."
        ),
    )


def serialize(contact) -> dict:
    """
    Describe a channel, including how to use it.

    Returns:
        dict: `contact_point_id` is what other tools accept; `value` is the detail itself.

    Note:
        The value used to be withheld. That was right when the reader was assumed to be
        operating the system — it never needed the digits, because the system built the link.
        The reader is a member of the public, and somebody about to drive across a city to a
        collection point needs to call it first. Withholding the number made the answer
        useless while sounding careful.

        What protects it is the API key and the fact that the person published it themselves,
        in a post asking for help.
    """
    row = {
        "contact_point_id": contact.id,
        "value": contact.value,
        "link": contact_link(contact),
        "kind": contact.kind,
        "reachable": contact.reachable,
        "verified": contact.verified,
        "times_seen": contact.times_seen,
        "confidence": round(contact.confidence, 2),
        "can_carry_a_message": contact.can_carry_a_message,
        "preference_rank": contact.preference_rank(),
    }
    if contact.platform:
        row["platform"] = contact.platform
    if contact.payment_network:
        row["payment_network"] = contact.payment_network
    if contact.unreachable_reason:
        row["unreachable_reason"] = contact.unreachable_reason
    return row


@tool("get_actor_contacts", args_schema=GetActorContactsInput)
def get_actor_contacts(event_id: int, actor_id: int, include_unusable: bool = False) -> dict:
    """
    List the ways to reach an actor, best channel first.

    Call this before drafting a message, to pick a channel and to learn whether one exists
    at all — plenty of posts name a place without naming any way to contact it.

    Values are returned: read them out when someone asks how to reach a place, and say when
    `times_seen` is 1, because a detail seen once is the likeliest to be wrong. Use
    `contact_point_id` with `draft_outreach`, which
    reads the address itself. `preference_rank` orders least intrusive first; the first row
    is the one to use unless you have a reason not to.

    An actor merged into another resolves to the surviving one, so the contacts and the
    outreach history stay together.
    """
    actor = get_actor(actor_id)
    if actor is None:
        return failure(f"actor {actor_id} does not exist", contacts=[])

    # Checked after the merge is resolved: what matters is where the surviving row lives
    try:
        require_same_event(event_id, actor, "actor")
    except ToolInputError as exc:
        return {**exc.payload, "contacts": []}

    contacts = get_contact_points(actor, usable_only=not include_unusable)

    return {
        "actor_id": actor.id,
        "actor": actor.canonical_name,
        "actor_kind": actor.kind,
        "is_organization": actor.is_organization,
        "count": len(contacts),
        "contacts": [serialize(c) for c in contacts],
    }
