"""Read the USGS feed and propose the earthquakes worth responding to."""

from argparse import ArgumentParser

import httpx
from django.core.management.base import BaseCommand, CommandError

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import Event
from ayudagente.radar.services.watch import USGS_FEED, watch


class Command(BaseCommand):
    """
    Look for new disasters and record them as paused events.

    Note:
        Nothing is scraped and nothing is spent. A proposed event is `paused`, which every
        writer already refuses through `Event.is_harvestable`, so the decision to spend money
        stays where it belongs — with `arm_event`.

        Safe to run on a timer. Proposing is idempotent on the USGS id, and USGS revises a
        quake's magnitude and alert for hours after it happens.
    """

    help = "Poll USGS and propose new events, paused until somebody arms them."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the feed override and the listing flag."""
        parser.add_argument("--feed", default=USGS_FEED, help="A different USGS summary feed.")
        parser.add_argument(
            "--list",
            action="store_true",
            help="Also list the candidates already waiting to be armed.",
        )

    def handle(self, *args, **options) -> None:
        """
        Poll, propose, and say what is now waiting.

        Raises:
            CommandError: When the feed cannot be read. A watch stage that swallows a dead
                feed reports calm forever.
        """
        try:
            proposed = watch(options["feed"])
        except httpx.HTTPError as exc:
            raise CommandError(f"could not read the feed: {exc}") from exc

        if not proposed:
            self.stdout.write("nothing new")
        for event in proposed:
            self.stdout.write(
                self.style.SUCCESS(f"proposed  {event.name}  (id {event.pk}, {event.country_code})")
            )

        if options["list"] or proposed:
            self._waiting()

    def _waiting(self) -> None:
        """List every candidate nobody has armed yet."""
        waiting = Event.objects.filter(detection_source="usgs", status=EventStatus.PAUSED).order_by(
            "-occurred_at"
        )
        if not waiting:
            return

        self.stdout.write(f"\n{waiting.count()} waiting to be armed:")
        for event in waiting:
            self.stdout.write(f"  {event.pk:>4}  {event.occurred_at:%Y-%m-%d %H:%M}  {event.name}")
        self.stdout.write(f"\narm one with:  uv run manage.py arm_event {waiting[0].pk}")
