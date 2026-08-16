"""
`draft_outreach` as an agent tool.

The last step before a real person is written to, and the one place where the model's
output reaches someone outside the system. Nothing is sent: the tool produces a draft and a
link, and a human clicks it. That is not a limitation to work around — it is the design.

Note:
    The body arrives in Spanish, written by the model. That is the documented exception to
    the English rule: the recipients are the people in the emergency.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent_tools.shared import ToolInputError, failure, get_requirement, require_same_event
from ayudagente.radar.choices import ContactKind, OutreachPurpose
from ayudagente.radar.models import ContactPoint, Match, Outreach
from ayudagente.radar.services import draft_outreach as draft_outreach_service
from ayudagente.radar.services.outreach import CHANNEL_BY_CONTACT_KIND

BODY_MIN_CHARS = 40
BODY_MAX_CHARS = 900

PURPOSE_VALUES = ", ".join(OutreachPurpose.values)


class DraftOutreachInput(BaseModel):
    """Arguments for `draft_outreach`."""

    event_id: int = Field(description="The emergency this conversation is bound to.")
    contact_point_id: int = Field(
        description="Channel to use, from `get_actor_contacts`. Its owner is the recipient."
    )
    purpose: str = Field(
        description=(
            f"Why this message exists: one of {PURPOSE_VALUES}. Use 'connect' to introduce "
            "a pairing, 'answer' to reply to someone who asked, 'verify' to check a need "
            "is still live, 'request_detail' to ask for a missing location or contact."
        )
    )
    body: str = Field(
        description=(
            "The message, in Spanish, addressed to the recipient. Say who you are, what "
            "you found and what you propose, in a few short sentences. It is read on a "
            "phone during an emergency, so no preamble and no formatting."
        )
    )
    subject: str = Field(
        default="", description="Subject line. Required for email, ignored elsewhere."
    )
    match_id: int | None = Field(
        default=None, description="The pairing being introduced, when purpose is 'connect'."
    )
    about_requirement_id: int | None = Field(
        default=None, description="The need or offer being verified or asked after."
    )


@tool("draft_outreach", args_schema=DraftOutreachInput)
def draft_outreach(
    event_id: int,
    contact_point_id: int,
    purpose: str,
    body: str,
    subject: str = "",
    match_id: int | None = None,
    about_requirement_id: int | None = None,
) -> dict:
    """
    Draft a message to one actor and build the link a human will click to send it.

    Nothing is sent. The result is a draft plus a `target_url` — a `wa.me` or `mailto:`
    link with the text already filled in, or a permalink for channels that cannot be
    prefilled. A person reviews it and sends it themselves.

    Drafting the same message twice returns the existing one rather than creating a second,
    so a retry cannot make "we already contacted ten people" untrue. That also means a
    rewritten body does not replace an existing draft.

    Refused when the actor asked not to be contacted, when the channel cannot carry a
    message, when the recipient is not a party to the match being introduced, or when any
    of them belongs to a different emergency than this conversation.

    Write the body in Spanish. It is read by someone in the middle of an emergency.
    """
    if purpose not in OutreachPurpose.values:
        return failure(f"unknown purpose {purpose!r}", f"use one of {PURPOSE_VALUES}")

    if not BODY_MIN_CHARS <= len(body.strip()) <= BODY_MAX_CHARS:
        return failure(
            f"body must be between {BODY_MIN_CHARS} and {BODY_MAX_CHARS} characters",
            "one short paragraph: who you are, what you found, what you propose",
        )

    try:
        contact_point = (
            ContactPoint.objects.select_related("actor").filter(id=contact_point_id).first()
        )
        if contact_point is None:
            raise ToolInputError(failure(f"contact point {contact_point_id} does not exist"))
        require_same_event(event_id, contact_point.actor, "actor")

        match = None
        if match_id is not None:
            match = (
                Match.objects.select_related("need__actor", "offer__actor", "via_transport__actor")
                .filter(id=match_id)
                .first()
            )
            if match is None:
                raise ToolInputError(failure(f"match {match_id} does not exist"))
            require_same_event(event_id, match.need, "need")

        requirement = None
        if about_requirement_id is not None:
            requirement = get_requirement(about_requirement_id, event_id)
    except ToolInputError as exc:
        return exc.payload

    # Asked before writing: `get_or_create` cannot say afterwards whether it created
    anchor = match or requirement
    key = Outreach.build_idempotency_key(
        contact_point.actor_id,
        purpose,
        CHANNEL_BY_CONTACT_KIND.get(ContactKind(contact_point.kind), ""),
        anchor.id if anchor is not None else None,
    )
    already_existed = Outreach.objects.filter(idempotency_key=key).exists()

    try:
        outreach = draft_outreach_service(
            match=match,
            contact_point=contact_point,
            body=body.strip(),
            subject=subject.strip(),
            purpose=purpose,
            about_requirement=requirement,
        )
    except ValueError as exc:
        return failure(str(exc))

    return {
        "outreach_id": outreach.id,
        "status": outreach.status,
        "channel": outreach.channel,
        "purpose": outreach.purpose,
        "target_actor_id": outreach.target_actor_id,
        "target_actor": outreach.target_actor.canonical_name,
        "target_url": outreach.target_url,
        "text_is_prefilled": outreach.text_is_prefilled,
        "already_existed": already_existed,
    }
