"""Fetch our own copy of the images posts carried, before their URLs expire."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from ayudagente.radar.services.media import download_pending, pending


class Command(BaseCommand):
    """
    Download the media rows that still have no local copy.

    Note:
        Failures are the normal case, not an error. Platform URLs are signed and expire within
        hours, so anything harvested more than a day ago is likely already gone — the number
        worth reading is how many were saved, not how many were missed.
    """

    help = "Store a local copy of harvested images before their signed URLs expire."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags."""
        parser.add_argument("event_id", type=int, nargs="?", help="Defaults to every event.")
        parser.add_argument("--limit", type=int, help="Fetch at most this many files.")

    def handle(self, *args, **options) -> None:
        """Fetch what is missing and report what landed."""
        waiting = pending(options["event_id"]).count()
        self.stdout.write(f"{waiting} media without a local copy")
        if not waiting:
            return

        result = download_pending(options["event_id"], options["limit"])

        style = self.style.SUCCESS if result.stored or result.reused else self.style.WARNING
        self.stdout.write(
            style(
                f"  {result.stored} stored, {result.reused} already held, "
                f"{result.failed} unreachable — {result.bytes / 1_048_576:.1f} MB"
            )
        )
