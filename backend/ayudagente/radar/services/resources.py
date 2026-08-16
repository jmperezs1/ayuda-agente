"""
Resolving what the extractor called a resource onto the catalog.

The catalog is not a fixed list. A flood asks for sandbags, a wildfire for N95 masks, and
neither is in the twenty-four categories anyone thought to seed — so a key nobody declared
has to become a real resource rather than be dropped. That part already worked.

What did not is everything after. A guess arrives as a slug the model invented, and the
guesses drift: "colchonetas" one day, "sleeping_mats" the next, "bedding" on a third. Each
drift became its own parentless row, so `resource_family` returned only itself and an offer
of the same thing under a different name covered nothing. The catalog had already split that
way once — `agua` beside `water`, `transporte` beside `transport` — and the fix was a hand
written list of legacy keys, which is a patch that should not need to exist.

So this is the actor cascade applied to a second table: deterministic key, then alias, then
trigram over both the slug and the Spanish name, then the model for what the cheap signals
could not settle. Every resolution is written back as an alias, so a drifted guess costs one
model call across a whole emergency rather than one per post.

Note:
    No embedding stage, and the omission is deliberate. Embeddings earn their place in actor
    resolution because there are thousands of pairs and each avoided model call is real
    money. An emergency produces a few dozen distinct resources, so the layer would add a
    migration, a vector column and a call per resource to save almost nothing — and go
    straight from letters to judgment is both cheaper and easier to explain.

    A new resource is created with a parent the model chooses, never as a root. A root only
    ever matches itself, so a resource that arrives unparented is a resource nothing can be
    substituted for — which for a need means nobody is ever proposed to fill it.
"""

import logging
from dataclasses import dataclass

from django.contrib.postgres.lookups import Unaccent
from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.db.models.functions import Lower

from ayudagente.radar.llm import Role, client, is_configured, model_for
from ayudagente.radar.models import ResourceType
from ayudagente.radar.schemas import ResourceVerdict
from ayudagente.radar.services.text import normalize

logger = logging.getLogger(__name__)

# Above this the letters have settled it; a guess and a catalog entry are the same resource
TRIGRAM_CERTAIN = 0.62
TRIGRAM_CANDIDATE = 0.25

LLM_CERTAIN = 0.75

MAX_CANDIDATES = 8
FALLBACK_KEY = "unclassified"

RESOLUTION_PROMPT = """\
You maintain the resource catalog of a disaster-response system.

A pipeline reading social media has produced a resource it could not match to the catalog.
Decide whether it is one of the existing entries under a different name, or genuinely new.

Say it matches only when the two are the *same kind of thing*. Sleeping mats and blankets are
bedding. Groceries, food parcels and hot meals are food. Drinking water is water. But a
generator is not electricity in general, and pet food is not food for people — those are
separate entries that already exist for a reason.

When it is new, give it a parent from the catalog. A resource with no parent can only ever be
matched against itself, which for a need means nobody will be proposed to fill it. Choose the
narrowest category that honestly contains it, and give it a name in the language of the post.
"""


@dataclass(frozen=True)
class Resolved:
    """
    One resolution.

    Attributes:
        resource (ResourceType): What the item will point at.
        method (str): `key`, `alias`, `trigram`, `llm` or `created`.
        created (bool): True when the catalog grew.
    """

    resource: ResourceType
    method: str
    created: bool = False


def resolve_resource(key: str, label: str = "", use_llm: bool = True) -> Resolved:
    """
    Map an extracted resource onto the catalog, extending it when nothing fits.

    Args:
        key (str): The slug the extractor guessed.
        label (str): The resource in the post's own words, which is what the model reads and
            what a new entry is named after.
        use_llm (bool): False skips adjudication, which is how the hermetic suite runs. A
            resource the letters could not place then lands as a root, exactly as before.

    Returns:
        Resolved: Always a usable resource. This never fails: dropping an item because its
            resource was unfamiliar would throw away the need it describes.

    Note:
        Writes the incoming key back as an alias on whatever it resolved to, which is what
        makes the expensive path run once. The second post saying "colchonetas" takes the
        alias branch and never reaches the model.
    """
    slug = normalize(key).replace(" ", "_")[:60] or FALLBACK_KEY

    exact = ResourceType.objects.filter(key=slug).first()
    if exact is not None:
        return Resolved(exact, method="key")

    alias = ResourceType.objects.filter(alternate_keys__contains=[slug]).first()
    if alias is not None:
        return Resolved(alias, method="alias")

    candidates = _candidates(slug, label)
    if candidates and candidates[0][1] >= TRIGRAM_CERTAIN:
        return _remember(candidates[0][0], slug, method="trigram")

    if use_llm:
        adjudicated = _adjudicate(slug, label, candidates)
        if adjudicated is not None:
            return adjudicated

    return _create(slug, label, parent=None)


def _candidates(slug: str, label: str) -> list[tuple[ResourceType, float]]:
    """
    Rank the catalog by how close it reads to the guess.

    Returns:
        list[tuple[ResourceType, float]]: Entries above the floor, most similar first.

    Note:
        Compared against both the slug and the Spanish name, because the two carry different
        information. The key catches a drifted English guess — "sleeping_mats" against
        "bedding" — and the name catches the cross-language case, since the extractor emits
        English keys while the post says "colchonetas".
    """
    written = normalize(label) or slug.replace("_", " ")

    ranked = (
        ResourceType.objects.annotate(
            by_key=TrigramSimilarity("key", slug),
            by_name=TrigramSimilarity(Unaccent(Lower("name")), written),
        )
        .filter(by_key__gte=TRIGRAM_CANDIDATE)
        .order_by("-by_key")[:MAX_CANDIDATES]
    )
    scored = [
        (resource, max(getattr(resource, "by_key", 0.0), getattr(resource, "by_name", 0.0)))
        for resource in ranked
    ]

    by_name = (
        ResourceType.objects.annotate(by_name=TrigramSimilarity(Unaccent(Lower("name")), written))
        .filter(by_name__gte=TRIGRAM_CANDIDATE)
        .order_by("-by_name")[:MAX_CANDIDATES]
    )
    seen = {resource.pk for resource, _ in scored}
    scored += [
        (resource, getattr(resource, "by_name", 0.0))
        for resource in by_name
        if resource.pk not in seen
    ]

    return sorted(scored, key=lambda pair: pair[1], reverse=True)[:MAX_CANDIDATES]


def _adjudicate(
    slug: str, label: str, candidates: list[tuple[ResourceType, float]]
) -> Resolved | None:
    """
    Ask the model whether this is an existing resource, and where a new one belongs.

    Returns:
        Resolved | None: A match or a newly parented entry, or None when the model is not
            configured or refuses to decide — in which case the caller creates a root and
            the catalog is merely as good as it was before.
    """
    if not is_configured():
        return None

    catalog = "\n".join(
        f"- {resource.key}: {resource.name}"
        + (f" (under {resource.parent.key})" if resource.parent else "")
        for resource in ResourceType.objects.select_related("parent").order_by("key")
    )
    near = ", ".join(f"{resource.key} ({score:.2f})" for resource, score in candidates[:4])

    question = (
        f"Catalog:\n{catalog}\n\n"
        f"Unmatched resource\n  guessed key: {slug!r}\n  as written in the post: {label!r}\n"
        f"  closest by spelling: {near or 'nothing close'}"
    )

    try:
        response = client().responses.parse(
            model=model_for(Role.REASONING),
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": RESOLUTION_PROMPT}]},
                {"role": "user", "content": [{"type": "input_text", "text": question}]},
            ],
            text_format=ResourceVerdict,
        )
    except Exception:
        logger.exception("resource adjudication failed for %r", slug)
        return None

    verdict = response.output_parsed
    if verdict is None or verdict.confidence < LLM_CERTAIN:
        return None

    if verdict.matches_key:
        existing = ResourceType.objects.filter(key=verdict.matches_key).first()
        if existing is not None:
            return _remember(existing, slug, method="llm")

    parent = ResourceType.objects.filter(key=verdict.parent_key).first()
    return _create(slug, label, parent=parent, name=verdict.name)


def _remember(resource: ResourceType, slug: str, method: str) -> Resolved:
    """
    Record the guess that led here, so the next post skips the whole cascade.

    Note:
        Guarded against the key that already exists as an alias elsewhere. Two resources
        claiming the same alias would make resolution depend on row order, which is the kind
        of bug that only shows up once the catalog is large.
    """
    if slug not in resource.alternate_keys:
        with transaction.atomic():
            taken = ResourceType.objects.filter(alternate_keys__contains=[slug]).exclude(
                pk=resource.pk
            )
            if not taken.exists():
                resource.alternate_keys = [*resource.alternate_keys, slug]
                resource.save(update_fields=["alternate_keys"])
    logger.info("resource %r resolved to %s by %s", slug, resource.key, method)
    return Resolved(resource, method=method)


def _create(slug: str, label: str, parent: ResourceType | None, name: str = "") -> Resolved:
    """
    Register a resource the catalog did not have.

    Note:
        Named after the post's own words when the model did not supply one, because a slug
        is an identifier and a coordinator reads the name. An entry called `sandbags` on a
        Spanish dashboard is a bug in the product, not in the data.

        Only the first letter is raised. `capitalize` lowercases everything after it, which
        turns "Mascarillas N95" into "Mascarillas n95" and quietly destroys every acronym.
    """
    display = (name or label or slug.replace("_", " ")).strip()[:120]
    resource, created = ResourceType.objects.get_or_create(
        key=slug,
        defaults={"name": display[:1].upper() + display[1:], "parent": parent},
    )
    if created:
        logger.info("resource %r created under %s", slug, parent.key if parent else "no parent")
    return Resolved(resource, method="created", created=created)
