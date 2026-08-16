"""Compare what each way of harvesting produced."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from ayudagente.radar.models import Event
from ayudagente.radar.services.quality import Report, report

COLUMNS = (
    "{route:<10} {harvested:>7} {read:>6} {actionable:>11} "
    "{yield_:>7} {confirmed:>10} {contactable:>12} {cost:>9}"
)


class Command(BaseCommand):
    """
    Report what searching, reading replies and pulling profiles each produced.

    Note:
        The `confirmed` column is why this exists. A route can look productive by requirement
        count and be producing leads nothing corroborates, which is what a live sweep did: 117
        requirements of which 90% were a single uncorroborated post, because toponym searches
        surface press and aggregators rather than the people asking for help.

        Run it after a pipeline pass. Reading is what turns a harvested post into anything
        measurable, so a route whose posts are unread shows zeroes rather than a verdict.
    """

    help = "Compare the yield and the backing of each harvesting route."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare which event to measure."""
        parser.add_argument("event_id", type=int, nargs="?", help="Defaults to the active event.")

    def handle(self, *args, **options) -> None:
        """
        Print one row per route, then the totals.

        Raises:
            CommandError: When no event matches, or nothing has been harvested yet.
        """
        event = self._event(options["event_id"])
        measured = report(event)
        if not measured.routes:
            raise CommandError(f"nothing harvested for {event} yet")

        self.stdout.write(f"{measured.event}\n")
        self.stdout.write(
            COLUMNS.format(
                route="route",
                harvested="posts",
                read="read",
                actionable="actionable",
                yield_="req/100",
                confirmed="confirmed",
                contactable="contactable",
                cost="$/action",
            )
        )
        for route in measured.routes:
            self.stdout.write(self._row(route))

        self.stdout.write(self.style.SUCCESS(self._row(measured.total)))
        self._verdict(measured)

    def _row(self, route) -> str:
        """One route as a line."""
        return COLUMNS.format(
            route=route.route,
            harvested=route.harvested,
            read=route.read,
            actionable=f"{route.actionable_share:.0%}",
            yield_=f"{route.yield_per_hundred:.0f}",
            confirmed=f"{route.confirmed_share:.0%}",
            contactable=f"{route.contactable_share:.0%}",
            cost=f"${route.cost_per_actionable:.4f}",
        )

    def _verdict(self, measured: Report) -> None:
        """
        Say which route backs its findings best, when there is more than one to compare.

        Note:
            Compared on `confirmed`, not on volume. A route that produces twice the rows and
            corroborates none of them is producing work for somebody, not answers.
        """
        readable = [route for route in measured.routes if route.requirements]
        if len(readable) < 2:
            return

        best = max(readable, key=lambda route: route.confirmed_share)
        worst = min(readable, key=lambda route: route.confirmed_share)
        if best.route == worst.route:
            return

        self.stdout.write(
            f"\n{best.route} backs {best.confirmed_share:.0%} of what it finds against "
            f"{worst.confirmed_share:.0%} for {worst.route}."
        )

    def _event(self, event_id: int | None) -> Event:
        """Resolve the event, defaulting to the only active one."""
        if event_id is not None:
            event = Event.objects.filter(pk=event_id).first()
            if event is None:
                raise CommandError(f"no event {event_id}")
            return event

        active = list(Event.objects.filter(status="active")[:2])
        if len(active) != 1:
            raise CommandError("name the event: several are active, or none is")
        return active[0]
