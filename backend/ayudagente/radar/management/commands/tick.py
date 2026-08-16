"""Run one beat of the perpetual loop now, instead of waiting for the scheduler."""

from django.core.management.base import BaseCommand

from ayudagente.radar.tasks import run_tick


class Command(BaseCommand):
    """
    Fire one beat by hand: dispatch what is queued, pull comments, let the frontier decide.

    Note:
        Beat runs its first tick a whole `TICK_SECONDS` after it starts rather than on
        startup, so a demonstration that opens by bringing the workers up shows nothing at
        all until that interval has passed. This is the same work, on demand.

        Dispatched through Celery exactly as the scheduled beat is, so a worker has to be up
        or the jobs are only queued. Nothing here bypasses the spend ceilings.
    """

    help = "Run one beat of the perpetual loop immediately."

    def handle(self, *args, **options) -> None:
        """Beat once and say what each active event decided."""
        outcome = run_tick()
        if not outcome["events"]:
            self.stdout.write("no active events; arm one first")
            return

        for event_id, result in outcome["events"].items():
            verb = "ran" if result.get("ran") else "waited"
            queued = result.get("queued")
            detail = f", queued {queued} jobs" if queued else ""
            self.stdout.write(f"  event {event_id}: {verb} — {result['reason']}{detail}")
