"""
Naming an image's type from its bytes rather than from its filename.

A stored blob keeps whatever extension the platform URL ended in, and a live corpus held 176
`.php` and 147 `.bin` files that were ordinary JPEGs. Declaring the type from the name sent
`application/x-httpd-php` and `application/octet-stream`, which the API rejects with a 400 —
so every case here is about trusting the header instead.
"""

from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from openai import BadRequestError, RateLimitError

from ayudagente.radar.services.extraction import Extractor, _image_mime
from ayudagente.radar.tasks import process_observation

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 10
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 4


class ImageMimeTests(TestCase):
    def test_it_reads_the_type_from_the_header(self):
        self.assertEqual(_image_mime(JPEG), "image/jpeg")
        self.assertEqual(_image_mime(PNG), "image/png")
        self.assertEqual(_image_mime(GIF), "image/gif")
        self.assertEqual(_image_mime(WEBP), "image/webp")

    def test_a_jpeg_stored_under_a_lying_extension_is_still_a_jpeg(self):
        self.assertEqual(_image_mime(JPEG), "image/jpeg")  # the name said .php or .bin

    def test_heif_is_refused_rather_than_mislabelled(self):
        self.assertIsNone(_image_mime(HEIC))

    def test_an_unrecognised_header_is_attempted_as_jpeg(self):
        self.assertEqual(_image_mime(b"not an image at all"), "image/jpeg")


class DataUriTests(TestCase):
    def _uri(self, data: bytes, name: str) -> str:
        path = Path(self.tmp) / name
        path.write_bytes(data)
        with patch("ayudagente.radar.services.extraction.settings.MEDIA_ROOT", self.tmp):
            return Extractor(model="x")._as_data_uri(name)

    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()

    def test_the_extension_does_not_decide_the_declared_type(self):
        self.assertTrue(self._uri(JPEG, "a.bin").startswith("data:image/jpeg;base64,"))
        self.assertTrue(self._uri(PNG, "b.php").startswith("data:image/png;base64,"))

    def test_a_refused_format_yields_nothing_to_send(self):
        self.assertEqual(self._uri(HEIC, "c.jpg"), "")

    def test_a_missing_file_is_skipped(self):
        with patch("ayudagente.radar.services.extraction.settings.MEDIA_ROOT", self.tmp):
            self.assertEqual(Extractor(model="x")._as_data_uri("nope.jpg"), "")


class RetryPolicyTests(TestCase):
    """
    Which OpenAI failures are worth a second attempt.

    Note:
        `BadRequestError` subclasses `APIError`, so listing the latter as retryable silently
        made a 400 look transient. Five attempts, five slots, five identical failures — and
        146 posts doing it at once left the pool idle in front of a queue of 1244.
    """

    def test_a_bad_request_is_refused_rather_than_retried(self):
        self.assertIn(BadRequestError, process_observation.dont_autoretry_for)

    def test_a_rate_limit_is_still_retried(self):
        self.assertIn(RateLimitError, process_observation.autoretry_for)

    def test_the_reading_throttle_comes_from_the_environment(self):
        self.assertEqual(process_observation.rate_limit, settings.EXTRACTION_RATE_LIMIT)

    def test_the_throttle_leaves_room_for_the_whole_pool(self):
        per_minute = int(settings.EXTRACTION_RATE_LIMIT.removesuffix("/m"))

        self.assertGreaterEqual(per_minute, 240)  # eight slots at ~1.7s a call
