"""Follow what the system is doing, in prose, on a screen somebody is watching."""

import time
from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from ayudagente.radar.choices import Direction, EventStatus, JobStatus
from ayudagente.radar.models import Event, HarvestJob, Match, Observation, Requirement

FINISHED = (JobStatus.DONE, JobStatus.EMPTY, JobStatus.FAILED, JobStatus.ACTOR_DOWN)

# Enough matches to show the shape of a round; past this a count reads better than a list
MATCH_SAMPLE = 3


class Command(BaseCommand):
    """
    Narrate the loop as it runs: what was detected, armed, harvested, read and proposed.

    Note:
        Reads watermarks rather than tailing the logs, so it needs no hook in the pipeline and
        survives a worker restarting mid-story. What it cannot do is recover the past: it
        starts from whatever the database already holds and narrates only what arrives after.

        Rows are summarised per poll rather than printed one by one. A pass that reads nine
        hundred posts is one sentence here and nine hundred lines in the log, and the whole
        point of this command is that somebody can follow it from across a room.

        Polling every second because the delay somebody notices is this one, and six counting
        queries a second cost nothing next to a screen that looks frozen. What paces the work
        itself is `TICK_SECONDS`, and no interval here can make a slow beat look busy.
    """

    help = "Narrate the loop in prose, for a demo screen."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the scope and the pace."""
        parser.add_argument("--event", type=int, help="Restrict to one event.")
        parser.add_argument("--interval", type=float, default=1.0, help="Seconds between polls.")
        parser.add_argument(
            "--once", action="store_true", help="Summarise what is already there, then exit."
        )

    def handle(self, *args, **options) -> None:
        """Poll until interrupted, saying whatever changed since the previous pass."""
        self.event_id = options["event"]

        if options["once"]:
            for line in self._changes(_from_nothing()):
                self.stdout.write(line)
            return

        marks = self._watermarks()
        self.stdout.write(self.style.HTTP_INFO("watching. ctrl-c to stop.\n"))
        try:
            while True:
                for line in self._changes(marks):
                    self.stdout.write(line)
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("\nstopped")

    def _watermarks(self) -> dict:
        """
        Where each stream stands right now, so the first pass narrates nothing old.

        Note:
            The opposite of `_from_nothing`, and the difference is the whole of `--once`.
            Following starts level with the database and reports what arrives; summarising
            starts from zero and reports what is already there.
        """
        return {
            "event": _high(self._events()),
            "status": dict(self._events().values_list("pk", "status")),
            "job": _high(self._jobs()),
            "observation": _high(self._observations()),
            "requirement": _high(self._requirements()),
            "match": _high(self._matches()),
        }

    def _changes(self, marks: dict) -> list[str]:
        """Every sentence this pass has to say, in the order the work actually happens."""
        return [
            *self._detected(marks),
            *self._armed(marks),
            *self._harvested(marks),
            *self._read(marks),
            *self._proposed(marks),
        ]

    def _detected(self, marks: dict) -> list[str]:
        """Emergencies that appeared, whether the watch stage proposed them or a human did."""
        fresh = self._events().filter(pk__gt=marks["event"]).order_by("pk")
        lines = []
        for event in fresh:
            marks["event"] = event.pk
            marks["status"][event.pk] = event.status
            waiting = "already armed" if event.is_harvestable else "waiting to be armed"
            lines.append(self._say("detected", f"{event.name} ({event.country_code}) — {waiting}"))
        return lines

    def _armed(self, marks: dict) -> list[str]:
        """Emergencies a human just gave permission to spend on."""
        lines = []
        for pk, status, name in self._events().values_list("pk", "status", "name"):
            was = marks["status"].get(pk)
            marks["status"][pk] = status
            if was == status or status != EventStatus.ACTIVE:
                continue
            lines.append(self._say("armed", f"{name} — a human authorised the spend"))
        return lines

    def _harvested(self, marks: dict) -> list[str]:
        """Harvest jobs that finished, one line each because they are few and each costs."""
        jobs = (
            self._jobs()
            .filter(pk__gt=marks["job"])
            .select_related("node", "node__admin_unit", "node__actor")
            .order_by("pk")
        )
        lines = []
        for job in jobs:
            marks["job"] = job.pk
            target = job.node or job.platform
            if job.status in (JobStatus.FAILED, JobStatus.ACTOR_DOWN):
                lines.append(self._say("failed", f"{target} — {job.status}"))
                continue
            lines.append(
                self._say(
                    "harvested",
                    f"{target} — {job.items_returned} posts, {job.items_new} of them new",
                )
            )
        return lines

    def _read(self, marks: dict) -> list[str]:
        """Posts turned into requirements, summarised: one pass can be nine hundred rows."""
        observations = self._observations().filter(pk__gt=marks["observation"])
        requirements = self._requirements().filter(pk__gt=marks["requirement"])

        posts = observations.count()
        needs = requirements.filter(direction=Direction.NEEDS).count()
        offers = requirements.filter(direction=Direction.OFFERS).count()
        if not (posts or needs or offers):
            return []

        marks["observation"] = max(marks["observation"], _high(observations))
        marks["requirement"] = max(marks["requirement"], _high(requirements))
        return [self._say("read", f"{posts} posts — {needs} needs, {offers} offers")]

    def _proposed(self, marks: dict) -> list[str]:
        """Matches the allocation pass drew, which is the output a coordinator acts on."""
        matches = (
            self._matches()
            .filter(pk__gt=marks["match"])
            .select_related("need__resource", "need__actor", "offer__actor")
            .order_by("pk")
        )
        shown = list(matches[:MATCH_SAMPLE])
        if not shown:
            return []

        total = matches.count()
        marks["match"] = _high(matches)
        lines = [
            self._say(
                "proposed",
                f"{match.need.resource.name}: {match.offer.actor.canonical_name} → "
                f"{match.need.actor.canonical_name}",
            )
            for match in shown
        ]
        if total > MATCH_SAMPLE:
            lines.append(self._say("proposed", f"and {total - MATCH_SAMPLE} more"))
        return lines

    def _say(self, verb: str, rest: str) -> str:
        """One sentence, with the verb in the left column so the eye can scan it."""
        colour = {
            "detected": self.style.HTTP_INFO,
            "armed": self.style.WARNING,
            "harvested": self.style.SUCCESS,
            "read": self.style.SUCCESS,
            "proposed": self.style.MIGRATE_HEADING,
            "failed": self.style.ERROR,
        }[verb]
        return f"  {colour(f'{verb:<10}')} {rest}"

    def _events(self):
        """The events in scope, which is one of them or all of them."""
        events = Event.objects.all()
        return events.filter(pk=self.event_id) if self.event_id else events

    def _jobs(self):
        """Finished harvest jobs in scope."""
        return self._scope(HarvestJob.objects.filter(status__in=FINISHED))

    def _observations(self):
        """Observations in scope."""
        return self._scope(Observation.objects.all())

    def _requirements(self):
        """Requirements in scope."""
        return self._scope(Requirement.objects.all())

    def _matches(self):
        """Matches in scope, reached through the need because a match has no event of its own."""
        return (
            Match.objects.filter(need__event_id=self.event_id)
            if self.event_id
            else Match.objects.all()
        )

    def _scope(self, queryset):
        """Narrow to the event under watch, when one was named."""
        return queryset.filter(event_id=self.event_id) if self.event_id else queryset


def _from_nothing() -> dict:
    """Watermarks that have seen nothing, so a pass narrates the whole database."""
    return {
        "event": 0,
        "status": {},
        "job": 0,
        "observation": 0,
        "requirement": 0,
        "match": 0,
    }


def _high(queryset) -> int:
    """
    The highest id a stream has reached.

    Returns:
        int: Zero when the stream is empty, so every comparison stays an ordinary `pk__gt`
            and no caller has to special-case a database with nothing in it.
    """
    return queryset.order_by("pk").values_list("pk", flat=True).last() or 0
