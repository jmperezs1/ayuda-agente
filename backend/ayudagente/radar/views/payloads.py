"""
The shapes the API returns.

One module, because the same entity shows up in several endpoints and has to look identical
in all of them: an actor is a node of the graph, the owner of a requirement and the target of
an outreach draft. Three hand-written copies of that dict is how a frontend ends up with three
parsers and a bug in one of them.

Note:
    Most entities have two shapes. The `_brief` form is what they look like embedded in
    something else — enough to draw a label and follow a link. The full form is what their own
    endpoint returns. Nesting full forms everywhere is what turns a list into a payload nobody
    can page through.
"""

from django.conf import settings

from ayudagente.radar.models import (
    Actor,
    ContactPoint,
    Event,
    Location,
    Match,
    Media,
    Observation,
    Outreach,
    Requirement,
    ResourceType,
)


def point(value) -> dict | None:
    """A geographic point as `{lat, lon}`, the order a mapping library expects."""
    if value is None:
        return None
    return {"lat": value.y, "lon": value.x}


def number(value) -> float | None:
    """A `Decimal` as a JSON number, because JSON has no decimal type."""
    return float(value) if value is not None else None


def location(value: Location | None) -> dict | None:
    """
    A resolved place, always carrying how exact it is.

    Note:
        `precision` travels with every point on purpose. A `country` point is the centroid of
        a nation, and a payload that hands over coordinates without saying so invites the
        frontend to draw it as a pin.
    """
    if value is None:
        return None
    return {
        "point": point(value.point),
        "precision": value.precision,
        "text": value.raw_text,
        "admin_unit": value.admin_unit.name if value.admin_unit else None,
    }


def event_brief(event: Event) -> dict:
    """An event as a list row and as the header of everything scoped to it."""
    return {
        "id": event.id,
        "name": event.name,
        "hazard": event.hazard,
        "status": event.status,
        "occurred_at": event.occurred_at.isoformat(),
        "magnitude": event.magnitude,
        "epicenter": point(event.epicenter),
    }


def event_detail(event: Event, summary: dict) -> dict:
    """An event with the counts a dashboard header shows."""
    return {
        **event_brief(event),
        "depth_km": event.depth_km,
        "country_code": event.country_code,
        "languages": event.languages,
        "detection_source": event.detection_source,
        "summary": summary,
    }


def actor_brief(actor: Actor) -> dict:
    """An actor embedded in a requirement, a match or a draft."""
    return {
        "id": actor.id,
        "name": actor.canonical_name,
        "kind": actor.kind,
        "is_organization": actor.is_organization,
        "credibility": actor.credibility,
        "verified": actor.verified,
    }


def actor_detail(actor: Actor, *, contacts: list, requirements: list) -> dict:
    """
    Everything about one actor, including how to reach it.

    Note:
        Contact details are returned in full. The whole system exists so a human can open a
        link and write to somebody, and a masked phone number would make the product a
        read-only map. What protects it is the API key, not redaction.
    """
    return {
        **actor_brief(actor),
        "alternate_names": actor.alternate_names,
        "credibility_source": actor.credibility_source,
        "max_followers": actor.max_followers,
        "first_seen_at": actor.first_seen_at.isoformat(),
        "last_seen_at": actor.last_seen_at.isoformat(),
        "location": location(actor.location),
        "contacts": [contact(item) for item in contacts],
        "requirements": [requirement_brief(item) for item in requirements],
    }


def contact(value: ContactPoint) -> dict:
    """
    One way to reach an actor, with everything a dashboard needs to rank it.

    Note:
        `times_seen` is the trust signal worth showing. A phone number written across five
        posts is almost certainly real; one that appeared once may be an extraction error,
        and a human deciding whether to call deserves to know which they are looking at.
    """
    return {
        "id": value.id,
        "kind": value.kind,
        "value": value.value,
        "raw_value": value.raw_value,
        "platform": value.platform,
        "payment_network": value.payment_network,
        "times_seen": value.times_seen,
        "confidence": value.confidence,
        "verified": value.verified,
        "reachable": value.reachable,
        "can_carry_a_message": value.can_carry_a_message,
        "preference_rank": value.preference_rank(),
        "attempts": value.attempts,
    }


def requirement_brief(requirement: Requirement) -> dict:
    """A need or an offer, as it hangs off a node of the graph."""
    return {
        "id": requirement.id,
        "direction": requirement.direction,
        "resource": requirement.resource.name,
        "resource_key": requirement.resource.key,
        "free_text": requirement.free_text,
        "urgency": requirement.urgency,
        "status": requirement.status,
        "quantity": number(requirement.quantity),
        "covered_quantity": number(requirement.covered_quantity),
        "outstanding": number(requirement.outstanding_quantity),
        "unit": requirement.unit,
        "destination": point(requirement.destination.point) if requirement.destination else None,
        "confidence": requirement.confidence,
    }


def requirement_row(requirement: Requirement) -> dict:
    """A requirement as a list row: enough to draw it on a map and label the pin."""
    return {
        **requirement_brief(requirement),
        "actor": actor_brief(requirement.actor),
        "location": location(requirement.location),
        "is_saturated": requirement.is_saturated,
        "window_start": timestamp(requirement.window_start),
        "window_end": timestamp(requirement.window_end),
        "last_seen_at": requirement.last_seen_at.isoformat(),
    }


def requirement_detail(requirement: Requirement, *, evidence: list, matches: list) -> dict:
    """
    A requirement with the posts it came from and the matches proposed over it.

    Note:
        `evidence` is a list because one post can legitimately produce several requirements
        and one requirement can be corroborated by several posts. A UI that assumes one post
        per requirement will show the wrong screenshot as often as the right one.
    """
    return {
        **requirement_row(requirement),
        "destination_location": location(requirement.destination),
        "created_at": requirement.created_at.isoformat(),
        "evidence": [observation_brief(item) for item in evidence],
        "matches": [match_row(item) for item in matches],
    }


def observation_brief(observation: Observation) -> dict:
    """A post as evidence: what it said, who said it, and where to go read it."""
    return {
        "id": observation.id,
        "platform": observation.platform,
        "permalink": observation.permalink,
        "posted_at": observation.posted_at.isoformat(),
        "text": observation.text,
        "transcript": observation.transcript,
        "language": observation.language,
        "author": {
            "handle": observation.author_handle,
            "name": observation.author_name,
            "avatar_url": observation.author_avatar_url,
            "followers": observation.author_followers,
            "verified": observation.author_verified,
        },
        "metrics": observation.metrics,
        "is_reply": observation.is_reply,
        "media": [media(item) for item in observation.media.all()],  # type: ignore[missing-attribute]
    }


def media(value: Media) -> dict:
    """
    One image or frame, pointing at our own copy rather than the platform's.

    Note:
        `url` is our stored copy and `source_url` the platform's. Prefer ours: platform media
        URLs are signed and expire within hours, so a frontend that renders `source_url` shows
        broken images on anything more than a day old.
    """
    return {
        "id": value.id,
        "kind": value.kind,
        "url": _media_url(value.blob_path),
        "source_url": value.source_url,
        "alt_text": value.platform_alt_text,
        "position": value.position,
    }


def match_row(value: Match) -> dict:
    """A proposed pairing, with both sides named so a row reads without another request."""
    return {
        "id": value.id,
        "status": value.status,
        "score": value.score,
        "distance_km": value.distance_km,
        "committed_quantity": number(value.committed_quantity),
        "rationale": value.rationale,
        "created_at": value.created_at.isoformat(),
        "need": _match_side(value.need),
        "offer": _match_side(value.offer),
        "via_transport": _match_side(value.via_transport) if value.via_transport else None,
    }


def outreach_row(value: Outreach) -> dict:
    """
    A drafted message and the link that sends it.

    Note:
        `target_url` is the entire dispatch mechanism. Nothing is ever sent by the system, so
        this is a button a person clicks — and `text_is_prefilled` says whether the body
        travels in the link or the dashboard has to offer a copy button beside it.
    """
    return {
        "id": value.id,
        "purpose": value.purpose,
        "channel": value.channel,
        "status": value.status,
        "subject": value.subject,
        "body": value.body,
        "target_url": value.target_url,
        "text_is_prefilled": value.text_is_prefilled,
        "drafted_by": value.drafted_by,
        "created_at": value.created_at.isoformat(),
        "dispatched_at": timestamp(value.dispatched_at),
        "target_actor": actor_brief(value.target_actor),
        "contact": contact(value.contact_point),
        "match_id": value.match_id,
        "requirement_id": value.about_requirement_id,
        "in_reply_to_id": value.in_reply_to_id,
    }


def resource_type(value: ResourceType) -> dict:
    """One entry of the resource catalog, for a filter menu."""
    return {
        "key": value.key,
        "name": value.name,
        "parent": value.parent.key if value.parent else None,
        "default_unit": value.default_unit,
        "perishable": value.perishable,
    }


def _match_side(requirement: Requirement) -> dict:
    """One end of a match: which actor, which resource, and where."""
    return {
        "requirement_id": requirement.id,
        "actor": actor_brief(requirement.actor),
        "resource": requirement.resource.name,
        "resource_key": requirement.resource.key,
        "location": location(requirement.location),
    }


def _media_url(blob_path: str) -> str | None:
    """Our copy's URL, or None when the file was never downloaded."""
    if not blob_path:
        return None
    return f"{settings.MEDIA_URL}{blob_path}"


def timestamp(value) -> str | None:
    """An optional datetime as ISO 8601."""
    return value.isoformat() if value else None
