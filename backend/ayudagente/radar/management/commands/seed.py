"""The single entry point for putting seed data into the database, and for taking it out."""

from argparse import ArgumentParser
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ayudagente.radar.models import Event
from ayudagente.radar.seeds import SEEDS
from ayudagente.radar.seeds.base import Counts, Seed


class Command(BaseCommand):
    """
    The one way data gets seeded, so datasets do not each grow their own command.

    Note:
        A dataset is registered in `ayudagente.radar.seeds`, not here. Adding a scenario is
        a new module in that package; this command never changes.
    """

    help = "Load or clear the seed datasets. Loading is idempotent."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags. Clearing and reloading are mutually exclusive by design."""
        parser.add_argument(
            "--only",
            nargs="+",
            choices=sorted(SEEDS),
            metavar="DATASET",
            help="Restrict to these datasets. Defaults to all of them.",
        )
        parser.add_argument("--list", action="store_true", help="Show what can be loaded.")

        mode = parser.add_mutually_exclusive_group()  # `--clear --flush` has no meaning
        mode.add_argument(
            "--clear", action="store_true", help="Delete the datasets instead of loading them."
        )
        mode.add_argument(
            "--flush",
            action="store_true",
            help="Delete first, then load. Use it to get back to a known state.",
        )

    def handle(self, *args, **options) -> None:
        """
        Load or clear the selected datasets.

        Raises:
            CommandError: If no dataset is registered, or a loader fails.

        Note:
            Each dataset runs in its own transaction, so a failure loading the second one
            does not undo the first.

            Clearing walks the registry backwards, and that is not cosmetic. The catalog
            loads first because the scenarios reference it, so it can only be removed once
            they are gone — clearing forwards leaves every resource type that was still in
            use at the moment its turn came, and they are orphans by the time the run ends.
        """
        if options["list"]:
            for seed in SEEDS.values():
                self.stdout.write(f"  {seed.name:<10} {seed.description}")
            return

        # Registry order, not alphabetical: a catalog has to load before what references it
        selected = [SEEDS[name] for name in (options["only"] or SEEDS)]
        if not selected:
            raise CommandError("no datasets are registered")

        if options["clear"] or options["flush"]:
            for seed in reversed(selected):
                # One transaction per dataset: a failure in the second must not undo the first
                with transaction.atomic():
                    self.stdout.write(f"clearing {seed.name}: {self._clear(seed)} rows")

        if not options["clear"]:
            for seed in selected:
                with transaction.atomic():
                    self._load(seed)

    def _clear(self, seed: Seed) -> int:
        """
        Delete a dataset by removing the events it owns; everything else cascades.

        Args:
            seed (Seed): Dataset to remove.

        Returns:
            int: Rows removed across every cascaded table.

        Note:
            A seed that also creates catalog rows supplies its own `clear`, since those sit
            outside the event and a cascade would leave them behind.
        """
        if seed.clear is not None:
            return seed.clear(self.stdout.write)
        deleted, _ = Event.objects.filter(name__in=seed.event_names).delete()
        return deleted

    def _load(self, seed: Seed) -> Counts:
        """
        Run one dataset's loader and report what it created.

        Args:
            seed (Seed): Dataset to load.

        Returns:
            Counts: What the loader created, all zeros when everything already existed.

        Raises:
            CommandError: If the loader fails, with the dataset named so the message is
                actionable rather than a bare traceback.
        """
        self.stdout.write(f"\nloading {seed.name}")
        try:
            counts = seed.load(self.stdout.write)
        except FileNotFoundError as exc:
            raise CommandError(f"{seed.name}: missing data file — {exc}") from exc
        except Exception as exc:
            raise CommandError(f"{seed.name}: {exc}") from exc

        total = sum(counts.values())
        summary = ", ".join(f"{count} {kind}" for kind, count in counts.items())
        style = self.style.SUCCESS if total else self.style.WARNING
        self.stdout.write(style(f"  {summary or 'nothing to load'}"))
        return Counter(counts)
