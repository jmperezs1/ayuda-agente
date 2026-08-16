"""Attach stored locations to the municipality they fall in."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from ayudagente.radar.models import Actor, Location, Requirement
from ayudagente.radar.services.geocoding import unit_for


class Command(BaseCommand):
    """
    Fill `Location.admin_unit` for the places geocoded before the link was being written.

    Note:
        The geocoder stored every location with `admin_unit=None`, and three consumers read
        that link — the agent's place filter, the blocking stage of identity resolution and
        the frontier's promotion of accounts. All three answered as if the country were
        empty. New locations resolve their unit on the way in; this is for the ones already
        stored.

        Safe to re-run and safe to interrupt. `location_unique` is `NULLS NOT DISTINCT` over
        `(text_norm, admin_unit)`, so one text is one row and filling the link cannot collide
        with anything.
    """

    help = "Attach existing locations to their municipality, from the point already stored."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the dry run, because this rewrites rows the whole graph reads."""
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change, write nothing."
        )

    def handle(self, *args, **options) -> None:
        """Resolve a municipality per unlinked location and report what was reached."""
        pending = Location.objects.filter(
            admin_unit__isnull=True, point__isnull=False
        ).select_related("admin_unit")

        linked, unreached = 0, 0
        for location in pending:
            country = _country_of(location)
            unit = unit_for(location.point, country) if country else None
            if unit is None:
                unreached += 1
                continue
            if not options["dry_run"]:
                location.admin_unit = unit
                location.save(update_fields=["admin_unit"])
            linked += 1

        verb = "would link" if options["dry_run"] else "linked"
        style = self.style.SUCCESS if linked else self.style.WARNING
        self.stdout.write(style(f"{verb} {linked}, {unreached} with no unit within reach"))


def _country_of(location: Location) -> str | None:
    """
    The country to search, taken from an event that already points at this location.

    Returns:
        str | None: An ISO code, or None when nothing references the location — in which case
            there is no country to narrow by and it is left alone.
    """
    requirement = Requirement.objects.filter(location=location).select_related("event").first()
    if requirement is not None:
        return requirement.event.country_code
    actor = Actor.objects.filter(location=location).select_related("event").first()
    return actor.event.country_code if actor is not None else None
