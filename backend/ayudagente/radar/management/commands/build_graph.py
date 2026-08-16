"""Run the matching pass so the event's graph gains its edges."""

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from ayudagente.radar.choices import EventStatus
from ayudagente.radar.models import Event
from ayudagente.radar.services import refresh_graph


class Command(BaseCommand):
    """
    The graph's nodes come from ingestion; its edges only exist after a matching pass.

    Note:
        Nothing else runs the pass today — no schedule, no endpoint — so a database can
        hold hundreds of requirements and zero matches. This command is the missing
        trigger, and the same entry point a Celery beat schedule should call later.
    """

    help = "Recompute proposed matches (graph edges) for one event or every active one."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--event",
            type=int,
            metavar="EVENT_ID",
            help="Restrict to one event. Defaults to every active event.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild even when the inputs are unchanged, after the payload shape changes.",
        )

    def handle(self, *args, **options):
        if options["event"] is not None:
            events = list(Event.objects.filter(id=options["event"]))
            if not events:
                raise CommandError(f"no event with id {options['event']}")
        else:
            events = list(Event.objects.filter(status=EventStatus.ACTIVE))
            if not events:
                raise CommandError("no active events")

        for event in events:
            snapshot, rebuilt = refresh_graph(event.id, force=options["force"])
            verb = "rebuilt" if rebuilt else "already current (inputs unchanged)"
            self.stdout.write(
                f"{event.name} (#{event.id}): {verb} — "
                f"{len(snapshot.payload['nodes'])} nodes, "
                f"{len(snapshot.payload['edges'])} matches proposed"
            )

        self.stdout.write(self.style.SUCCESS("Graph snapshots current."))
