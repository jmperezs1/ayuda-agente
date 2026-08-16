"""Run the observation pipeline, here or on the queue."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from ayudagente.radar.models import Event
from ayudagente.radar.services import refresh_graph
from ayudagente.radar.tasks import (
    extraction_cost_estimate,
    pending_observations,
    process_observation,
)


class Command(BaseCommand):
    """
    Read an event's posts and turn them into requirements.

    Note:
        Runs inline by default so the pipeline can be exercised without a worker, which is
        what makes a first pass over real posts a single command rather than a deployment.
        `--queue` hands the same work to Celery for a real harvest.

        It refuses to start a large run without showing the projected token spend first.
        Reading a corpus is the most expensive thing the system does, and discovering that
        after the fact is not a discovery worth having.
    """

    help = "Extract, geocode, resolve and materialise an event's observations."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags, which choose how much to read and where to run it."""
        parser.add_argument("event_id", type=int, nargs="?", help="Defaults to the active event.")
        parser.add_argument("--limit", type=int, help="Read at most this many posts.")
        parser.add_argument(
            "--workers", type=int, default=16, help="Inline concurrency. Ignored with --queue."
        )
        parser.add_argument("--force", action="store_true", help="Re-read posts already read.")
        parser.add_argument("--queue", action="store_true", help="Hand the work to Celery.")
        parser.add_argument("--yes", action="store_true", help="Skip the cost confirmation.")

    def handle(self, *args, **options) -> None:
        """
        Read what is pending, inline or queued.

        Raises:
            CommandError: If no event matches, or nothing is pending.
        """
        event = self._event(options["event_id"])
        pending = pending_observations(event.pk, force=options["force"])
        if options["limit"]:
            pending = pending[: options["limit"]]

        ids = list(pending.values_list("pk", flat=True))
        if not ids:
            raise CommandError("nothing pending; use --force to re-read")

        self._announce(event, ids, options)
        if not options["yes"] and not self._confirmed(len(ids)):
            self.stdout.write("cancelled")
            return

        if options["queue"]:
            for observation_id in ids:
                process_observation.delay(  # type: ignore[attr-defined]  # celery stubs
                    observation_id, force=options["force"]
                )
            self.stdout.write(self.style.SUCCESS(f"queued {len(ids)} observations"))
            return

        self._run_inline(event, ids, options)

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

    def _announce(self, event: Event, ids: list[int], options: dict) -> None:
        """Say what is about to be read and what it is projected to cost."""
        self.stdout.write(f"{event}")
        self.stdout.write(f"  {len(ids)} observations to read")
        tokens_in, tokens_out = extraction_cost_estimate(event.pk, len(ids))
        if tokens_in:
            self.stdout.write(f"  projected ~{tokens_in:,} input and ~{tokens_out:,} output tokens")
        else:
            self.stdout.write("  no reading measured yet, so no projection")

    def _confirmed(self, count: int) -> bool:
        """Ask before a run large enough to be worth a second thought."""
        if count <= 20:
            return True
        answer = input(f"read {count} observations? [y/N] ").strip().casefold()
        return answer in {"y", "yes"}

    def _run_inline(self, event: Event, ids: list[int], options: dict) -> None:
        """
        Process here, several at a time.

        Note:
            Each worker closes its database connections on the way out. A thread that ends
            holding one leaks it for the life of the process, which on a long run is how a
            command exhausts the connection pool of the database it is writing to.
        """
        totals = {"requirements": 0, "dropped": 0, "failed": 0}

        def work(observation_id: int) -> dict:
            try:
                return process_observation(observation_id, force=options["force"])
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            futures = {pool.submit(work, oid): oid for oid in ids}
            for done, future in enumerate(as_completed(futures), start=1):
                try:
                    result = future.result()
                except Exception as exc:
                    totals["failed"] += 1
                    self.stdout.write(self.style.ERROR(f"  {futures[future]}: {exc}"))
                    continue

                totals["requirements"] += result.get("requirements", 0)
                totals["dropped"] += len(result.get("dropped", []))
                if done % 25 == 0 or done == len(ids):
                    self.stdout.write(
                        f"  {done}/{len(ids)} · {totals['requirements']} requirements · "
                        f"{totals['failed']} failed"
                    )

        style = self.style.SUCCESS if not totals["failed"] else self.style.WARNING
        self.stdout.write(
            style(
                f"\n{totals['requirements']} requirements created, "
                f"{totals['dropped']} items dropped, {totals['failed']} observations failed"
            )
        )
        self._rebuild_graph(event)

    def _rebuild_graph(self, event: Event) -> None:
        """
        Rebuild the graph before returning, because this run just invalidated it.

        Note:
            An inline run is the one path that knows it changed everything and has nobody to
            tell. It writes requirements without a worker to pick up the rebuild, so leaving
            it queued means the first reader pays for it — or, when no worker exists at all,
            that the graph stays behind until somebody notices.
        """
        snapshot, _rebuilt = refresh_graph(event.pk, force=True)
        self.stdout.write(
            f"graph: {len(snapshot.payload['nodes'])} nodes, "
            f"{len(snapshot.payload['edges'])} matches proposed"
        )
