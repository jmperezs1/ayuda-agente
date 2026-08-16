"""
Drafting outreach: the only door from proposals to a real person.

Nothing here sends anything. Every message resolves to a link a human clicks — `wa.me` and
`mailto:` carry the text, a post permalink does not — so the whole module's job is to write
the body, pick the channel and build that link exactly once per finding.
"""

from ayudagente.radar.choices import (
    ContactKind,
    OutreachChannel,
    OutreachPurpose,
    Platform,
    UnreachableReason,
)
from ayudagente.radar.models import ContactPoint, Match, Observation, Outreach, Requirement

CHANNEL_BY_CONTACT_KIND = {
    ContactKind.EMAIL: OutreachChannel.EMAIL,
    ContactKind.WHATSAPP: OutreachChannel.WHATSAPP,
    ContactKind.PHONE: OutreachChannel.PHONE_CALL,
    ContactKind.HANDLE: OutreachChannel.DIRECT_MESSAGE,
}

# Reasons a channel is closed for good. A bounce may be transient; these are not.
REFUSED_UNREACHABLE_REASONS = frozenset(
    {
        UnreachableReason.OPTED_OUT,
        UnreachableReason.INVALID,
    }
)


def match_participants(match: Match) -> set[int]:
    """
    Actor ids that are party to a match: the one needing, the one offering, the carrier.

    Returns:
        set[int]: Everyone it is legitimate to write to about this match. Proposing a
            message about someone else's pairing to an unrelated actor is a privacy leak,
            not a routing mistake.
    """
    actors = {match.need.actor_id, match.offer.actor_id}
    if match.via_transport is not None:
        actors.add(match.via_transport.actor_id)
    return actors


PROFILE_URL_BY_PLATFORM = {
    Platform.X: "https://x.com/{handle}",
    Platform.INSTAGRAM: "https://instagram.com/{handle}",
    Platform.FACEBOOK: "https://facebook.com/{handle}",
    Platform.TIKTOK: "https://tiktok.com/@{handle}",
}


def contact_link(contact_point: ContactPoint) -> str:
    """
    The clickable form of a contact detail, whatever kind it is.

    Args:
        contact_point (ContactPoint): The detail to turn into something tappable.

    Returns:
        str: A link a phone will act on, or empty when the kind has none — a payment account
            and a street address are read, not opened.

    Note:
        Separate from `build_target_url`, which prefills a drafted message. This one carries no
        text: it is what someone asking "how do I reach them" needs, and it exists for the
        kinds that a drafted message cannot use — a phone to dial, a profile to look at.
    """
    value = contact_point.value.strip()
    if not value:
        return ""

    if contact_point.kind == ContactKind.EMAIL:
        return f"mailto:{value}"
    if contact_point.kind == ContactKind.WHATSAPP:
        return f"https://wa.me/{value.lstrip('+')}"
    if contact_point.kind == ContactKind.PHONE:
        return f"tel:{value}"
    if contact_point.kind in (ContactKind.WEBSITE, ContactKind.FORM):
        return value if value.startswith("http") else f"https://{value}"
    if contact_point.kind == ContactKind.HANDLE and contact_point.platform:
        template = PROFILE_URL_BY_PLATFORM.get(Platform(contact_point.platform), "")
        return template.format(handle=value.lstrip("@")) if template else ""
    return ""


def build_target_url(
    contact_point: ContactPoint,
    body: str,
    subject: str = "",
    in_reply_to: Observation | None = None,
) -> str:
    """
    Build the link the human clicks, prefilled with the text wherever the channel allows it.

    Args:
        contact_point (ContactPoint): Where the message is going.
        body (str): Text the model wrote.
        subject (str): Subject line, used by email only.
        in_reply_to (Observation | None): Post or comment being answered, when there is one.

    Returns:
        str: A deep link needing no API, credentials or app review. Falls back to the
            observation's permalink for channels that cannot be prefilled.

    Note:
        A phone becomes `tel:`, which carries no text but opens the dialler. Someone about to
        drive across a city to a collection point wants to call it first, and returning no
        link at all told them there was no way to reach a number we were holding.
    """
    if contact_point.kind == ContactKind.EMAIL:
        return Outreach.build_mailto_url(contact_point.value, subject, body)
    if contact_point.kind == ContactKind.WHATSAPP:
        return Outreach.build_whatsapp_url(contact_point.value, body)
    if contact_point.kind == ContactKind.PHONE:
        return f"tel:{contact_point.value}"
    if contact_point.kind == ContactKind.WEBSITE:
        return contact_point.value
    return in_reply_to.permalink if in_reply_to else ""


def draft_outreach(
    match: Match | None,
    contact_point: ContactPoint,
    body: str,
    subject: str = "",
    drafted_by: str = "agent",
    purpose: str = OutreachPurpose.CONNECT,
    in_reply_to: Observation | None = None,
    about_requirement: Requirement | None = None,
) -> Outreach:
    """
    Create, idempotently, a draft message to the contact point's actor.

    Args:
        match (Match | None): The pairing being introduced, when the purpose is to connect.
        contact_point (ContactPoint): Channel to reach the actor through.
        body (str): Message text.
        subject (str): Subject line, email only.
        drafted_by (str): Which model deployment wrote it.
        purpose (str): An `OutreachPurpose` value; not every message pairs two parties.
        in_reply_to (Observation | None): Post or comment being answered.
        about_requirement (Requirement | None): Need or offer being verified or asked after.

    Returns:
        Outreach: The existing row on a retry, so "ten people already contacted" stays true
            rather than approximately true.

    Raises:
        ValueError: If the contact point cannot carry a message (a bank account is a way to
            give someone money, not a way to talk to them), if the actor asked not to be
            contacted, or if the recipient is not a party to the match being introduced.
    """
    channel = CHANNEL_BY_CONTACT_KIND.get(ContactKind(contact_point.kind))
    if channel is None or not contact_point.can_carry_a_message:
        raise ValueError(f"contact kind {contact_point.kind!r} is not a messaging channel")

    if contact_point.unreachable_reason in REFUSED_UNREACHABLE_REASONS:
        raise ValueError(
            f"contact point {contact_point.id} is closed ({contact_point.unreachable_reason})"
        )

    if match is not None and contact_point.actor_id not in match_participants(match):
        raise ValueError(f"actor {contact_point.actor_id} is not a party to match {match.id}")

    anchor = match or in_reply_to or about_requirement
    key = Outreach.build_idempotency_key(
        contact_point.actor_id, purpose, channel, anchor.id if anchor else None
    )
    outreach, _created = Outreach.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "match": match,
            "in_reply_to": in_reply_to,
            "about_requirement": about_requirement,
            "target_actor_id": contact_point.actor_id,
            "contact_point": contact_point,
            "purpose": purpose,
            "channel": channel,
            "subject": subject,
            "body": body,
            "target_url": build_target_url(contact_point, body, subject, in_reply_to),
            "drafted_by": drafted_by,
        },
    )
    return outreach
