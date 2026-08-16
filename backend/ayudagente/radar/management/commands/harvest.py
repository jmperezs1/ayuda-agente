"""Run the harvest jobs the frontier agent queued."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from ayudagente.radar.choices import JobStatus
from ayudagente.radar.models import Event, HarvestJob
from ayudagente.radar.services.harvest import HarvestNotConfigured, run_harvest_job
from ayudagente.radar.tasks import process_observation


class Command(BaseCommand):
    """
    Execute pending harvest jobs and read what comes back.

    Note:
        Runs inline by default so the loop can be exercised without a worker, which is what
        makes a first live harvest one command rather than a deployment. `--queue` hands the
        same work to Celery, which is how it runs unattended.

        This spends real Apify credit, so it lists what it is about to run and asks first.
    """

    help = "Run the pending harvest jobs for an event."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the flags, which choose how much to run and where."""
        parser.add_argument("event_id", type=int, nargs="?", help="Defaults to the active event.")
        parser.add_argument("--limit", type=int, help="Run at most this many jobs.")
        parser.add_argument("--queue", action="store_true", help="Hand the work to Celery.")
        parser.add_argument(
            "--workers", type=int, default=4, help="Concurrent runs. Ignored with --queue."
        )
        parser.add_argument(
            "--pipeline",
            action="store_true",
            help="Also read the harvested posts. Costs model calls on top of the scraping.",
        )
        parser.add_argument("--yes", action="store_true", help="Skip the confirmation.")

    def handle(self, *args, **options) -> None:
        """
        Run what is pending.

        Raises:
            CommandError: If no event matches, nothing is pending, or Apify is unconfigured.
        """
        event = self._event(options["event_id"])
        jobs = list(
            HarvestJob.objects.filter(event=event, status=JobStatus.PENDING)
            .select_related("node__admin_unit", "node__actor")
            .order_by("created_at")[: options["limit"] or None]
        )
        if not jobs:
            raise CommandError(f"no pending jobs for {event}; run the frontier agent first")

        self._announce(event, jobs)
        if not options["yes"] and not self._confirmed(len(jobs)):
            self.stdout.write("cancelled")
            return

        if options["queue"]:
            from ayudagente.radar.tasks import harvest

            for job in jobs:
                harvest.delay(job.pk)  # type: ignore[attr-defined]
            self.stdout.write(self.style.SUCCESS(f"queued {len(jobs)} harvest jobs"))
            return

        self._run_inline(jobs, options)

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

    def _announce(self, event: Event, jobs: list[HarvestJob]) -> None:
        """Say what is about to be scraped, and why the agent chose it."""
        self.stdout.write(f"{event}")
        for job in jobs:
            target = job.node or "manual"
            self.stdout.write(f"  {job.platform:<10} {job.target_kind:<9} {target}")
            self.stdout.write(f"             {job.rationale}")

    def _confirmed(self, count: int) -> bool:
        """Ask before spending Apify credit."""
        answer = input(f"run {count} harvest jobs against Apify? [y/N] ").strip().casefold()
        return answer in {"y", "yes"}

    def _run_inline(self, jobs: list[HarvestJob], options: dict) -> None:
        """
        Run the jobs concurrently, reporting each as it lands.

        Note:
            This used to be sequential, on the claim that Apify rate limits per account so
            parallelism bought little. Measured, that was wrong: eight jobs took six minutes
            of which the slowest was ninety-six seconds, and the rest was waiting. Apify runs
            are independent and bounded by the account's memory, not by a run rate.

            Each worker closes its database connections on the way out. A thread that ends
            holding one leaks it for the life of the process, which on a long run exhausts the
            pool of the database it is writing to.
        """
        totals = {"returned": 0, "new": 0, "failed": 0}

        def work(job: HarvestJob):
            try:
                return run_harvest_job(job.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=options["workers"]) as pool:
            futures = {pool.submit(work, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except HarvestNotConfigured as exc:
                    raise CommandError(str(exc)) from exc
                except Exception as exc:
                    totals["failed"] += 1
                    self.stdout.write(self.style.ERROR(f"  job {job.pk}: {exc}"))
                    continue

                totals["returned"] += result.items_returned
                totals["new"] += result.items_new
                job.refresh_from_db()
                self.stdout.write(
                    f"  job {job.pk}: {result.items_returned} items, "
                    f"{result.items_new} new, {job.status}"
                )

                if options["pipeline"]:
                    for observation_id in result.observation_ids:
                        process_observation(observation_id)

        style = self.style.SUCCESS if not totals["failed"] else self.style.WARNING
        self.stdout.write(
            style(
                f"\n{totals['returned']} items returned, {totals['new']} new observations, "
                f"{totals['failed']} jobs failed"
            )
        )
