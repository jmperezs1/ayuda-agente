"""The persisted graph: what the frontend reads, rebuilt only when its inputs change."""

from django.db import models


class GraphSnapshot(models.Model):
    """
    The event's graph, serialized once and served as-is.

    Recomputing the graph on every fetch wastes the exact same work a hundred times when
    nothing changed. The snapshot inverts it: writes (new requirements, agent actions,
    match updates) trigger a rebuild through signals, and reads are a single row.

    Note:
        `stale` is what separates *knowing* the graph is behind from *doing* something about
        it. Marking is one UPDATE, synchronous, and cannot fail; rebuilding is expensive and
        may be queued. Before this the two were the same act, so when the queue had no
        consumer nothing marked the snapshot either — and the read path, which only rebuilt
        when no snapshot existed at all, served a stale one forever.

        That is the failure it exists to prevent: an event whose summary reported 803
        requirements while its graph reported none, with neither endpoint wrong by its own
        logic.

        `input_fingerprint` is what makes the trigger cheap to over-fire. It hashes the
        graph's inputs, so a rebuild request that finds the stored fingerprint unchanged
        does nothing. Fifty redundant triggers cost fifty hash comparisons, not fifty
        matching passes — and the matches the pass itself writes are part of the stored
        fingerprint, which is what terminates the write→trigger→write loop.
    """

    event = models.OneToOneField(
        "radar.Event", on_delete=models.CASCADE, related_name="graph_snapshot"
    )
    payload = models.JSONField()
    input_fingerprint = models.CharField(max_length=64)
    stale = models.BooleanField(default=False)
    built_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"graph snapshot #{self.pk} @ {self.built_at:%H:%M:%S}"
