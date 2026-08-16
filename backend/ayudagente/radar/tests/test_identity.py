"""
Tests for the actor resolution cascade.

The cheap signals — contacts, blocking, trigram — run against the database with no model
calls, which is most of the cascade. Embeddings and adjudication are switched off so the
order of preference is what gets asserted, not the network.
"""

from datetime import UTC, datetime

import pytest

from ayudagente.radar.choices import ActorKind, ContactKind, Platform, ResolutionMethod
from ayudagente.radar.models import Actor, ActorMention, ContactPoint, Event, Observation
from ayudagente.radar.schemas import ExtractedActor, ExtractedContact
from ayudagente.radar.services.identity import IdentityResolver
from ayudagente.radar.services.text import normalize


@pytest.fixture
def event(db):
    return Event.objects.create(
        hazard="earthquake",
        name="Test event",
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
        country_code="CO",
        languages=["es"],
        detection_source="manual",
    )


@pytest.fixture
def observation(event):
    return Observation.objects.create(
        event=event,
        platform=Platform.X,
        platform_id="1",
        permalink="https://x.com/a/1",
        posted_at=datetime(2026, 8, 15, tzinfo=UTC),
        text="Punto de acopio en el Coliseo Mayor de Pereira",
        author_handle="reporta_pereira",
        raw={},
    )


@pytest.fixture
def resolver():
    """Cheap signals only, so a test asserts the cascade rather than the network."""
    return IdentityResolver(use_embeddings=False, use_llm=False)


def make_actor(event, name, kind=ActorKind.COLLECTION_CENTER):
    now = datetime(2026, 8, 12, tzinfo=UTC)
    return Actor.objects.create(
        event=event,
        kind=kind,
        canonical_name=name,
        name_norm=normalize(name),
        first_seen_at=now,
        last_seen_at=now,
    )


class TestDeterministicSignals:
    """A shared handle or phone cannot be coincidence, so it outranks every name signal."""

    def test_a_shared_phone_wins_over_an_unrelated_name(self, event, observation, resolver):
        existing = make_actor(event, "Fundación Patitas")
        ContactPoint.objects.create(
            actor=existing,
            kind=ContactKind.PHONE,
            value="+573002377012",
            raw_value="300 237 7012",
        )
        resolution = resolver.resolve(
            ExtractedActor(name="Nombre Completamente Distinto", kind="nonprofit"),
            observation,
            contacts=[ExtractedContact(kind="phone", value="+573002377012")],
        )
        assert resolution.actor == existing
        assert resolution.method == ResolutionMethod.PHONE

    def test_a_shared_address_does_not_merge_two_organisations(self, event, observation, resolver):
        existing = make_actor(event, "Fundación Patitas")
        ContactPoint.objects.create(
            actor=existing,
            kind=ContactKind.STREET_ADDRESS,
            value="calle 10 #5-5",
            raw_value="Calle 10 #5-5",
        )
        resolution = resolver.resolve(
            ExtractedActor(name="Otra Fundación", kind="nonprofit"),
            observation,
            contacts=[ExtractedContact(kind="street_address", value="calle 10 #5-5")],
        )
        assert resolution.actor != existing

    def test_a_merged_actor_is_never_matched_again(self, event, observation, resolver):
        winner = make_actor(event, "Coliseo Mayor de Pereira")
        loser = make_actor(event, "Coliseo Mayor de Pereira")
        loser.merged_into = winner
        loser.save(update_fields=["merged_into"])

        resolution = resolver.resolve(
            ExtractedActor(name="Coliseo Mayor de Pereira", kind="collection_center"),
            observation,
        )
        assert resolution.actor == winner


class TestNameSimilarity:
    """Trigrams beat semantics on proper nouns, which is why they run before embeddings."""

    def test_the_same_name_spelled_differently_resolves_to_one_actor(
        self, event, observation, resolver
    ):
        existing = make_actor(event, "Coliseo Mayor de Pereira")
        resolution = resolver.resolve(
            ExtractedActor(name="coliseo mayor de pereira", kind="collection_center"),
            observation,
        )
        assert resolution.actor == existing
        assert resolution.method == ResolutionMethod.TRIGRAM

    def test_one_word_apart_is_not_the_same_building(self, event, observation, resolver):
        make_actor(event, "Coliseo Mayor")
        resolution = resolver.resolve(
            ExtractedActor(name="Coliseo Menor", kind="collection_center"),
            observation,
        )
        assert resolution.actor.canonical_name == "Coliseo Menor"

    def test_an_unknown_name_creates_an_actor(self, event, observation, resolver):
        resolution = resolver.resolve(
            ExtractedActor(name="Taller el Adorno", kind="company"), observation
        )
        assert Actor.objects.filter(canonical_name="Taller el Adorno").exists()
        assert resolution.method == ResolutionMethod.MANUAL

    def test_actors_from_another_event_are_never_candidates(self, event, observation, resolver):
        other = Event.objects.create(
            hazard="flood",
            name="Другое",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            country_code="CO",
            detection_source="manual",
        )
        stranger = make_actor(other, "Coliseo Mayor de Pereira")
        resolution = resolver.resolve(
            ExtractedActor(name="Coliseo Mayor de Pereira", kind="collection_center"),
            observation,
        )
        assert resolution.actor != stranger


class TestAmbiguityIsLeftToTheExpensiveSignals:
    """
    Trigrams cannot settle a name extension, and must not pretend to.

    Note:
        Measured: "coliseo mayor" scores 0.560 against "coliseo mayor de pereira" and 0.556
        against "coliseo menor" — one pair is the same place and the other is two, at the
        same score. With the expensive signals off the only safe answer is a new actor,
        because a duplicate is cheaper to fix than a merge that routes aid to the wrong
        building.
    """

    def test_a_name_extension_is_not_merged_on_letters_alone(self, event, observation, resolver):
        make_actor(event, "Coliseo Mayor")
        resolution = resolver.resolve(
            ExtractedActor(name="Coliseo Mayor de Pereira", kind="collection_center"),
            observation,
        )
        assert resolution.actor.canonical_name == "Coliseo Mayor de Pereira"
        assert Actor.objects.filter(event=event).count() == 2


class TestAudit:
    """A wrong merge has to be diagnosable, so every resolution leaves a trail."""

    def test_the_surface_form_and_the_signal_are_recorded(self, event, observation, resolver):
        make_actor(event, "Coliseo Mayor de Pereira")
        resolver.resolve(
            ExtractedActor(name="el coliseo mayor de pereira", kind="collection_center"),
            observation,
        )
        mention = ActorMention.objects.get(observation=observation)
        assert mention.surface_form == "el coliseo mayor de pereira"
        assert mention.resolved_by in ResolutionMethod.values

    def test_a_spelling_variant_is_recorded_on_the_actor_it_matched(
        self, event, observation, resolver
    ):
        make_actor(event, "Coliseo Mayor de Pereira")
        resolver.resolve(
            ExtractedActor(name="COLISEO MAYOR DE PEREIRA", kind="collection_center"),
            observation,
        )
        actor = Actor.objects.get(canonical_name="Coliseo Mayor de Pereira")
        assert "COLISEO MAYOR DE PEREIRA" in actor.alternate_names

    def test_an_unnamed_mention_falls_back_to_the_author(self, event, observation, resolver):
        resolution = resolver.resolve(ExtractedActor(name="   ", kind="person"), observation)
        assert resolution.actor.canonical_name == "reporta_pereira"
