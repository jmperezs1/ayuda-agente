"""List every event and say what each one is allowed to do."""

from argparse import ArgumentParser
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import Event
from ayudagente.radar.services.pacing import total_spent

ROW = "{pk:>4}  {status:<9} {harvest:<9} {occurred:<16} {needs:>6} {offers:>7} {spent:>9}  {name}"


class Command(BaseCommand):
    """
    Show the events, newest first, with the one column that matters most: whether each is
    allowed to spend.

    Note:
        `harvest` is `Event.is_harvestable`, printed rather than inferred from the status.
        The whole cost control rests on that flag, and a list that showed the status alone
        would make a reader derive it — which is how a paused event gets assumed to be armed.
    """

    help = "List events with their status, harvest permission and totals."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the filter for the common case of looking at live work only."""
        parser.add_argument(
            "--active", action="store_true", help="Only the events currently being harvested."
        )

    def handle(self, *args, **options) -> None:
        """Print one row per event, then how to arm whatever is waiting."""
        events = Event.objects.order_by("-occurred_at")
        if options["active"]:
            events = events.filter(status=EventStatus.ACTIVE)

        events = events.annotate(
            needs=Count("requirements", filter=Q(requirements__direction="needs"), distinct=True),
            offers=Count("requirements", filter=Q(requirements__direction="offers"), distinct=True),
        )
        if not events:
            self.stdout.write("no events yet — try `make watch`")
            return

        self.stdout.write(
            ROW.format(
                pk="id",
                status="status",
                harvest="harvest",
                occurred="occurred",
                needs="needs",
                offers="offers",
                spent="spent",
                name="name",
            )
        )
        for event in events:
            self.stdout.write(self._row(event))

        self._spend()
        self._waiting()

    def _row(self, event: Event) -> str:
        """One event as a line, coloured by whether it may spend."""
        line = ROW.format(
            pk=event.pk,
            status=event.status,
            harvest="yes" if event.is_harvestable else "no",
            occurred=f"{event.occurred_at:%Y-%m-%d %H:%M}",
            needs=event.needs,  # type: ignore[attr-defined]
            offers=event.offers,  # type: ignore[attr-defined]
            spent=f"${event.spent_usd}",
            name=event.name,
        )
        return self.style.SUCCESS(line) if event.is_harvestable else line

    def _spend(self) -> None:
        """
        Total spend against the global breaker, and whether it is currently blocking.

        Note:
            Printed because the global ceiling refuses at the gate instead of pausing, so a
            blocked system still shows every event as harvestable. Without this line the
            listing would say yes while nothing harvests, which is the shape of the failure
            this project has already paid for once.
        """
        ceiling = Decimal(str(settings.HARVEST_SPEND_TOTAL_CEILING_USD))
        spent = total_spent()
        if ceiling <= 0:
            self.stdout.write(f"\nspent ${spent} in total, no global ceiling set")
            return

        line = f"\nspent ${spent} of a ${ceiling} global ceiling"
        if spent >= ceiling:
            self.stdout.write(
                self.style.ERROR(f"{line} — BLOCKED, nothing will harvest until it is raised")
            )
            self.stdout.write("raise it with:  make prod.ceiling USD=<amount>")
            return
        self.stdout.write(line)

    def _waiting(self) -> None:
        """Say how to arm a candidate, when there is one to arm."""
        candidate = Event.objects.filter(status=EventStatus.PAUSED).order_by("-occurred_at").first()
        if candidate is not None:
            self.stdout.write(f"\narm one with:  make arm ARGS='{candidate.pk}'")
