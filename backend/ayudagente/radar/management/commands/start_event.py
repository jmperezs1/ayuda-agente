"""Create an event and prepare it to be harvested."""

from argparse import ArgumentParser

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from ayudagente.radar.choices import HazardKind
from ayudagente.radar.models import AdminUnit, Event
from ayudagente.radar.services.sweep import bootstrap_event


class Command(BaseCommand):
    """
    Open an emergency: create the event, give it watch targets, queue the cold-start sweep.

    Note:
        This is the manual stand-in for the watch stage. Detecting that a disaster happened —
        from GDACS, USGS or a national feed — is its own slice; everything downstream of the
        `Event` row already works, so typing one is enough to start.

        Nothing is scraped here. The sweep is queued as pending jobs and `make harvest` runs
        them, which keeps the decision to spend money separate from the decision to start.
    """

    help = "Create an event, bootstrap its frontier and queue the cold-start sweep."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare what an event needs to exist and to be searchable."""
        parser.add_argument("name", help='Human name, e.g. "Sismo Chocó M7.4".')
        parser.add_argument("--country", required=True, help="ISO 3166-1 alpha-2, e.g. CO.")
        parser.add_argument("--hazard", default=HazardKind.EARTHQUAKE, choices=HazardKind.values)
        parser.add_argument("--at", required=True, help="When it happened, ISO 8601 UTC.")
        parser.add_argument("--epicenter", help='"lat,lon". Without it every place is impact.')
        parser.add_argument("--magnitude", type=float)
        parser.add_argument("--languages", default="es", help="Comma separated ISO 639-1.")
        parser.add_argument(
            "--hashtags", default="", help="Comma separated, without the leading #."
        )
        parser.add_argument(
            "--negatives",
            default="",
            help="Terms belonging to other concurrent emergencies, comma separated.",
        )
        parser.add_argument(
            "--demand",
            default="",
            help="Words the affected write, in their language: necesitamos, urgente, ayuda.",
        )
        parser.add_argument(
            "--supply",
            default="",
            help="Words helpers write: punto de acopio, donaciones, recogemos.",
        )

    def handle(self, *args, **options) -> None:
        """
        Create the event and bootstrap it, or explain what is missing.

        Raises:
            CommandError: On an unparseable timestamp or epicenter, on a country with no
                gazetteer loaded, or when an event of the same name is already open.
        """
        occurred_at = parse_datetime(options["at"])
        if occurred_at is None:
            raise CommandError(f"--at is not an ISO 8601 timestamp: {options['at']!r}")

        country = options["country"].upper()
        if not AdminUnit.objects.filter(country_code=country).exists():
            raise CommandError(
                f"no gazetteer for {country}; run `manage.py load_gazetteer {country}` first"
            )

        if Event.objects.filter(name=options["name"]).exists():
            raise CommandError(f"an event named {options['name']!r} already exists")

        with transaction.atomic():
            event = Event.objects.create(
                name=options["name"],
                hazard=options["hazard"],
                occurred_at=occurred_at,
                epicenter=self._epicenter(options["epicenter"]),
                magnitude=options["magnitude"],
                country_code=country,
                languages=_terms(options["languages"]),
                detection_source="manual",
                lexicon={
                    "hashtags": [f"#{t.lstrip('#')}" for t in _terms(options["hashtags"])],
                    "negatives": _terms(options["negatives"]),
                    "demand": _terms(options["demand"]),
                    "supply": _terms(options["supply"]),
                },
            )
            counts = bootstrap_event(event)

        self.stdout.write(self.style.SUCCESS(f"created {event} (id {event.pk})"))
        self.stdout.write(f"  {counts['nodes']} watch targets, {counts['jobs']} sweep jobs queued")
        self.stdout.write(f"\nrun them with:  make harvest ARGS='{event.pk}'")

    def _epicenter(self, raw: str | None) -> Point | None:
        """
        Parse `"lat,lon"` into a point.

        Raises:
            CommandError: On anything that is not two numbers.
        """
        if not raw:
            return None
        try:
            latitude, longitude = (float(part) for part in raw.split(","))
        except ValueError as exc:
            raise CommandError(f'--epicenter takes "lat,lon", got {raw!r}') from exc
        return Point(longitude, latitude, srid=4326)


def _terms(raw: str) -> list[str]:
    """Split a comma-separated option into clean terms."""
    return [term.strip() for term in raw.split(",") if term.strip()]
