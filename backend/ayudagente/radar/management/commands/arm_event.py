"""Give a proposed event permission to be harvested."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import AdminUnit, Event
from ayudagente.radar.services.gazetteer import GazetteerError, load_country
from ayudagente.radar.services.sweep import bootstrap_event


class Command(BaseCommand):
    """
    Arm a detected event: give it its search vocabulary, its watch targets and its sweep.

    Note:
        This is the one place a human decides to spend money, and it is deliberately a separate
        act from detecting. Detection is free and continuous; harvesting costs real credit per
        query, so the two must not be the same command.

        Even here nothing is scraped. The sweep is queued as pending jobs and `make harvest`
        runs them, so arming is reversible right up to the moment somebody dispatches.
    """

    help = "Activate a proposed event and queue its cold-start sweep."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare which event, and the vocabulary the feed could not know."""
        parser.add_argument("event_id", type=int)
        parser.add_argument("--languages", default="es", help="Comma separated ISO 639-1.")
        parser.add_argument("--hashtags", default="", help="Comma separated, without the #.")
        parser.add_argument(
            "--negatives", default="", help="Terms belonging to other concurrent emergencies."
        )
        parser.add_argument("--demand", default="", help="Words the affected write.")
        parser.add_argument("--supply", default="", help="Words helpers write.")
        parser.add_argument(
            "--rearm",
            action="store_true",
            help="Bootstrap an event that is already active, instead of refusing.",
        )

    def handle(self, *args, **options) -> None:
        """
        Activate the event and bootstrap its frontier.

        Raises:
            CommandError: When no such event exists, when it is already active and `--rearm`
                was not given, or when its country's gazetteer cannot be loaded — a sweep with
                no toponym pulls in other countries' disasters, which is invariant 9.

        Note:
            An event can be active and still have no frontier: the seeded corpus arrives that
            way, already carrying its posts but with nothing to harvest from. `--rearm` gives
            one a sweep without pausing it first, and it queues fresh jobs every time it runs,
            which is why it is a flag rather than the default.
        """
        event = Event.objects.filter(pk=options["event_id"]).first()
        if event is None:
            raise CommandError(f"no event {options['event_id']}")
        if event.status == EventStatus.ACTIVE and not options["rearm"]:
            raise CommandError(f"{event.name} is already active; pass --rearm to sweep it again")
        self._ensure_gazetteer(event)

        with transaction.atomic():
            event.languages = _terms(options["languages"])
            event.lexicon = {
                "hashtags": [f"#{t.lstrip('#')}" for t in _terms(options["hashtags"])],
                "negatives": _terms(options["negatives"]),
                "demand": _terms(options["demand"]),
                "supply": _terms(options["supply"]),
            }
            event.status = EventStatus.ACTIVE
            event.save(update_fields=["languages", "lexicon", "status"])
            counts = bootstrap_event(event)

        self.stdout.write(self.style.SUCCESS(f"armed {event} (id {event.pk})"))
        self.stdout.write(f"  {counts['nodes']} watch targets, {counts['jobs']} sweep jobs queued")
        self.stdout.write(f"\nrun them with:  make harvest ARGS='{event.pk}'")

    def _ensure_gazetteer(self, event: Event) -> None:
        """
        Load the event's country from GeoNames when nothing local covers it yet.

        Args:
            event (Event): The emergency about to be armed.

        Raises:
            CommandError: When the dump cannot be read, or holds no unit for the country.
                Arming without toponyms queues a sweep that pulls in other countries'
                disasters, which is invariant 9.

        Note:
            Done here rather than by hand because the event already carries its country and
            the dump costs nothing. Detection stays free and continuous; arming is the first
            act that prepares to spend, so it is the right one to pay a download.
        """
        code = event.country_code.upper()
        if AdminUnit.objects.filter(country_code=code).exists():
            return

        self.stdout.write(f"no gazetteer for {code} yet, downloading it from GeoNames")
        try:
            load_country(code)
        except GazetteerError as exc:
            raise CommandError(f"could not load the gazetteer for {code}: {exc}") from exc

        total = AdminUnit.objects.filter(country_code=code).count()
        if not total:
            raise CommandError(f"the GeoNames dump for {code} held no administrative units")
        self.stdout.write(self.style.SUCCESS(f"  {total} places loaded for {code}"))


def _terms(raw: str) -> list[str]:
    """Split a comma-separated option into clean terms."""
    return [term.strip() for term in raw.split(",") if term.strip()]
