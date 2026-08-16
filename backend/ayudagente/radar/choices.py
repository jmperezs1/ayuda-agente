"""
Enumerations shared across every model.

They live in one module because several cross layer boundaries: `Platform` is used by the
harvest layer, the observation layer and the contact layer, and a divergence between those
copies would break queries silently.

Nothing here is country-specific. Administrative levels follow the GeoNames convention so a
single catalog covers every country, and payment rails are one generic kind with the network
stored as data — Nequi in Colombia, Pix in Brazil, M-Pesa in Kenya.
"""

from django.db import models


class Platform(models.TextChoices):
    X = "x", "X (Twitter)"
    INSTAGRAM = "instagram", "Instagram"
    FACEBOOK = "facebook", "Facebook"
    TIKTOK = "tiktok", "TikTok"


class HazardKind(models.TextChoices):
    EARTHQUAKE = "earthquake", "Earthquake"
    FLOOD = "flood", "Flood"
    LANDSLIDE = "landslide", "Landslide"
    CYCLONE = "cyclone", "Cyclone"
    WILDFIRE = "wildfire", "Wildfire"
    WINDSTORM = "windstorm", "Windstorm"
    OTHER = "other", "Other"


class EventStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class AdminLevel(models.TextChoices):
    """GeoNames administrative levels, so one catalog serves every country."""

    COUNTRY = "country", "Country"
    ADMIN_1 = "admin_1", "First level (state, department, province)"
    ADMIN_2 = "admin_2", "Second level (municipality, county, district)"
    ADMIN_3 = "admin_3", "Third level (settlement, locality, ward)"


class DecisionSource(models.TextChoices):
    AGENT = "agent", "Frontier agent"
    RULE = "rule", "Scheduling rule"
    MANUAL = "manual", "Manual"


class JobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    EMPTY = "empty", "No results"
    FAILED = "failed", "Failed"
    ACTOR_DOWN = "actor_down", "Actor down"


class HarvestTarget(models.TextChoices):
    """What a job points at. Searching a place is cheap; pulling a profile is the deep pass."""

    SEARCH = "search", "Keyword search over a place"
    PROFILE = "profile", "One account's timeline"
    THREAD = "thread", "Replies under one post"
    COMMENTS = "comments", "Comments on one post"


class MediaKind(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    FRAME = "frame", "Video frame"
    COVER = "cover", "Video cover"


class ExtractionClass(models.TextChoices):
    NEED = "need", "Need"
    OFFER = "offer", "Offer"
    BOTH = "both", "Need and offer"
    DISCARD = "discard", "Discard"


class LocationPrecision(models.TextChoices):
    """Ordered coarse to fine. Matching compares positions, so order is load-bearing."""

    COUNTRY = "country", "Country"
    ADMIN_1 = "admin_1", "First level"
    ADMIN_2 = "admin_2", "Second level"
    ADMIN_3 = "admin_3", "Third level"
    NEIGHBORHOOD = "neighborhood", "Neighborhood or rural district"
    STREET_ADDRESS = "street_address", "Street address"
    EXACT_POINT = "exact_point", "Exact point"


def precisions_at_least(minimum: str) -> list[str]:
    """
    Precision values as fine as a minimum, or finer.

    The declaration order of `LocationPrecision` is the scale, coarsest first. This is the
    single place that knows it: `Location.is_at_least` compares one row against a floor and
    the services filter a queryset against the same floor, and a second copy of the
    ordering would let the two drift.

    Args:
        minimum (str): A `LocationPrecision` value to use as the floor.

    Returns:
        list[str]: The floor itself and everything finer.

    Raises:
        ValueError: When the value is not a `LocationPrecision`.
    """
    scale = list(LocationPrecision.values)
    if minimum not in scale:
        raise ValueError(f"unknown location precision {minimum!r}; expected one of {scale}")
    return scale[scale.index(minimum) :]


class GeocodeSource(models.TextChoices):
    GOOGLE = "google", "Google Geocoding"
    GAZETTEER = "gazetteer", "Administrative gazetteer (GeoNames)"
    PLATFORM = "platform", "Platform metadata"
    MANUAL = "manual", "Manual"


class ActorKind(models.TextChoices):
    PERSON = "person", "Person"
    COLLECTION_CENTER = "collection_center", "Collection center"
    NONPROFIT = "nonprofit", "Nonprofit or NGO"
    COMPANY = "company", "Company"
    PUBLIC_ENTITY = "public_entity", "Public entity"
    MEDIA_OUTLET = "media_outlet", "Media outlet"
    COMMUNITY = "community", "Community or local board"
    CHURCH = "church", "Church"
    SCHOOL = "school", "School or university"
    VOLUNTEER_GROUP = "volunteer_group", "Volunteer group"


class CredibilitySource(models.TextChoices):
    FOLLOWERS = "followers", "Follower count"
    VERIFIED = "verified", "Verified account"
    ENGAGEMENT = "engagement", "Engagement ratio"
    OFFICIAL_ENTITY = "official_entity", "Known official entity"
    NONE = "none", "No signal available"


class MentionRole(models.TextChoices):
    AUTHOR = "author", "Author of the post"
    MENTIONED = "mentioned", "Mentioned"
    SUBJECT = "subject", "Subject of the content"


class ResolutionMethod(models.TextChoices):
    HANDLE = "handle", "Same platform handle"
    PHONE = "phone", "Same phone number"
    TRIGRAM = "trigram", "Trigram name similarity"
    EMBEDDING = "embedding", "Embedding similarity"
    LLM = "llm", "Adjudicated by LLM"
    MANUAL = "manual", "Manual"


class ContactKind(models.TextChoices):
    HANDLE = "handle", "Platform handle"
    PHONE = "phone", "Phone"
    WHATSAPP = "whatsapp", "WhatsApp"
    EMAIL = "email", "Email"
    WEBSITE = "website", "Website"
    FORM = "form", "Web form"
    PAYMENT = "payment", "Payment account"
    STREET_ADDRESS = "street_address", "Street address"


class UnreachableReason(models.TextChoices):
    BOUNCED = "bounced", "Bounced"
    INVALID = "invalid", "Invalid format"
    OPTED_OUT = "opted_out", "Asked not to be contacted"
    SATURATED = "saturated", "Already contacted too many times"


class Direction(models.TextChoices):
    NEEDS = "needs", "Needs"
    OFFERS = "offers", "Offers"


class Urgency(models.TextChoices):
    CRITICAL = "critical", "Critical"
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"


class RequirementStatus(models.TextChoices):
    OPEN = "open", "Open"
    PARTIAL = "partial", "Partially covered"
    COVERED = "covered", "Covered"
    EXPIRED = "expired", "Expired"
    UNVERIFIED = "unverified", "Unverified"
    DISCARDED = "discarded", "Discarded"


class MatchStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    CONTACTED = "contacted", "Contacted"
    CONFIRMED = "confirmed", "Confirmed"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    DISCARDED = "discarded", "Discarded"


class OutreachPurpose(models.TextChoices):
    """Why a message exists. Not every message links two parties."""

    CONNECT = "connect", "Introduce a need to an offer"
    ANSWER = "answer", "Answer someone who asked where to go or how to help"
    VERIFY = "verify", "Confirm a need or offer is still live"
    REQUEST_DETAIL = "request_detail", "Ask for the location or contact the post omitted"


class OutreachChannel(models.TextChoices):
    EMAIL = "email", "Email"
    WHATSAPP = "whatsapp", "WhatsApp"
    COMMENT_REPLY = "comment_reply", "Reply to a post or comment"
    DIRECT_MESSAGE = "direct_message", "Direct message"
    PHONE_CALL = "phone_call", "Phone call"


class OutreachStatus(models.TextChoices):
    DRAFT = "draft", "Drafted, waiting for a human"
    DISPATCHED = "dispatched", "A human opened the link and sent it"
    ANSWERED = "answered", "The recipient replied"
    DISMISSED = "dismissed", "A human decided not to send it"
    FAILED = "failed", "Could not be delivered"


class Zone(models.TextChoices):
    """Decides which query axis runs, so it is structural rather than descriptive."""

    IMPACT = "impact", "Impact zone — search for demand"
    SUPPORT = "support", "Support zone — search for supply"


class NodeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    EXHAUSTED = "exhausted", "Exhausted — produced nothing useful"
    PAUSED = "paused", "Paused"
