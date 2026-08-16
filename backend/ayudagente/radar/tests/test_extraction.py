"""
Tests for the multimodal extraction pass.

Everything that does not need OpenAI runs by default: the prompt assembly, which media get
sent, and how a result is stored. The one test that calls the real model is marked `live`.
"""

import base64
from datetime import UTC, datetime

import pytest

from ayudagente.radar.choices import ExtractionClass, MediaKind, Platform
from ayudagente.radar.llm import is_configured
from ayudagente.radar.models import Event, Media, Observation
from ayudagente.radar.schemas import ExtractionResult
from ayudagente.radar.services.extraction import Extractor


def _user_text(blocks: list) -> str:
    """Join every text block of the user turn, which is where the post is rendered."""
    return " ".join(part["text"] for part in blocks[1]["content"] if part["type"] == "input_text")


@pytest.fixture
def event(db):
    return Event.objects.create(
        hazard="earthquake",
        name="Chocó earthquake M7.4",
        occurred_at=datetime(2026, 8, 10, 12, 34, tzinfo=UTC),
        magnitude=7.4,
        country_code="CO",
        languages=["es"],
        detection_source="usgs",
        lexicon={"negatives": ["Venezuela", "Indonesia"]},
    )


@pytest.fixture
def observation(event):
    return Observation.objects.create(
        event=event,
        platform=Platform.FACEBOOK,
        platform_id="p1",
        permalink="https://facebook.com/p/1",
        posted_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        text="Punto de acopio en Pereira, Cra 5 con calle 34. Recibimos agua y colchonetas.",
        raw={},
    )


class TestPromptAssembly:
    """The model has to be told which disaster is ours, or it conflates two of them."""

    def test_event_context_names_the_concurrent_disasters_to_exclude(self, observation):
        text = _user_text(Extractor().build_input(observation))
        assert "Venezuela" in text and "Indonesia" in text

    def test_the_post_text_reaches_the_model(self, observation):
        text = _user_text(Extractor().build_input(observation))
        assert "Cra 5 con calle 34" in text

    def test_platform_image_descriptions_are_included(self, observation):
        Media.objects.create(
            observation=observation,
            kind=MediaKind.IMAGE,
            source_url="https://cdn.example/1.jpg",
            platform_alt_text="IBAGUÉ SOLIDARIA por nuestros hermanos de Pereira",
        )
        text = _user_text(Extractor().build_input(observation))
        assert "IBAGUÉ SOLIDARIA" in text

    def test_a_transcript_is_included_when_present(self, observation):
        observation.transcript = "estoy en la vereda kilómetro 41"
        observation.save(update_fields=["transcript"])
        text = _user_text(Extractor().build_input(observation))
        assert "kilómetro 41" in text


# Smallest valid PNG, so a test can write a real file without shipping a fixture image
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestImageSelection:
    """Bytes travel inline; a platform URL expired hours after the harvest."""

    def test_an_expired_platform_url_alone_sends_no_image(self, observation):
        Media.objects.create(
            observation=observation,
            kind=MediaKind.IMAGE,
            source_url="https://cdn.example/expired.jpg",
        )
        blocks = Extractor().build_input(observation)[1]["content"]
        assert all(block["type"] == "input_text" for block in blocks)

    def test_a_stored_copy_is_inlined_as_a_data_uri(self, observation, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        (tmp_path / "pilot").mkdir()
        (tmp_path / "pilot" / "1.png").write_bytes(TINY_PNG)
        Media.objects.create(
            observation=observation,
            kind=MediaKind.IMAGE,
            source_url="https://cdn.example/1.png",
            blob_path="pilot/1.png",
        )
        block = Extractor().build_input(observation)[1]["content"][-1]
        assert block["type"] == "input_image"
        assert block["image_url"].startswith("data:image/png;base64,")

    def test_a_missing_file_is_skipped_rather_than_raised_on(self, observation, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        Media.objects.create(
            observation=observation,
            kind=MediaKind.IMAGE,
            source_url="https://cdn.example/1.png",
            blob_path="gone.png",
        )
        blocks = Extractor().build_input(observation)[1]["content"]
        assert all(block["type"] == "input_text" for block in blocks)


class TestPersistence:
    """A stored extraction is what makes the surrounding task safe to retry."""

    def _store(self, observation, **overrides):
        result = ExtractionResult(
            classification="offer",
            confidence=0.9,
            geocode_query="Pereira, Risaralda, Colombia",
            **overrides,
        )
        return Extractor(model="test-model")._persist(observation, result, replacing=None)

    def test_a_result_from_another_disaster_is_discarded(self, observation):
        extraction = self._store(observation, belongs_to_event=False)
        assert extraction.classification == ExtractionClass.DISCARD

    def test_the_raw_result_is_kept_in_the_payload(self, observation):
        extraction = self._store(observation)
        assert extraction.payload["geocode_query"] == "Pereira, Risaralda, Colombia"


@pytest.mark.live
@pytest.mark.skipif(not is_configured(), reason="OpenAI is not configured")
def test_extraction_against_the_real_model(observation):
    """One real call, to prove the schema survives contact with the real model."""
    extraction = Extractor().run(observation, force=True)
    assert extraction.classification in ExtractionClass.values
    assert extraction.payload["items"], "a collection point post should yield at least one item"
