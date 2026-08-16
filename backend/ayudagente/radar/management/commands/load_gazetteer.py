"""Load a country's administrative divisions from GeoNames."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from ayudagente.radar.models import AdminUnit
from ayudagente.radar.services.gazetteer import GazetteerError, load_country


class Command(BaseCommand):
    """
    Populate `AdminUnit` for one country, so a sweep has real toponyms to query.

    Note:
        Idempotent, and safe to re-run when GeoNames publishes a new dump: units are matched
        on `(country, level, code)` and refreshed in place rather than duplicated.
    """

    help = "Download and load a country's ADM1 and ADM2 units from GeoNames."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the country to load."""
        parser.add_argument("country_code", help="ISO 3166-1 alpha-2, e.g. CO.")

    def handle(self, *args, **options) -> None:
        """
        Fetch the dump and store what it holds.

        Raises:
            CommandError: When the dump cannot be downloaded or read, naming the country so
                the message says which one to retry.
        """
        code = options["country_code"].upper()
        self.stdout.write(f"downloading the GeoNames dump for {code}")

        try:
            result = load_country(code)
        except GazetteerError as exc:
            raise CommandError(str(exc)) from exc

        total = AdminUnit.objects.filter(country_code=code).count()
        style = self.style.SUCCESS if total else self.style.WARNING
        self.stdout.write(
            style(
                f"  {result.created} created, {result.updated} updated, "
                f"{result.skipped} skipped — {total} units now loaded for {code}"
            )
        )
