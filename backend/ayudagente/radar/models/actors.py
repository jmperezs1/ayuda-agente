"""
Real-world entities: where things are, and who has or needs them.

This layer is what turns loose posts into a graph with identity. Without it you can count
posts, but you cannot say "that center already has ten volunteers on the way, stop sending
people" — which is where the value lives.
"""

from django.contrib.gis.db import models as gis
from django.db import models
from pgvector.django import HnswIndex, VectorField

from ayudagente.radar.choices import (
    ActorKind,
    ContactKind,
    CredibilitySource,
    GeocodeSource,
    LocationPrecision,
    MentionRole,
    Platform,
    ResolutionMethod,
    UnreachableReason,
    precisions_at_least,
)
from ayudagente.radar.models.catalog import AdminUnit

# Actor kinds that count as organizations. Query with `kind__in=ORGANIZATION_KINDS`.
ORGANIZATION_KINDS = frozenset(
    {
        ActorKind.COLLECTION_CENTER,
        ActorKind.NONPROFIT,
        ActorKind.COMPANY,
        ActorKind.PUBLIC_ENTITY,
        ActorKind.MEDIA_OUTLET,
        ActorKind.CHURCH,
        ActorKind.SCHOOL,
        ActorKind.VOLUNTEER_GROUP,
    }
)

# Contact kinds that can actually carry a message. Payment and postal details cannot.
OUTREACH_CHANNELS = frozenset(
    {
        ContactKind.EMAIL,
        ContactKind.WHATSAPP,
        ContactKind.HANDLE,
        ContactKind.FORM,
        ContactKind.WEBSITE,
        ContactKind.PHONE,
    }
)

# Which channel to offer a human first. Least intrusive and most expected wins.
CHANNEL_PREFERENCE = (
    ContactKind.EMAIL,
    ContactKind.WHATSAPP,
    ContactKind.HANDLE,
    ContactKind.FORM,
    ContactKind.WEBSITE,
    ContactKind.PHONE,
)


class Location(models.Model):
    """
    A resolved geographic point, carrying an explicit record of how fine it is.

    `precision` is not decoration: without it matching lies. A need placed in "Chocó" and a
    truck heading to a street address are both a dot on the map, and if you cannot tell
    that one covers an entire department you will propose impossible deliveries. Each kind
    of match enforces a minimum precision.

    Note:
        Deduplicated on `text_norm` plus administrative unit, so "Coliseo Mayor, Pereira"
        is sent to Google exactly once no matter how many posts mention it. That is both a
        cache and a direct saving on the geocoding bill.

        The constraint treats nulls as equal on purpose: until the gazetteer is loaded every
        location has no administrative unit, and the default `NULLS DISTINCT` would let the
        same place be cached once per post.
    """

    point = gis.PointField(geography=True)
    precision = models.CharField(max_length=20, choices=LocationPrecision.choices)
    raw_text = models.CharField(max_length=300)
    text_norm = models.CharField(max_length=300, db_index=True)
    admin_unit = models.ForeignKey(
        AdminUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locations",
    )
    source = models.CharField(max_length=20, choices=GeocodeSource.choices)
    confidence = models.FloatField(default=1.0)
    raw_response = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # NULLS NOT DISTINCT, or the cache duplicates every place with no admin unit yet
            models.UniqueConstraint(
                fields=["text_norm", "admin_unit"],
                name="location_unique",
                nulls_distinct=False,
            )
        ]
        indexes = [models.Index(fields=["precision"])]

    def __str__(self) -> str:
        return f"{self.raw_text} [{self.precision}]"

    def is_at_least(self, precision: str) -> bool:
        """
        Report whether this location is as fine as, or finer than, a required precision.

        Args:
            precision (str): A `LocationPrecision` value to use as the minimum.

        Returns:
            bool: True when it is precise enough for a match that demands that detail.
        """
        return self.precision in precisions_at_least(precision)


class Actor(models.Model):
    """
    A real-world entity that needs or offers something: person, center, company or agency.

    This is the node of the graph. The same actor gets mentioned under different names
    across dozens of posts, and unifying those is the hardest data problem in the system:
    without stable identity there is no saturation counting and no outreach history.

    Note:
        Identity resolution is a cascade, and embeddings are the weakest signal in it, not
        the first. Deterministic keys come first (same handle, same phone), then blocking
        by municipality, then trigram similarity — which beats semantic similarity on
        proper nouns — and only then embeddings and model adjudication. "Coliseo Mayor" and
        "Coliseo Menor" are nearly identical as vectors and are different places.

        `merged_into` keeps duplicates instead of deleting them, so a bad merge can be
        undone. The model will get one wrong eventually.

        `is_organization` is derived from `kind` rather than stored, so it cannot go stale
        and cannot be skipped by `bulk_create`. Filter in SQL with
        `kind__in=ORGANIZATION_KINDS`.
    """

    event = models.ForeignKey("radar.Event", on_delete=models.CASCADE, related_name="actors")
    kind = models.CharField(max_length=25, choices=ActorKind.choices)
    canonical_name = models.CharField(max_length=250)
    name_norm = models.CharField(max_length=250, db_index=True)
    alternate_names = models.JSONField(default=list, blank=True)

    embedding = VectorField(dimensions=1536, null=True, blank=True)
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="actors"
    )

    credibility = models.FloatField(default=0.5)
    credibility_source = models.CharField(
        max_length=20, choices=CredibilitySource.choices, default=CredibilitySource.NONE
    )
    max_followers = models.IntegerField(null=True, blank=True)
    verified = models.BooleanField(default=False)

    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_from"
    )

    class Meta:
        indexes = [
            models.Index(fields=["event", "kind"]),
            HnswIndex(
                name="actor_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.canonical_name} ({self.get_kind_display()})"

    @property
    def is_organization(self) -> bool:
        """True for centers, nonprofits, companies and public bodies — never for a person."""
        return self.kind in ORGANIZATION_KINDS

    @property
    def is_merged(self) -> bool:
        """True when this actor was absorbed into another and should no longer be used."""
        return self.merged_into_id is not None


class ActorMention(models.Model):
    """
    The trail of why a given post was decided to be about a given actor.

    It exists so identity resolution can be audited and undone. When two different centers
    end up merged by mistake — and they will — this table says which signal caused it and
    with what reasoning.

    Note:
        `surface_form` keeps the name exactly as it appeared ("el coliseo", "Coliseo Mayor
        de Pereira"), which is what feeds trigram similarity on later passes.
    """

    actor = models.ForeignKey(Actor, on_delete=models.CASCADE, related_name="mentions")
    observation = models.ForeignKey(
        "radar.Observation", on_delete=models.CASCADE, related_name="actor_mentions"
    )
    surface_form = models.CharField(max_length=250)
    role = models.CharField(max_length=20, choices=MentionRole.choices)
    resolved_by = models.CharField(max_length=20, choices=ResolutionMethod.choices)
    resolution_confidence = models.FloatField()
    rationale = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "observation", "surface_form"], name="mention_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.surface_form} → {self.actor_id} ({self.resolved_by})"


class ContactPoint(models.Model):
    """
    One concrete way to reach an actor: handle, phone, email or payment account.

    It is its own table rather than a JSON blob on `Actor` because it has to be queried
    ("give me every actor with an email in Pereira"), because attempts are counted per
    channel, and because one channel can be marked as bounced without touching the others.
    Contact details also surface incrementally: the phone appears in one post and the email
    in another three days later.

    Note:
        `value` holds the normalized form and `raw_value` the one that appeared in the
        text. "300 2377012", "+57 300 237 7012" and "3002377012" are the same phone, and
        without normalization the uniqueness constraint is worthless.

        `times_seen` is a confidence signal: a number appearing across five separate posts
        is almost certainly real; one appearing once may be an extraction hallucination.

        Payment rails are one kind with the network as data, because they are country
        specific and there is no reason to migrate the schema when the system reaches a new
        one. The system never moves money — it only surfaces the detail.

    See:
        `preference_rank` for which channel a human gets offered first.
    """

    actor = models.ForeignKey(Actor, on_delete=models.CASCADE, related_name="contact_points")
    kind = models.CharField(max_length=25, choices=ContactKind.choices)
    platform = models.CharField(
        max_length=20, choices=Platform.choices, blank=True
    )  # only meaningful when kind is a platform handle
    payment_network = models.CharField(
        max_length=40, blank=True
    )  # nequi, daviplata, bre_b, pix, mpesa, upi… only when kind is payment
    value = models.CharField(max_length=300, db_index=True)
    raw_value = models.CharField(max_length=300)

    discovered_in = models.ForeignKey(
        "radar.Observation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovered_contacts",
    )
    times_seen = models.IntegerField(default=1)
    confidence = models.FloatField(default=0.5)

    verified = models.BooleanField(default=False)
    reachable = models.BooleanField(default=True)
    unreachable_reason = models.CharField(
        max_length=25, choices=UnreachableReason.choices, blank=True
    )
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "kind", "value"], name="contact_point_unique")
        ]
        indexes = [models.Index(fields=["kind", "reachable"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.value}"

    @property
    def can_carry_a_message(self) -> bool:
        """True when this detail is a channel at all. A bank account is not."""
        return self.reachable and self.kind in OUTREACH_CHANNELS

    def preference_rank(self) -> int:
        """
        Rank this channel for the human deciding how to reach the actor.

        Nothing is ever sent automatically, so this orders what the dashboard offers first
        rather than granting permission. Lower is better; channels that cannot carry a
        message sort last.

        Returns:
            int: Position in `CHANNEL_PREFERENCE`, or a value past the end when the detail
                is not a usable channel.
        """
        if not self.can_carry_a_message:
            return len(CHANNEL_PREFERENCE)
        return CHANNEL_PREFERENCE.index(self.kind)
