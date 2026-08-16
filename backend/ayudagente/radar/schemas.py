"""
The contract the model must fill when reading one observation.

This is the only place a DTO earns its keep alongside the agent's tool signatures: structured
output needs a JSON schema, and Pydantic gives validation plus a retry when the model strays
from it. Everywhere else the services pass Django instances around.

Note:
    The schema is deliberately close to what a post actually says rather than to the database.
    It captures `resource` as free text next to a guessed taxonomy key, and location as the
    words used rather than coordinates, because resolving either is a later step with its own
    failure modes. Making the model guess a foreign key is how you get confident nonsense.
"""

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["needs", "offers"]
Classification = Literal["need", "offer", "both", "discard"]
Urgency = Literal["critical", "high", "medium", "low"]
ContactKind = Literal[
    "handle", "phone", "whatsapp", "email", "website", "form", "payment", "street_address"
]
ActorKind = Literal[
    "person",
    "collection_center",
    "nonprofit",
    "company",
    "public_entity",
    "media_outlet",
    "community",
    "church",
    "school",
    "volunteer_group",
]


class ExtractedContact(BaseModel):
    """A way to reach the actor, exactly as it appeared in the post."""

    kind: ContactKind
    value: str = Field(description="The handle, number, address or account as written.")
    network: str = Field(
        default="",
        description="Payment network when kind is payment: nequi, daviplata, bre_b, pix, upi.",
    )


class ExtractedActor(BaseModel):
    """
    Who needs or offers the thing.

    Note:
        `is_author` decides whose reputation the claim carries. A post *by* a collection
        center saying "we are receiving donations" is that center speaking; a stranger saying
        "the center is receiving donations" is hearsay about it, and the follower count and
        verification badge on that post belong to the stranger.

        Asked of the model rather than derived from the handle, because the two almost never
        match as strings — "Abaco (Bancos de Alimentos)" against `@abacocolombia`.
    """

    name: str = Field(description="The name as written, without normalizing or expanding it.")
    kind: ActorKind
    is_author: bool = Field(
        default=False,
        description="True when this entity is the account that published the post, rather "
        "than someone the post talks about.",
    )


class ExtractedItem(BaseModel):
    """
    One thing an actor needs or offers.

    Note:
        A post can hold several. One listing three collection centers yields three items, and
        "we have food but no way to move it" yields two with opposite directions.
    """

    direction: Direction
    resource: str = Field(description="What is needed or offered, in the post's own words.")
    resource_key: str = Field(
        description="Lowercase slug guess for the taxonomy: water, food, medicine, shelter, "
        "transport, volunteers, hygiene, bedding, pet_food, machinery, cash, mental_health."
    )
    quantity: float | None = Field(
        default=None,
        description="Any number quantifying this item: 500 for 500 litres, 80 for 80 "
        "affected families. Null only when the post states no number.",
    )
    unit: str = Field(
        default="",
        description="What the quantity counts, as written: litros, familias, personas, "
        "camiones. This is what tells a resource count apart from a people count.",
    )
    location_text: str = Field(
        default="",
        description="Where this item is, in the post's words. Empty when the post says "
        "nothing; never guess a place that is not written.",
    )
    urgency: Urgency = "medium"
    window_text: str = Field(
        default="",
        description="Any deadline or departure time as written: 'until 16 August', "
        "'leaves tomorrow'. Empty when none is given.",
    )
    actor: ExtractedActor
    contacts: list[ExtractedContact] = Field(
        default_factory=list,
        description="Only contacts written in the post. The author's own handle is known "
        "already and is derived later, so never repeat it here.",
    )


class ExtractionResult(BaseModel):
    """
    Everything the model understood from one observation, in a single pass.

    Note:
        Classification, extraction, image reading and the geocoding string come out together
        because four calls would cost four times the rate limit for the same work — and
        because the model resolves a contradiction between the caption and the picture better
        when it sees both at once.
    """

    classification: Classification = Field(
        description="discard covers everything with nothing to act on: political argument "
        "about the response, and reporting that describes damage without anyone asking "
        "for help. Both are a large share of the volume."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    language: str = Field(default="", description="ISO 639-1 code of the post.")
    geocode_query: str = Field(
        default="",
        description="A single geocodable string built from the post plus the event's country: "
        "'Gamma sector, Pereira, Risaralda, Colombia'. Empty when no place is mentioned.",
    )
    visual_summary: str = Field(
        default="",
        description="What the images show, including any text legible in them. Empty when "
        "there was no image to read.",
    )
    text_image_conflict: bool = Field(
        default=False,
        description="True when the image does not match what the text claims, which suggests "
        "recycled or mislabelled imagery.",
    )
    belongs_to_event: bool = Field(
        default=True,
        description="False when the post is about a different disaster that shares vocabulary.",
    )
    items: list[ExtractedItem] = Field(default_factory=list)


class ActorMatchVerdict(BaseModel):
    """
    Whether two mentions are the same real-world entity.

    Note:
        Asked only for the pairs the cheap signals could not settle. The model is told to
        refuse rather than guess, because a wrong merge is worse than a duplicate: it makes
        two places look like one and sends aid to whichever address won.
    """

    same_entity: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, so a bad merge can be diagnosed later.")


class ResourceVerdict(BaseModel):
    """
    Where an unfamiliar resource belongs in the catalog.

    Note:
        Asked once per resource the cheap signals could not place, not once per post — the
        answer is written back as an alias, so the second occurrence never reaches a model.

        `parent_key` is required even when the resource is new, because a resource with no
        parent can only ever be matched against itself. For a need that means nobody is ever
        proposed to fill it, which is a silent failure rather than a visible one.
    """

    matches_key: str = Field(
        default="",
        description="An existing catalog key when this is the same kind of thing under "
        "another name. Empty when it is genuinely new.",
    )
    parent_key: str = Field(
        default="",
        description="The narrowest existing category that honestly contains it. Used only "
        "when matches_key is empty.",
    )
    name: str = Field(
        default="",
        description="Display name for a new resource, in the language of the post.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, so a bad placement can be diagnosed later.")
