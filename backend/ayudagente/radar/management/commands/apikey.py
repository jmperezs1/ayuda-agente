"""Mint an API key and write it into the environment file."""

from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string

# 43 characters over a 62-symbol alphabet, so ~256 bits — the same strength as a session key
KEY_LENGTH = 43
KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
KEY_PREFIX = "ayk_"

SETTING = "API_KEYS"


class Command(BaseCommand):
    """
    Generate one API key, add it to `API_KEYS` and print it.

    Note:
        The key is random, not derived from anything. `get_random_string` draws from the
        operating system's cryptographic generator, which is the property that matters —
        a key an attacker can predict is worse than no key, and a key that encodes a
        timestamp or a name is predictable.

        Existing keys are kept unless `--replace` is passed. Consumers hold different keys
        so one can be revoked without locking out the others, and a command that silently
        overwrote the list would take the frontend down every time someone added a service.
    """

    help = "Generate an API key and append it to API_KEYS in the environment file."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags, which choose what happens to the keys already configured."""
        parser.add_argument(
            "--replace", action="store_true", help="Drop the existing keys instead of adding."
        )
        parser.add_argument(
            "--show", action="store_true", help="Print the key only, and write nothing."
        )
        parser.add_argument("--env-file", default=".env", help="Defaults to .env.")

    def handle(self, *args, **options) -> None:
        """
        Mint the key and place it in the file.

        Raises:
            CommandError: If the environment file does not exist. Creating it here would
                produce one without a database password, and the next command to run would
                fail somewhere far less obvious.
        """
        key = KEY_PREFIX + get_random_string(KEY_LENGTH, KEY_ALPHABET)

        if options["show"]:
            self.stdout.write(key)
            return

        path = Path(settings.BASE_DIR) / options["env_file"]
        if not path.exists():
            raise CommandError(f"{path} not found: run make init")

        kept = [] if options["replace"] else _configured_keys(path)
        path.write_text(_with_keys(path.read_text(), [*kept, key]), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(key))
        self.stdout.write(f"written to {SETTING} in {path}, now holding {len(kept) + 1} key(s)")


def _configured_keys(path: Path) -> list[str]:
    """
    The keys already in the file.

    Note:
        Read from the file rather than from `settings.API_KEYS`, because the settings were
        loaded when the process started and a key minted a moment ago would be missing.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{SETTING}="):
            return [key.strip() for key in line.partition("=")[2].split(",") if key.strip()]
    return []


def _with_keys(content: str, keys: list[str]) -> str:
    """
    The file's text with `API_KEYS` set to `keys`, rewriting the line or adding it.

    Returns:
        str: The whole file. Every other line is left byte for byte as it was, so a comment
            or a secret elsewhere in the file cannot be lost to this command.
    """
    assignment = f"{SETTING}={','.join(keys)}"
    lines = content.splitlines()

    for index, line in enumerate(lines):
        if line.startswith(f"{SETTING}="):
            lines[index] = assignment
            break
    else:
        lines.append(assignment)

    return "\n".join(lines) + "\n"
