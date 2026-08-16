"""
Measuring what each way of harvesting actually produced.

The system can harvest three ways — sweep a place, pull the replies under a post, read one
account's timeline — and until now nothing compared them. That left the most consequential
decision in the loop unanswered: a live sweep found 117 requirements of which 90% were single
uncorroborated reports, and the diagnosis was that toponym searches return what *ranks*, which
is press and aggregators rather than the people asking for help. That was a hypothesis. This
is how it gets tested.

Note:
    Deliberately not a score. Every column is a count or a ratio a person can check by hand,
    because the number that matters here decides where money goes and nobody should have to
    trust a weighted average to make that call.

    Yield is per hundred *posts read*, not per post harvested. A harvest that returns two
    hundred duplicates read as nothing did not fail at finding, it failed at finding anything
    new, and `items_new` already measures that.
"""

from dataclasses import dataclass, field

from django.db.models import Count, Q, Sum

from ayudagente.radar.choices import ExtractionClass, HarvestTarget, RequirementStatus
from ayudagente.radar.models import Event, HarvestJob, Observation, Requirement

ACTIONABLE = (ExtractionClass.NEED, ExtractionClass.OFFER, ExtractionClass.BOTH)

# What a harvest job was reaching for, in the terms this report compares
ROUTES = {
    HarvestTarget.SEARCH: "search",
    HarvestTarget.COMMENTS: "comments",
    HarvestTarget.PROFILE: "profile",
    HarvestTarget.THREAD: "thread",
}


@dataclass
class RouteReport:
    """
    What one way of harvesting produced.

    Attributes:
        route (str): `search`, `comments`, `profile` or `thread`.
        harvested (int): Observations this route brought in.
        read (int): How many of those the pipeline has read.
        actionable (int): Readings that were a need or an offer rather than a discard.
        requirements (int): Rows produced from them.
        confirmed (int): Requirements something corroborated.
        contactable (int): Requirements whose actor has a way to be reached.
        cost_usd (float): What the harvesting cost. Model calls are not included — they are
            the same per post whichever route brought it.
    """

    route: str
    harvested: int = 0
    read: int = 0
    actionable: int = 0
    requirements: int = 0
    confirmed: int = 0
    contactable: int = 0
    cost_usd: float = 0.0

    @property
    def actionable_share(self) -> float:
        """Share of read posts that said somebody needs or offers something."""
        return self.actionable / self.read if self.read else 0.0

    @property
    def yield_per_hundred(self) -> float:
        """Requirements produced per hundred posts read."""
        return 100 * self.requirements / self.read if self.read else 0.0

    @property
    def confirmed_share(self) -> float:
        """
        Share of requirements something backs.

        Note:
            The column the whole report exists for. A route that produces many requirements
            nothing corroborates is producing leads, not answers.
        """
        return self.confirmed / self.requirements if self.requirements else 0.0

    @property
    def contactable_share(self) -> float:
        """Share whose actor can actually be reached — a need nobody can answer is inert."""
        return self.contactable / self.requirements if self.requirements else 0.0

    @property
    def cost_per_actionable(self) -> float:
        """What one actionable post cost to harvest."""
        return self.cost_usd / self.actionable if self.actionable else 0.0


@dataclass
class Report:
    """Every route of one event, plus the totals."""

    event: str
    routes: list[RouteReport] = field(default_factory=list)

    @property
    def total(self) -> RouteReport:
        """All routes summed, for the line that says where the event as a whole stands."""
        combined = RouteReport(route="all")
        for route in self.routes:
            combined.harvested += route.harvested
            combined.read += route.read
            combined.actionable += route.actionable
            combined.requirements += route.requirements
            combined.confirmed += route.confirmed
            combined.contactable += route.contactable
            combined.cost_usd += route.cost_usd
        return combined


def report(event: Event) -> Report:
    """
    Compare what each harvesting route produced for one event.

    Args:
        event (Event): The emergency to measure.

    Returns:
        Report: One row per route that brought in at least one post.

    Note:
        Observations with no job are excluded rather than bucketed. They came from a seed or
        were written by hand, and counting them would credit a route that did no work.
    """
    result = Report(event=str(event))

    for target, name in ROUTES.items():
        jobs = HarvestJob.objects.filter(event=event, target_kind=target)
        if not jobs.exists():
            continue

        observations = Observation.objects.filter(event=event, job__in=jobs)
        harvested = observations.count()
        if not harvested:
            continue

        counted = observations.aggregate(
            read=Count("extraction"),
            actionable=Count("extraction", filter=Q(extraction__classification__in=ACTIONABLE)),
        )
        requirements = Requirement.objects.filter(evidence__in=observations).distinct()
        rows = requirements.aggregate(
            total=Count("id", distinct=True),
            confirmed=Count("id", distinct=True, filter=~Q(status=RequirementStatus.UNVERIFIED)),
            contactable=Count("id", distinct=True, filter=Q(actor__contact_points__isnull=False)),
        )

        result.routes.append(
            RouteReport(
                route=name,
                harvested=harvested,
                read=counted["read"] or 0,
                actionable=counted["actionable"] or 0,
                requirements=rows["total"] or 0,
                confirmed=rows["confirmed"] or 0,
                contactable=rows["contactable"] or 0,
                cost_usd=float(jobs.aggregate(spent=Sum("actual_cost_usd"))["spent"] or 0),
            )
        )

    return result
