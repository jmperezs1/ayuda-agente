"""
The triggers that keep the stored graph current.

Any write to the graph's inputs — actors, requirements, matches, whether from ingestion,
an agent tool or the admin — queues a rebuild after the transaction commits. The rebuild
itself is fingerprint-guarded, so redundant triggers are cheap by design; the two triggers
worth suppressing are the write storm the matching pass causes while a rebuild is already
running (the `rebuilding` flag), and the thousand-row seed transaction that would otherwise
queue a thousand identical rebuilds (the per-transaction dedupe below).
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from ayudagente.radar.models import Actor, Match, Requirement
from ayudagente.radar.services.graph import rebuilding

logger = logging.getLogger(__name__)


def _schedule_rebuild(event_id: int | None) -> None:
    """
    Queue one rebuild per event per transaction, however many rows the write touched.

    Args:
        event_id (int | None): Event whose graph went stale. None when the written row
            had no event, which is nothing to rebuild.

    Note:
        The dedupe reads the connection's own on-commit queue, so a bulk load (seeds,
        ingestion) enqueues once per event instead of once per row, and warns once rather
        than once per row when the broker is down. That queue is transaction-scoped and
        cleared on rollback, so nothing leaks between requests or between tests.
    """
    if event_id is None or rebuilding.get():
        return

    # Skip when this event already has a rebuild queued in this transaction
    connection = transaction.get_connection()
    for entry in connection.run_on_commit:
        func = entry[1]  # (savepoint_ids, func, robust) in current Django
        if getattr(func, "_graph_rebuild_event", None) == event_id:
            return

    def enqueue() -> None:
        """
        Mark the snapshot behind, then ask for a rebuild.

        Note:
            The order matters and the two are not the same act. Marking is synchronous and
            cannot fail; rebuilding is expensive and goes to a queue that a deployment may
            deliberately have no consumer for. Before the split, a broker with no worker meant
            nothing recorded that the graph was behind — and the read path, which rebuilt only
            when no snapshot existed, then served the stale one forever.
        """
        from ayudagente.radar.models import GraphSnapshot
        from ayudagente.radar.tasks import rebuild_graph

        # Marked in the database first: one UPDATE, and it cannot fail
        GraphSnapshot.objects.filter(event_id=event_id).update(stale=True)

        try:
            rebuild_graph.delay(event_id)  # type: ignore[attr-defined]  # celery stubs
        except Exception:  # broker down, or a deployment that runs no worker on purpose
            logger.info(
                "no worker to rebuild the graph for event %s; it is marked stale and the "
                "next read will rebuild it inline",
                event_id,
            )

    enqueue._graph_rebuild_event = event_id  # type: ignore[attr-defined]  # dedupe marker
    transaction.on_commit(enqueue)


@receiver(post_save, sender=Actor)
@receiver(post_delete, sender=Actor)
@receiver(post_save, sender=Requirement)
@receiver(post_delete, sender=Requirement)
def _on_node_change(sender, instance, **kwargs):
    _schedule_rebuild(instance.event_id)


@receiver(post_save, sender=Match)
@receiver(post_delete, sender=Match)
def _on_edge_change(sender, instance, **kwargs):
    event_id = (
        Requirement.objects.filter(id=instance.need_id).values_list("event_id", flat=True).first()
    )
    _schedule_rebuild(event_id)
