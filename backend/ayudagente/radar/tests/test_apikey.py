"""
Tests for the key-minting command.

What matters is not the randomness — that is the standard library's problem — but what the
command does to a file full of other secrets. Losing a database password to a convenience
command is the failure worth guarding against.
"""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from ayudagente.radar.management.commands.apikey import KEY_PREFIX

ENV_BODY = """SECRET_KEY=change-me
DEBUG=True

# One key per API consumer
API_KEYS=existing-key
OPENAI_API_KEY=sk-secret
"""


class ApiKeyCommandTests(SimpleTestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.path = Path(self.directory) / ".env"
        self.path.write_text(ENV_BODY, encoding="utf-8")

    def _run(self, *args) -> str:
        out = StringIO()
        with override_settings(BASE_DIR=self.directory):
            call_command("apikey", *args, stdout=out)
        return out.getvalue()

    def _keys(self) -> list[str]:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_KEYS="):
                return [key for key in line.partition("=")[2].split(",") if key]
        return []

    def test_it_adds_a_key_and_keeps_the_ones_already_there(self):
        output = self._run()

        keys = self._keys()
        self.assertEqual(keys[0], "existing-key")
        self.assertEqual(len(keys), 2)
        self.assertTrue(keys[1].startswith(KEY_PREFIX))
        self.assertIn(keys[1], output)

    def test_replace_drops_the_existing_keys(self):
        self._run("--replace")

        self.assertEqual(len(self._keys()), 1)

    def test_every_other_line_survives_byte_for_byte(self):
        self._run()

        content = self.path.read_text(encoding="utf-8")
        self.assertIn("OPENAI_API_KEY=sk-secret", content)
        self.assertIn("# One key per API consumer", content)
        self.assertIn("SECRET_KEY=change-me", content)

    def test_two_runs_produce_two_different_keys(self):
        self._run()
        self._run()

        keys = self._keys()
        self.assertEqual(len(set(keys)), 3)

    def test_show_prints_a_key_without_touching_the_file(self):
        before = self.path.read_text(encoding="utf-8")

        output = self._run("--show")

        self.assertTrue(output.strip().startswith(KEY_PREFIX))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_missing_file_is_an_error_rather_than_a_new_one(self):
        self.path.unlink()

        with self.assertRaises(CommandError):
            self._run()

    def test_the_line_is_added_when_the_file_has_none(self):
        self.path.write_text("SECRET_KEY=change-me\n", encoding="utf-8")

        self._run()

        self.assertEqual(len(self._keys()), 1)
