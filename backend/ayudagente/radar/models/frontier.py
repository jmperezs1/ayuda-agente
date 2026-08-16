"""The scoreboard the frontier agent reads, and nothing else."""

from django.db import models

from ayudagente.radar.choices import NodeStatus, Platform, Zone
from ayudagente.radar.models.actors import Actor


class FrontierNode(models.Model):
    """
    One watch target — a place or an account on a platform — with its running quality record.

    This is the only table the agent ever reads. A few dozen rows, a couple of thousand
    tokens, and it never sees a single post. Reading a scoreboard and deciding where to look
    next is judgment, which is what a model is good at; extracting a phone number from a
    caption is a function, which it is not.

    Note:
        A node watches either an `admin_unit` or an `actor`, never both and never neither.
        Places are the broad sweep; accounts are what you follow once one turns out to be a
        real coordinator, and that is the pass worth deciding about. Both compete for the
        same attention, so they share one table.

        The decision signal is **quality, not cost**. Sweeping is cheap once toponyms are
        batched into one query, so there is no budget to optimize — what matters is whether
        a target produces actionable content. A node that yields nothing across several
        passes goes to `exhausted`; nothing here divides by price.

        Cadence is not stored. The scheduler derives it from `yield_rate` and
        `last_useful_find_at` at the moment it runs, so there is no field to keep in sync.

        `is_unexplored` supports forced exploration. A fixed share of every pass must go to
        targets with no history, or the agent converges onto what it already knows and never
        finds the rural district nobody was posting about twenty minutes ago — precisely the
        highest-value case.
    """

    event = models.ForeignKey("radar.Event", on_delete=models.CASCADE, related_name="frontier")
    admin_unit = models.ForeignKey(
        "radar.AdminUnit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="frontier_nodes",
    )
    actor = models.ForeignKey(
        Actor,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="frontier_nodes",
    )
    platform = models.CharField(max_length=20, choices=Platform.choices)
    zone = models.CharField(max_length=10, choices=Zone.choices)

    yield_rate = models.FloatField(default=0, db_index=True)  # actionable items per 100
    avg_credibility = models.FloatField(default=0.5)
    distance_km = models.FloatField(null=True, blank=True)  # cold-start prior, before yield

    last_harvest_at = models.DateTimeField(null=True, blank=True)
    last_useful_find_at = models.DateTimeField(null=True, blank=True)

    passes = models.IntegerField(default=0)
    total_items = models.IntegerField(default=0)
    actionable_items = models.IntegerField(default=0)
    observed_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    status = models.CharField(max_length=20, choices=NodeStatus.choices, default=NodeStatus.ACTIVE)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "admin_unit", "platform"],
                condition=models.Q(actor__isnull=True),
                name="frontier_place_unique",
            ),
            models.UniqueConstraint(
                fields=["event", "actor", "platform"],
                condition=models.Q(admin_unit__isnull=True),
                name="frontier_account_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(admin_unit__isnull=False, actor__isnull=True)
                    | models.Q(admin_unit__isnull=True, actor__isnull=False)
                ),
                name="frontier_node_watches_exactly_one",
            ),
        ]
        indexes = [models.Index(fields=["event", "status", "-yield_rate"])]

    def __str__(self) -> str:
        target = self.admin_unit or self.actor
        return f"{target} · {self.platform}"

    @property
    def is_unexplored(self) -> bool:
        """True when this target has never been harvested and has no quality record yet."""
        return self.passes == 0

    @property
    def watches_account(self) -> bool:
        """True when this node follows one account rather than sweeping a place."""
        return self.actor_id is not None

    def refresh_yield_rate(self) -> float:
        """
        Recompute the quality signal from the running counters.

        Returns:
            float: Actionable items per 100 harvested, or 0 before anything was collected.
        """
        self.yield_rate = 100 * self.actionable_items / self.total_items if self.total_items else 0
        return self.yield_rate
