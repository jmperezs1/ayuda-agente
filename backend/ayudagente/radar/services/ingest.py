"""
Turning what the model read into what we believe about the world.

This is where interpretation becomes state: each extracted item gets its actor resolved, its
place geocoded, its contacts recorded, and lands as a `Requirement` backed by the observation
it came from. It is the last step that can still refuse — once a requirement exists, matching
will act on it.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ayudagente.radar.choices import (
    ContactKind,
    Direction,
    ExtractionClass,
    RequirementStatus,
    Urgency,
)
from ayudagente.radar.models import (
    ContactPoint,
    Extraction,
    Location,
    Observation,
    Requirement,
    ResourceType,
)
from ayudagente.radar.schemas import ExtractedContact, ExtractedItem, ExtractionResult
from ayudagente.radar.services import verification
from ayudagente.radar.services.geocoding import Geocoder
from ayudagente.radar.services.identity import IdentityResolver
from ayudagente.radar.services.resources import resolve_resource
from ayudagente.radar.services.text import normalize

logger = logging.getLogger(__name__)

# Nothing below this is worth putting in front of a person during an emergency.
MIN_ITEM_CONFIDENCE = 0.4

# What `Requirement.quantity` can hold: numeric(12, 2)
MAX_QUANTITY = Decimal("9999999999.99")


def _quantity(value: float | None) -> Decimal | None:
    """
    A stated amount, or None when the number cannot be one.

    Args:
        value (float | None): What the model read as a quantity.

    Returns:
        Decimal | None: The amount, or None when it is negative, not finite, or larger than
            the column holds.

    Note:
        Out of range means the model read something that was not a quantity — a phone number,
        a year, a follower count. Dropping it keeps the requirement, and a null amount already
        means "nobody stated one", which is the common case and reads correctly.

        A live run lost an observation to `numeric field overflow` here. The row failed, so it
        had no extraction, so every later pass picked it up and failed again — an error that
        repeats itself forever is worse than one that happens once.

        NaN is checked explicitly because `Decimal("nan")` parses without complaint and only
        raises on the comparison, three lines further down.
    """
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None

    if not amount.is_finite() or amount < 0 or amount > MAX_QUANTITY:
        logger.info("dropped an out-of-range quantity: %s", value)
        return None
    return amount.quantize(Decimal("0.01"))


@dataclass
class Ingested:
    """
    What one extraction produced.

    Attributes:
        requirements (list[Requirement]): Rows created, one per surviving item.
        dropped (list[str]): Why each rejected item was rejected, kept so a silent loss can
            be told apart from a post that genuinely said nothing.
    """

    requirements: list[Requirement] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


class Ingestor:
    """
    Builds actors, locations, contacts and requirements from one extraction.

    Note:
        The contradiction check lives here rather than in extraction, because spotting that
        one actor both needs and offers the same resource requires the actor to be resolved
        first — inside a single reading the names have usually drifted. Measured across model
        tiers: a post where an authority asks for help is repeatedly read as also offering it.

        When it fires, the need wins. An authority coordinating a response is not supplying
        the thing it just asked for, and a phantom offer is worse than a missing one: it makes
        a shortage look covered.
    """

    def __init__(
        self,
        geocoder: Geocoder | None = None,
        resolver: IdentityResolver | None = None,
        min_confidence: float = MIN_ITEM_CONFIDENCE,
        resolve_resources: bool = True,
    ):
        self.geocoder = geocoder or Geocoder()
        self.resolver = resolver or IdentityResolver()
        self.min_confidence = min_confidence
        self.resolve_resources = resolve_resources

    @transaction.atomic
    def ingest(self, extraction: Extraction) -> Ingested:
        """
        Materialise one extraction, skipping what should not reach a person.

        Args:
            extraction (Extraction): A stored reading.

        Returns:
            Ingested: The requirements created, and a reason for everything dropped.
        """
        result = ExtractionResult.model_validate(extraction.payload)
        outcome = Ingested()

        if extraction.classification == ExtractionClass.DISCARD:
            outcome.dropped.append("classified as discard")
            return outcome
        if extraction.confidence < self.min_confidence:
            outcome.dropped.append(f"confidence {extraction.confidence:.2f} below floor")
            return outcome

        observation = extraction.observation
        location = self.geocoder.resolve(extraction.geocode_query, observation.event)

        for item in self._without_contradictions(result.items, outcome):
            requirement = self._build(item, observation, location, extraction)
            if requirement is not None:
                outcome.requirements.append(requirement)
        return outcome

    def _without_contradictions(
        self, items: list[ExtractedItem], outcome: Ingested
    ) -> list[ExtractedItem]:
        """
        Drop offers that contradict a need from the same actor for the same resource.

        Args:
            items (list[ExtractedItem]): Everything the model read from one post.
            outcome (Ingested): Collects a line per dropped item.

        Returns:
            list[ExtractedItem]: What survives, with the need kept over the offer.

        Note:
            Matching is on the normalized actor name rather than the resolved actor, because
            this runs before resolution and the drift is within a single post — one name is
            the same string the model wrote twice.
        """
        needed = {
            (normalize(item.actor.name), item.resource_key)
            for item in items
            if item.direction == "needs"
        }
        surviving = []
        for item in items:
            key = (normalize(item.actor.name), item.resource_key)
            if item.direction == "offers" and key in needed:
                outcome.dropped.append(
                    f"{item.actor.name!r} cannot both need and offer {item.resource_key!r}"
                )
                continue
            surviving.append(item)
        return surviving

    def _build(
        self,
        item: ExtractedItem,
        observation: Observation,
        fallback_location: Location | None,
        extraction: Extraction,
    ) -> Requirement | None:
        """
        Turn one item into a requirement, or refuse it.

        Args:
            item (ExtractedItem): One thing needed or offered.
            observation (Observation): The post it came from.
            fallback_location (Location | None): Resolved from the post's overall geocode
                query, used when the item named no place of its own.
            extraction (Extraction): Supplies the confidence carried onto the requirement.

        Returns:
            Requirement | None: The stored requirement, or None when it has no place. A
                requirement nobody can reach is not actionable, and keeping it would inflate
                the demand a coordinator sees without giving them anywhere to go.
        """
        location = fallback_location
        if item.location_text.strip():
            location = (
                self.geocoder.resolve(
                    f"{item.location_text}, {observation.event.country_code}", observation.event
                )
                or fallback_location
            )
        if location is None:
            return None

        resolution = self.resolver.resolve(
            item.actor, observation, contacts=item.contacts, location=location
        )
        self._record_contacts(item.contacts, resolution.actor, observation)
        if item.actor.is_author:
            self._record_author_handle(resolution.actor, observation)

        resource = self._resource(item.resource_key, item.resource)
        existing = self._already_known(observation, resolution.actor, resource, item)
        if existing is not None:
            return self._corroborate(existing, observation, item)

        requirement = Requirement.objects.create(
            event=observation.event,
            actor=resolution.actor,
            direction=Direction.NEEDS if item.direction == "needs" else Direction.OFFERS,
            resource=resource,
            free_text=item.resource[:300],
            quantity=_quantity(item.quantity),
            unit=item.unit[:30],
            location=location,
            urgency=item.urgency if item.urgency in Urgency.values else Urgency.MEDIUM,
            status=RequirementStatus.OPEN,
            confidence=extraction.confidence,
            last_seen_at=observation.posted_at or timezone.now(),
        )
        requirement.evidence.add(observation)
        verification.apply(requirement, evidence_count=1)
        return requirement

    def _already_known(
        self, observation: Observation, actor, resource: ResourceType, item: ExtractedItem
    ) -> Requirement | None:
        """
        Find the live requirement this item is another sighting of.

        Args:
            observation (Observation): The post being read.
            actor: The resolved actor.
            resource (ResourceType): The resolved resource.
            item (ExtractedItem): The thing needed or offered.

        Returns:
            Requirement | None: The existing row, or None when this is genuinely new.

        Note:
            Matched on actor, resource and direction, which is what makes two posts about the
            same collection point one node instead of two. A live run produced 162 rows from
            60 posts of which 38 were repeats — four identical "punto oficial de acopio" rows
            for one actor — and every repeat was a node the map drew twice.

            Nothing else is compared. A quantity that changed between posts is new information
            about the same requirement, not a different requirement, and treating it as one
            would make the duplicate come back.
        """
        return (
            Requirement.objects.filter(
                event=observation.event,
                actor=actor,
                resource=resource,
                direction=Direction.NEEDS if item.direction == "needs" else Direction.OFFERS,
                status__in=(RequirementStatus.OPEN, RequirementStatus.PARTIAL),
            )
            .order_by("created_at")
            .first()
        )

    def _corroborate(
        self, requirement: Requirement, observation: Observation, item: ExtractedItem
    ) -> Requirement:
        """
        Attach a second sighting to a requirement that already exists.

        Returns:
            Requirement: The same row, now backed by one more observation.

        Note:
            Corroboration is the point, not the saved row. `evidence` is what says a claim was
            made twice by different posts, and it is the only automatic way a requirement can
            earn its way out of quarantine without a person looking at it.

            A stated quantity fills a blank one but never overwrites a number already there.
            The first post that bothered to count is better evidence than a later one that
            rounded.
        """
        requirement.evidence.add(observation)

        changed = ["last_seen_at"]
        requirement.last_seen_at = max(
            requirement.last_seen_at, observation.posted_at or requirement.last_seen_at
        )
        if requirement.quantity is None and _quantity(item.quantity) is not None:
            requirement.quantity = _quantity(item.quantity)
            requirement.unit = item.unit[:30]
            changed += ["quantity", "unit"]

        requirement.save(update_fields=changed)
        verification.apply(requirement)
        return requirement

    def _resource(self, key: str, label: str = "") -> ResourceType:
        """
        Map a guessed key onto the catalog, extending it when nothing fits.

        Args:
            key (str): The slug the model guessed.
            label (str): The resource in the post's own words.

        Returns:
            ResourceType: An existing type, or a new one placed under a real category.

        Note:
            The catalog is open by design — a flood asks for sandbags and a wildfire for
            masks, and neither is in the seeded categories. What the resolution adds is that
            a new arrival is *placed*: a resource with no parent only ever matches itself,
            so an unparented need is one nobody is ever proposed to fill.
        """
        return resolve_resource(key, label, use_llm=self.resolve_resources).resource

    def _record_author_handle(self, actor, observation: Observation) -> None:
        """
        Store the account that posted as a way to reach the actor it speaks for.

        Note:
            The most reliable contact in the system, and it used to be thrown away. A phone
            number in a caption is something the model read; the posting handle is something
            the platform stated, and it is how most people asking for help can be reached —
            plenty give a username and no number at all.

            Only when the model read the actor as the author. A handle taken from a post
            *about* somebody else reaches the wrong person, which during an emergency is worse
            than reaching nobody.
        """
        handle = (observation.author_handle or "").strip().lstrip("@")
        if not handle or not observation.platform:
            return

        point, created = ContactPoint.objects.get_or_create(
            actor=actor,
            kind=ContactKind.HANDLE,
            value=handle[:300],
            defaults={
                "raw_value": observation.author_handle[:300],
                "platform": observation.platform,
                "discovered_in": observation,
                "verified": bool(observation.author_verified),
                "confidence": 0.9,
            },
        )
        if not created:
            point.times_seen += 1
            point.confidence = min(1.0, 0.5 + 0.1 * point.times_seen)
            point.save(update_fields=["times_seen", "confidence"])

    def _record_contacts(
        self, contacts: list[ExtractedContact], actor, observation: Observation
    ) -> None:
        """
        Store the ways to reach an actor, counting repeats rather than duplicating them.

        Note:
            `times_seen` is the confidence signal: a number written across five posts is
            almost certainly real, one appearing once may be an extraction slip. So a repeat
            increments rather than inserting.

            A handle is stored without its `@` and carries the platform of the post it was
            read in. Both are what turns a username into something tappable: without the
            platform `contact_link` has no domain to build and returns nothing, which is how
            a live event ended up holding 79 accounts a citizen could read but not open. The
            platform is a guess — an X post can cite an Instagram account — and a profile URL
            that sometimes misses beats a username that never opens.
        """
        for contact in contacts:
            value = contact.value.strip()
            if contact.kind == ContactKind.HANDLE:
                value = value.lstrip("@")
            if not value:
                continue
            point, created = ContactPoint.objects.get_or_create(
                actor=actor,
                kind=contact.kind,
                value=value[:300],
                defaults={
                    "raw_value": contact.value.strip()[:300],
                    "payment_network": contact.network[:40],
                    "platform": (
                        observation.platform if contact.kind == ContactKind.HANDLE else ""
                    ),
                    "discovered_in": observation,
                    "confidence": 0.6,
                },
            )
            if not created:
                point.times_seen += 1
                point.confidence = min(1.0, 0.5 + 0.1 * point.times_seen)
                point.save(update_fields=["times_seen", "confidence"])
