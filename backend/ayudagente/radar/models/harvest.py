"""
Evidence layer: what we asked for, what came back, and what the model understood.

The three central tables — job, observation, extraction — are deliberately separate so
interpretation never overwrites proof. When the prompt improves, we re-extract over the
same observations without re-scraping anything.
"""

from django.contrib.gis.db import models as gis
from django.contrib.postgres.fields import ArrayField
from django.db import models

from ayudagente.radar.choices import (
    DecisionSource,
    ExtractionClass,
    HarvestTarget,
    JobStatus,
    MediaKind,
    Platform,
)


class HarvestJob(models.Model):
    """
    A harvesting decision turned into an executable, auditable record.

    This is the only artifact the frontier agent produces. The agent decides and writes the
    job; a worker executes it against Apify. That split is what lets a failed harvest be
    retried without invoking the model again.

    Note:
        `target_kind` separates the cheap pass from the expensive one. A keyword search over
        a place costs cents because toponyms are batched into one query; pulling an account's
        whole timeline, a thread or a post's comments is the deep pass, and that is the only
        allocation decision worth making.

        `rationale` stores, in plain text, why the agent picked this target. It is not needed
        to run: it is needed to debug, and to show the agent's reasoning on the dashboard.

        The `actor_down` status exists because an Apify Actor can return success with zero
        results while actually being broken. Mistaking that for "no signal here" makes the
        frontier penalize a place that did have information.
    """

    event = models.ForeignKey("radar.Event", on_delete=models.CASCADE, related_name="jobs")
    node = models.ForeignKey(
        "radar.FrontierNode",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
    )

    platform = models.CharField(max_length=20, choices=Platform.choices)
    target_kind = models.CharField(
        max_length=20, choices=HarvestTarget.choices, default=HarvestTarget.SEARCH
    )
    apify_actor = models.CharField(max_length=120)
    actor_input = models.JSONField()  # exact payload sent, so a run can be reproduced

    decided_by = models.CharField(max_length=20, choices=DecisionSource.choices)
    rationale = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=JobStatus.choices, default=JobStatus.PENDING)
    run_id = models.CharField(max_length=40, blank=True)
    dataset_id = models.CharField(max_length=40, blank=True)
    items_returned = models.IntegerField(default=0)
    items_new = models.IntegerField(default=0)  # after deduplicating against what we hold
    actual_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["event", "status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.platform} · {self.apify_actor} · {self.status}"


class Observation(models.Model):
    """
    A post or comment exactly as the platform returned it. Immutable.

    Never edited, never deleted: when someone posts "we no longer need water", that arrives
    as a new observation that changes a requirement's status, not as a correction of the
    earlier one. That preserves history and lets us explain why an item was closed.

    Note:
        The author snapshot lives here rather than on `Actor` because profiles change and
        we want the state at posting time. Which fields arrive depends on the platform: X
        and TikTok give follower counts and verification, Instagram and Facebook give no
        follower count, and Instagram gives no avatar either. Every author field is
        optional on purpose.

        `platform_geo` is almost always empty — from 0% on Facebook to 34% on Instagram —
        so the real location is extracted from the text and geocoded separately.

    See:
        `Extraction` for the interpretation, `Media` for downloaded imagery.
    """

    event = models.ForeignKey("radar.Event", on_delete=models.CASCADE, related_name="observations")
    job = models.ForeignKey(
        HarvestJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="observations",
    )

    platform = models.CharField(max_length=20, choices=Platform.choices)
    platform_id = models.CharField(max_length=80)
    permalink = models.URLField(max_length=500)  # unlike media URLs, this does not expire
    posted_at = models.DateTimeField(db_index=True)

    text = models.TextField(blank=True)
    transcript = models.TextField(blank=True)  # TikTok subtitles, already cleaned up
    language = models.CharField(max_length=10, blank=True)

    author_handle = models.CharField(max_length=120, blank=True)
    author_name = models.CharField(max_length=200, blank=True)
    author_platform_id = models.CharField(max_length=80, blank=True)
    # Avatar URLs are signed and run long, past what a 500-char column holds
    author_avatar_url = models.URLField(max_length=1000, blank=True)
    author_followers = models.IntegerField(null=True, blank=True)
    author_verified = models.BooleanField(null=True, blank=True)
    author_bio = models.TextField(blank=True)

    platform_geo = gis.PointField(geography=True, null=True, blank=True)
    platform_geo_name = models.CharField(max_length=200, blank=True)

    thread_id = models.CharField(max_length=80, blank=True)  # lets us pull the whole thread
    reply_to_id = models.CharField(max_length=80, blank=True)
    # True for anything that is not a top-level post; `reply_to_id` says what it hangs under
    is_reply = models.BooleanField(default=False)

    hashtags = ArrayField(models.CharField(max_length=80), default=list, blank=True)
    mentions = ArrayField(models.CharField(max_length=120), default=list, blank=True)
    external_links = ArrayField(models.URLField(max_length=500), default=list, blank=True)

    metrics = models.JSONField(default=dict)  # {likes, shares, comments, views}
    raw = models.JSONField()
    harvested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["platform", "platform_id"], name="observation_unique")
        ]
        indexes = [
            models.Index(fields=["event", "posted_at"]),
            models.Index(fields=["thread_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.platform}:{self.platform_id} @{self.author_handle}"


class Media(models.Model):
    """
    An image, video or frame attached to an observation, plus our own permanent copy.

    Platform media URLs are signed and expire within hours or days, so storing only the
    link leaves the frontend with broken images and the model with nothing to re-evaluate.
    Hence `blob_path`: our own copy, stored under `MEDIA_ROOT` rather than in Postgres,
    because hundreds of megabytes of bytes belong in a filesystem and not in every backup.

    Note:
        For videos we keep the frames the vision model actually looked at, not the full
        file. That keeps audits reproducible — you can open exactly what it saw — without
        paying to store the original.

        `platform_alt_text` is text the platform gives away for free. Facebook returns it
        on 100% of posts that carry media and it transcribes the text on the flyer, which
        often removes the need to call the vision model at all.

        `sha256` catches the same photo recycled across posts and across events. It is the
        cheapest defense we have against image-based misinformation.
    """

    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=20, choices=MediaKind.choices)

    source_url = models.URLField(max_length=1000)
    blob_path = models.CharField(max_length=400, blank=True)  # relative to MEDIA_ROOT
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    size_bytes = models.IntegerField(null=True, blank=True)

    platform_alt_text = models.TextField(blank=True)
    position = models.IntegerField(default=0)
    offset_ms = models.IntegerField(null=True, blank=True)  # where the frame sits in the video

    analyzed_at = models.DateTimeField(null=True, blank=True)
    analysis = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "media"
        ordering = ["observation", "position"]

    def __str__(self) -> str:
        return f"{self.kind} #{self.position} of {self.observation_id}"


class Extraction(models.Model):
    """
    What the model understood from one observation, in a single multimodal pass.

    Classification, structured fields, image reading and the geocoding query string all come
    out of one schema-constrained call. It is done this way rather than in four calls for
    two reasons: the per-model rate limit is the real bottleneck, and the model
    resolves contradictions better when it sees text and image together.

    Note:
        It is one-to-one with the observation, but **one extraction can yield several
        requirements**: a post listing three collection centers produces three. That
        relation lives on `Requirement.evidence`.

        `text_image_conflict` flags cases where the photo does not match what the text
        claims. That is a signal of recycled imagery or of error, and it lowers confidence.
    """

    observation = models.OneToOneField(
        Observation, on_delete=models.CASCADE, related_name="extraction"
    )
    model = models.CharField(max_length=80)  # model id used
    prompt_version = models.CharField(max_length=20)

    classification = models.CharField(max_length=20, choices=ExtractionClass.choices)
    confidence = models.FloatField()
    payload = models.JSONField()  # object validated against the output schema

    geocode_query = models.CharField(max_length=300, blank=True)
    visual_summary = models.TextField(blank=True)
    text_image_conflict = models.BooleanField(default=False)

    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["classification", "confidence"])]

    def __str__(self) -> str:
        return f"{self.classification} ({self.confidence:.2f}) of {self.observation_id}"
