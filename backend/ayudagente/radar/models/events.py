"""The event: the isolation boundary for the whole system."""

from django.contrib.gis.db import models as gis
from django.contrib.postgres.fields import ArrayField
from django.db import models

from ayudagente.radar.choices import EventStatus, HazardKind
from ayudagente.radar.models.catalog import AdminUnit


class Event(models.Model):
    """
    One concrete disaster, with its official ground truth.

    Almost the entire graph hangs off an event: actors, requirements and frontier nodes
    belong to exactly one and never mix across events. Two simultaneous emergencies mean
    two independent agents and two frontiers, sharing nothing but the geographic catalog.

    Note:
        `country_code` and `languages` are what let the same pipeline run anywhere. Three
        things read them: which toponyms anchor a query, which payment rails to look for in
        the text, and which languages to search and classify in.

        `lexicon` stores how people actually refer to the event — hashtags, nicknames — and,
        under `negatives`, the terms belonging to *other* concurrent emergencies. Those get
        injected into every query. It is the cheapest defense against conflating two
        disasters, and it lives as data rather than code so it can be tuned mid-event.

        `spent_usd` is observed, not enforced. Cost is recorded because Apify returns it for
        free and because a runaway loop should be visible, but nothing in the system decides
        based on it: what decides is whether a source produces useful content.

    See:
        `ayudagente.radar.models.frontier.FrontierNode` for the search state.
    """

    hazard = models.CharField(max_length=20, choices=HazardKind.choices)
    name = models.CharField(max_length=200)
    occurred_at = models.DateTimeField()
    epicenter = gis.PointField(geography=True, null=True, blank=True)
    magnitude = models.FloatField(null=True, blank=True)
    depth_km = models.FloatField(null=True, blank=True)

    country_code = models.CharField(max_length=2, db_index=True)  # ISO 3166-1 alpha-2
    languages = ArrayField(models.CharField(max_length=10), default=list)  # ISO 639-1

    detection_source = models.CharField(max_length=40)  # gdacs|usgs|emsc|national|manual
    external_id = models.CharField(max_length=80, blank=True)

    affected_units = models.ManyToManyField(AdminUnit, blank=True, related_name="events")
    lexicon = models.JSONField(default=dict)  # {hashtags: [], nicknames: [], negatives: []}

    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.ACTIVE
    )
    spent_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "occurred_at"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.country_code}, {self.occurred_at:%Y-%m-%d})"

    @property
    def is_harvestable(self) -> bool:
        """True when new harvest jobs may be scheduled for this event."""
        return self.status == EventStatus.ACTIVE
