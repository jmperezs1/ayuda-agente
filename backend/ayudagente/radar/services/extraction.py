"""
The one multimodal call that turns an observation into structured meaning.

Everything a post can tell us comes out of a single schema-constrained request: what it is,
what is needed or offered, what the picture shows, and the string to geocode. Splitting it
into four calls would cost four times the rate limit for the same work, and would lose the
one thing a combined call has — seeing the caption and the image together, which is what
catches a photo that does not match its text.
"""

import base64
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from ayudagente.radar.choices import ExtractionClass
from ayudagente.radar.llm import Role, client, model_for
from ayudagente.radar.models import Extraction, Media, Observation
from ayudagente.radar.schemas import ExtractionResult

PROMPT_VERSION = "v9"

# Leading bytes of the formats the model accepts
SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# ISO base media brands that mean HEIF, which the model refuses
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")


def _image_mime(data: bytes) -> str | None:
    """
    The image type of some bytes, read from the bytes themselves.

    Args:
        data (bytes): The stored file.

    Returns:
        str | None: A MIME type, or None when the model would refuse the format.

    Note:
        The stored name cannot be trusted: it ends in whatever the platform URL did, and a
        live corpus held 176 `.php` and 147 `.bin` files that were ordinary JPEGs. Naming the
        type from the extension sent `application/x-httpd-php` and `application/octet-stream`,
        which the API rejects with a 400 — and a 400 that looked transient cost five retries
        each.

        An unrecognised header still goes as JPEG. Most of them are one, a lost call is cheap,
        and refusing everything unknown would drop the images that work today.
    """
    head = data[:16]
    for signature, mime in SIGNATURES:
        if head.startswith(signature):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:
        return None
    return "image/jpeg"


SYSTEM_PROMPT = """\
You read one social media post from a disaster zone and pull out what someone needs or what
someone is offering.

- A NEED is someone asking for something concrete: water, food, medicine, shelter, transport,
  rescue, volunteers.
- An OFFER is someone providing something concrete: a collection point, a vehicle, a donation
  drive, volunteers, goods.

Five things to hold on to:

1. Take only what the post says. Never fill in a place, a quantity or a contact that is not
   written — a wrong address sends people to the wrong door in an emergency.

2. One post can hold several items, and the same actor can appear on both sides. Three
   collection centers is three items; "we have food but no way to move it" is an offer of
   food and a need for transport.

3. Some posts have neither. Argument about the response, and reporting that describes damage
   without anyone asking for anything, are `discard`.

4. Asking where to find something is not a need. "Centro de acopio en Bogotá?" and "¿siguen
   necesitando voluntarios?" are people looking for information: nobody stated a requirement,
   and recording one inverts who needs help — the person who wanted to donate becomes a place
   asking for donations. Those are `discard`.

   A question that carries an offer is still an offer. "¿Puedo ser voluntario sin ser médico?
   Soy arquitecto" is somebody volunteering, and "¿alguien sabe de un camión? tengo ayudas
   para enviar" is a need for transport and an offer of goods. Read what the person has or
   lacks, not whether the sentence ends in a question mark.

5. Set `is_author` on the actor that is the account publishing the post. Most posts are an
   organisation or a person speaking for themselves — "estamos recibiendo donaciones" is the
   poster. Leave it false only when the post talks about somebody else: "la Cruz Roja está
   recibiendo donaciones" written by a bystander is about the Cruz Roja, not by it.

The event context below says which disaster is ours. Set `belongs_to_event` to false when the
post is about a different one.
"""

EVENT_CONTEXT = """\
Event: {name} ({hazard}, magnitude {magnitude}) on {occurred_at:%Y-%m-%d} in {country}.
Languages expected: {languages}.
Other concurrent disasters that must NOT be confused with this one: {negatives}.
"""


class Extractor:
    """
    Runs the multimodal pass and persists the result.

    Note:
        The `Extraction` row is written the moment the model answers, before geocoding or
        identity resolution run. That is deliberate: a retry after a failure further down the
        pipeline must not pay for the model call twice, and `run` returning the existing row
        is what makes the surrounding task safe to retry.
    """

    def __init__(self, prompt_version: str = PROMPT_VERSION, model: str | None = None):
        self.prompt_version = prompt_version
        self.model = model or model_for(Role.EXTRACTION)

    def run(self, observation: Observation, *, force: bool = False) -> Extraction:
        """
        Extract one observation, reusing the stored result unless asked not to.

        Args:
            observation (Observation): The post to read.
            force (bool): Re-run even when an extraction already exists, which is how a
                changed prompt is rolled out over the corpus.

        Returns:
            Extraction: The stored interpretation.

        Raises:
            ValueError: If the model refused or returned nothing parseable, so the caller
                can retry rather than persist an empty reading.
        """
        existing = Extraction.objects.filter(observation=observation).first()
        if existing and not force:
            return existing

        response = client().responses.parse(
            model=self.model,
            input=self.build_input(observation),
            text_format=ExtractionResult,
        )
        result = response.output_parsed
        if result is None:
            raise ValueError(f"no parseable output for observation {observation.pk}")
        self._repair(result, observation)
        return self._persist(observation, result, replacing=existing, usage=response.usage)

    def build_input(self, observation: Observation) -> list[Any]:
        """
        Assemble the instructions, the event context and the post itself.

        Args:
            observation (Observation): The post to read.

        Returns:
            list[Any]: Responses API input blocks, with `input_image` entries appended for
                whatever media still resolves.
        """
        content: list[Any] = [
            {"type": "input_text", "text": self._event_context(observation)},
            {"type": "input_text", "text": self._render(observation)},
        ]
        content.extend(self._image_blocks(observation))
        return [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": content},
        ]

    def _repair(self, result: ExtractionResult, observation: Observation) -> None:
        """
        Fill in what the model cannot know but the observation already does.

        Args:
            result (ExtractionResult): Parsed output, edited in place.
            observation (Observation): The post it came from.

        Note:
            Measured across model tiers rather than assumed: every tier leaves the actor
            unnamed or paraphrased when the author is the subject, which is exactly the case
            where the handle is already known. Asking the model for it buys nothing and
            invites several spellings of one entity.

            Contradictory directions from one actor are *not* repaired here. Spotting them
            needs the actor resolved first, and inside a single extraction the names have
            already drifted — so that check belongs after identity resolution.
        """
        author = observation.author_handle or observation.author_name
        for item in result.items:
            if author and not item.actor.name.strip():
                item.actor.name = author

    def _event_context(self, observation: Observation) -> str:
        """Tell the model which disaster is ours, and which ones share its vocabulary."""
        event = observation.event
        lexicon = event.lexicon or {}
        return EVENT_CONTEXT.format(
            name=event.name,
            hazard=event.get_hazard_display(),
            magnitude=event.magnitude or "unknown",
            occurred_at=event.occurred_at,
            country=event.country_code,
            languages=", ".join(event.languages) or "any",
            negatives=", ".join(lexicon.get("negatives", [])) or "none known",
        )

    def _render(self, observation: Observation) -> str:
        """Lay out everything the platform gave us as text, skipping what it did not."""
        parts = [
            f"Platform: {observation.platform}",
            f"Posted at: {observation.posted_at:%Y-%m-%d %H:%M} UTC",
            f"Author: @{observation.author_handle or observation.author_name or 'unknown'}",
            f"Text: {observation.text or '(none)'}",
        ]
        if observation.transcript:
            parts.append(f"Spoken transcript: {observation.transcript}")
        if observation.platform_geo_name:
            parts.append(f"Platform location tag: {observation.platform_geo_name}")

        # Facebook ships its own OCR of the attached image, which often carries the flyer
        alt_texts = [
            media.platform_alt_text
            for media in Media.objects.filter(observation=observation)
            if media.platform_alt_text
        ]
        if alt_texts:
            parts.append("Platform image descriptions: " + " | ".join(alt_texts))
        return "\n".join(parts)

    def _image_blocks(self, observation: Observation) -> list[Any]:
        """
        Build image inputs from our own stored copies, inlined as data URIs.

        Returns:
            list[Any]: `input_image` blocks, empty when nothing is readable.

        Note:
            The bytes travel in the request rather than as a link. A path under `MEDIA_ROOT`
            is not reachable from OpenAI's side, and the platform URL beside it expired hours
            after the harvest, so inlining is the only thing that works for both.

            Seeded pilot observations therefore go through as text only — correct rather than
            a failure, since their images no longer exist anywhere.
        """
        blocks = []
        for media in Media.objects.filter(observation=observation):
            data_uri = self._as_data_uri(media.blob_path)
            if data_uri:
                blocks.append({"type": "input_image", "image_url": data_uri})
        return blocks

    def _as_data_uri(self, blob_path: str) -> str:
        """
        Read a stored image and encode it for transport.

        Args:
            blob_path (str): Path relative to `MEDIA_ROOT`.

        Returns:
            str: A `data:` URI, or an empty string when the file is missing or holds a format
                the model refuses. Both are skipped rather than raised on, because one
                unreadable image should not cost the whole extraction.
        """
        if not blob_path:
            return ""
        path = Path(settings.MEDIA_ROOT) / blob_path
        if not path.is_file():
            return ""
        data = path.read_bytes()
        mime = _image_mime(data)
        if mime is None:
            return ""
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    def _persist(
        self,
        observation: Observation,
        result: ExtractionResult,
        *,
        replacing: Extraction | None,
        usage: Any = None,
    ) -> Extraction:
        """Store the interpretation, overwriting an earlier one only on an explicit re-run."""
        classification = (
            ExtractionClass.DISCARD if not result.belongs_to_event else result.classification
        )
        values = {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "classification": classification,
            "confidence": result.confidence,
            "payload": result.model_dump(mode="json"),
            "geocode_query": result.geocode_query[:300],
            "visual_summary": result.visual_summary,
            "text_image_conflict": result.text_image_conflict,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "created_at": timezone.now(),
        }
        if replacing:
            for field, value in values.items():
                setattr(replacing, field, value)
            replacing.save()
            return replacing
        return Extraction.objects.create(observation=observation, **values)
