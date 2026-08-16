"""
Deciding whether two mentions are the same real-world entity.

This is the hardest data problem in the system. "Coliseo Mayor", "el coliseo" and "Coliseo
Mayor de Pereira" are one place across three posts, and without joining them there is no
saturation counting and no outreach history — the system keeps sending people to a site it
cannot tell is already full.

The cascade runs cheapest first and stops as soon as something is certain:

1. A shared handle, phone or email. Deterministic, and it settles most of them.
2. Blocking, so only actors near the same place are ever compared.
3. Trigram similarity, which beats semantic similarity on proper nouns.
4. Embeddings, for the same entity worded differently.
5. The model, for what is left.

Step 4 exists because steps 1-3 miss "Cruz Roja Risaralda" against "Seccional Risaralda de la
Cruz Roja Colombiana". It is deliberately fourth: "Coliseo Mayor" and "Coliseo Menor" are
nearly identical as vectors and are different buildings.

Note:
    The trigram bar is high because it was measured, not guessed. "coliseo mayor" scores
    0.560 against "coliseo mayor de pereira" — one place — and 0.556 against "coliseo menor",
    which is two. Nothing in that band can be settled by letters, so the threshold refuses
    and the expensive signals decide.
"""

from dataclasses import dataclass

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet
from django.utils import timezone
from pgvector.django import CosineDistance

from ayudagente.radar.choices import ContactKind, MentionRole, ResolutionMethod
from ayudagente.radar.llm import Role, client, model_for
from ayudagente.radar.models import Actor, ActorMention, ContactPoint, Location, Observation
from ayudagente.radar.schemas import ActorMatchVerdict, ExtractedActor, ExtractedContact
from ayudagente.radar.services import credibility
from ayudagente.radar.services.text import normalize

TRIGRAM_CERTAIN = 0.82  # above this, two names are one name
TRIGRAM_CANDIDATE = 0.30

# Cosine distance, so lower is closer. Past this the model decides.
EMBEDDING_CERTAIN = 0.10
EMBEDDING_CANDIDATE = 0.35

# Refuse a merge the model is not sure about; a duplicate is cheaper to fix than a bad join.
LLM_CERTAIN = 0.80

# Contact kinds that identify one entity; a shared address does not
IDENTIFYING_CONTACTS = frozenset({ContactKind.HANDLE, ContactKind.PHONE, ContactKind.EMAIL})

ADJUDICATION_PROMPT = """\
You decide whether two mentions from disaster-response posts are the same real-world entity.

Say no unless you are sure. A duplicate costs a cleanup; a wrong merge makes two places look
like one and routes help to whichever address happened to win.

Watch for names that differ by one word and mean different buildings — "Coliseo Mayor" and
"Coliseo Menor" are two places. An abbreviation, a legal suffix or a regional branch of the
same organisation is the same entity.
"""


@dataclass(frozen=True)
class Resolution:
    """
    The outcome of resolving one mention.

    Attributes:
        actor (Actor): The entity the mention was attached to, new or existing.
        method (str): Which signal settled it, kept so a bad merge can be traced.
        confidence (float): How sure that signal was.
        rationale (str): Free text, filled when the model was the one deciding.
    """

    actor: Actor
    method: str
    confidence: float
    rationale: str = ""


class IdentityResolver:
    """
    Attaches an extracted mention to an actor, creating one only when nothing matches.

    Note:
        Every resolution writes an `ActorMention` recording which signal fired and why. When
        two centers end up merged by mistake — and one will — that trail is the difference
        between a diagnosable bug and a mystery.

        Contacts are matched before names because they are the only signal that cannot be
        coincidence. A shared address is excluded on purpose: two organizations often run
        collection points out of the same building.
    """

    def __init__(self, use_embeddings: bool = True, use_llm: bool = True):
        self.use_embeddings = use_embeddings
        self.use_llm = use_llm

    def resolve(
        self,
        extracted: ExtractedActor,
        observation: Observation,
        *,
        contacts: list[ExtractedContact] | None = None,
        location: Location | None = None,
    ) -> Resolution:
        """
        Find the actor a mention belongs to, or create it.

        Args:
            extracted (ExtractedActor): Name and kind as the model read them.
            observation (Observation): The post the mention came from.
            contacts (list[ExtractedContact] | None): Contacts stated in the same post.
            location (Location | None): Where the mention places the actor.

        Returns:
            Resolution: The actor and the signal that settled it.
        """
        name = extracted.name.strip() or observation.author_handle or observation.author_name
        contacts = contacts or []

        resolution = (
            self._by_contact(contacts, observation)
            or self._by_name(name, observation, location)
            or self._create(extracted, name, observation, location)
        )
        self._record(resolution, extracted, observation)
        return resolution

    def _by_contact(
        self, contacts: list[ExtractedContact], observation: Observation
    ) -> Resolution | None:
        """Match on a handle, phone or email, the only signals that cannot be coincidence."""
        values = [
            normalize(contact.value)
            for contact in contacts
            if contact.kind in IDENTIFYING_CONTACTS and contact.value.strip()
        ]
        if not values:
            return None

        point = (
            ContactPoint.objects.filter(
                actor__event=observation.event,
                actor__merged_into__isnull=True,
                value__in=values,
            )
            .select_related("actor")
            .first()
        )
        if point is None:
            return None
        method = (
            ResolutionMethod.HANDLE if point.kind == ContactKind.HANDLE else ResolutionMethod.PHONE
        )
        return Resolution(actor=point.actor, method=method, confidence=1.0)

    def _by_name(
        self, name: str, observation: Observation, location: Location | None
    ) -> Resolution | None:
        """Walk the name signals in order, stopping at the first that is certain."""
        candidates = self._candidates(name, observation, location)
        if not candidates:
            return None

        best, score = candidates[0]
        if score >= TRIGRAM_CERTAIN:
            return Resolution(actor=best, method=ResolutionMethod.TRIGRAM, confidence=score)

        if self.use_embeddings:
            matched = self._by_embedding(name, [actor for actor, _ in candidates])
            if matched is not None:
                return matched

        if self.use_llm:
            return self._adjudicate(name, best, observation)
        return None

    def _candidates(
        self, name: str, observation: Observation, location: Location | None
    ) -> list[tuple[Actor, float]]:
        """
        Narrow the field before comparing anything, and rank what survives by name similarity.

        Args:
            name (str): The mention's name.
            observation (Observation): Supplies the event, which scopes every actor.
            location (Location | None): When known, restricts to actors in the same
                administrative unit — the blocking step that keeps this from being quadratic.

        Returns:
            list[tuple[Actor, float]]: Candidates above the floor, most similar first.
        """
        queryset: QuerySet[Actor] = Actor.objects.filter(
            event=observation.event, merged_into__isnull=True
        )
        if location is not None and location.admin_unit_id is not None:
            queryset = queryset.filter(location__admin_unit_id=location.admin_unit_id)

        ranked = (
            queryset.annotate(similarity=TrigramSimilarity("name_norm", normalize(name)))
            .filter(similarity__gte=TRIGRAM_CANDIDATE)
            .order_by("-similarity")[:10]
        )
        # `similarity` is attached by annotate() and invisible to the type stubs
        return [(actor, getattr(actor, "similarity", 0.0)) for actor in ranked]

    def _by_embedding(self, name: str, candidates: list[Actor]) -> Resolution | None:
        """Catch the same entity worded differently, once the letters have failed to."""
        embedded = [actor for actor in candidates if actor.embedding is not None]
        if not embedded:
            return None

        vector = self.embed(name)
        nearest = (
            Actor.objects.filter(pk__in=[actor.pk for actor in embedded])
            .annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance")
            .first()
        )
        if nearest is None:
            return None
        distance = getattr(nearest, "distance", 1.0)
        if distance > EMBEDDING_CERTAIN:
            return None
        return Resolution(
            actor=nearest,
            method=ResolutionMethod.EMBEDDING,
            confidence=1.0 - distance,
        )

    def _adjudicate(
        self, name: str, candidate: Actor, observation: Observation
    ) -> Resolution | None:
        """Ask the model about the one pair the cheap signals could not settle."""
        question = (
            f"Event country: {observation.event.country_code}\n"
            f"Mention A: {name!r}\n"
            f"Mention B: {candidate.canonical_name!r} "
            f"(also known as: {', '.join(candidate.alternate_names) or 'nothing else'})\n"
            f"Post text: {observation.text[:500]}"
        )
        response = client().responses.parse(
            model=model_for(Role.REASONING),
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": ADJUDICATION_PROMPT}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": question}]},
            ],
            text_format=ActorMatchVerdict,
        )
        verdict = response.output_parsed
        if verdict is None or not verdict.same_entity or verdict.confidence < LLM_CERTAIN:
            return None
        return Resolution(
            actor=candidate,
            method=ResolutionMethod.LLM,
            confidence=verdict.confidence,
            rationale=verdict.reason,
        )

    def _create(
        self,
        extracted: ExtractedActor,
        name: str,
        observation: Observation,
        location: Location | None,
    ) -> Resolution:
        """Register a mention nothing matched as a new entity."""
        now = timezone.now()
        actor = Actor.objects.create(
            event=observation.event,
            kind=extracted.kind,
            canonical_name=name[:250],
            name_norm=normalize(name)[:250],
            location=location,
            embedding=self.embed(name) if self.use_embeddings else None,
            max_followers=observation.author_followers,
            verified=bool(observation.author_verified),
            first_seen_at=observation.posted_at or now,
            last_seen_at=observation.posted_at or now,
        )
        credibility.refresh(actor, observation, is_author=extracted.is_author)
        return Resolution(actor=actor, method=ResolutionMethod.MANUAL, confidence=1.0)

    def _record(
        self, resolution: Resolution, extracted: ExtractedActor, observation: Observation
    ) -> None:
        """
        Leave the trail that makes a wrong merge diagnosable rather than mysterious.

        Note:
            Credibility is refreshed here rather than only on creation. An actor first seen in
            a stranger's post and later posting from its own verified account has to gain the
            badge; scoring once at creation would freeze it at whatever the first mention was.
        """
        credibility.refresh(resolution.actor, observation, is_author=extracted.is_author)
        surface = extracted.name.strip() or resolution.actor.canonical_name
        ActorMention.objects.get_or_create(
            actor=resolution.actor,
            observation=observation,
            surface_form=surface[:250],
            defaults={
                "role": MentionRole.AUTHOR if extracted.is_author else MentionRole.SUBJECT,
                "resolved_by": resolution.method,
                "resolution_confidence": resolution.confidence,
                "rationale": resolution.rationale,
            },
        )
        if surface and surface not in resolution.actor.alternate_names:
            resolution.actor.alternate_names.append(surface[:250])
        resolution.actor.last_seen_at = observation.posted_at or timezone.now()
        resolution.actor.save(update_fields=["alternate_names", "last_seen_at"])

    def embed(self, text: str) -> list[float]:
        """
        Turn a name into a vector.

        Args:
            text (str): The name to embed.

        Returns:
            list[float]: The embedding, sized to match `Actor.embedding`.
        """
        response = client().embeddings.create(model=model_for(Role.EMBEDDING), input=text)
        return response.data[0].embedding
