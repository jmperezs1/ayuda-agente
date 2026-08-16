"""
Reading the replies under a post, which is where the affected actually write.

A live sweep produced 117 requirements of which 90% stayed in quarantine, and that number was
correct rather than a bad threshold. Toponym searches surface what ranks — press accounts and
aggregators reposting institutional announcements — so almost every requirement was a third
party reporting somebody else's need. Lowering the bar would only have let hearsay through.

The people asking for help are one level down. They reply to the announcement, they comment on
the video, they answer the collection point's post asking whether it is still open. That is
first-hand, it names a neighbourhood rather than a department, and it is the half of the
conversation a search never returns.

Note:
    Rule-driven, not agent-driven, and that follows from an invariant rather than convenience.
    Choosing which *post* to read replies under requires knowing what the post said, and the
    frontier agent never sees a post. So the agent allocates depth across places and accounts;
    this picks posts, mechanically, from what the pipeline already judged actionable.

    All four are pulled. The normalizers were checked against a live event and every platform's
    replies came back whole, so nothing here is blocked on parsing.

    What each platform's replies are *worth* is still open, and the one comparison that exists
    is not fair: TikTok's 33% actionable was measured after the ranking below was fixed, while
    Instagram's 6% and X's 7% were measured before it, with the engagement ranking that is now
    known to select press and entertainment. On Instagram that means the largest accounts,
    whose comments are applause by construction — the confound and the result are the same
    thing. X's number rests on 43 replies besides, which is noise.

    So `COMMENT_PLATFORMS` is where a platform would be dropped once there is evidence to drop
    it on. Cutting one now would be filtering for what might be poor rather than for what
    cannot be acted on, which is the distinction this system is built around.
"""

import logging

from django.db.models import ExpressionWrapper, FloatField, IntegerField, Q, QuerySet
from django.db.models.functions import Cast, Coalesce

from ayudagente.radar.choices import (
    DecisionSource,
    ExtractionClass,
    HarvestTarget,
    JobStatus,
    Platform,
)
from ayudagente.radar.models import Event, HarvestJob, Observation
from ayudagente.radar.services.apify_inputs import (
    COMMENT_ACTOR_BY_PLATFORM,
    build_comment_input,
    comment_targets,
)

logger = logging.getLogger(__name__)

# Posts per job. The Actor takes several at once, so this is a batch, not a limit on depth.
POSTS_PER_JOB = 5

COMMENTS_PER_POST = 100

# Damps the ratio of posts too small for it to mean anything, without excluding them
SMOOTHING = 50

ACTIONABLE = (ExtractionClass.NEED, ExtractionClass.OFFER, ExtractionClass.BOTH)

# Narrow this only on a fair measurement, never on a hunch — see the module note
COMMENT_PLATFORMS = tuple(COMMENT_ACTOR_BY_PLATFORM)


def worth_reading(event: Event, platform: str) -> QuerySet:
    """
    The posts of one platform whose replies are most likely to hold first-hand reports.

    Args:
        event (Event): The emergency.
        platform (str): A `Platform` value.

    Returns:
        QuerySet[Observation]: Most conversational first, excluding posts already pulled and
            comments themselves.

    Note:
        Ranked by how much of the response is *reply* rather than applause, never filtered by
        it. A quiet post may still be the one person offering a truck, so order decides what is
        read first, not what is read at all.

        Ranking by raw engagement was measured and it was wrong. It selected the posts with the
        widest audience, which on every platform means press and entertainment, and their
        replies are public reaction: 200 comments pulled that way were 80% discards against 36%
        for search, and 3% of what they produced was corroborated against 21%. Reach and
        conversation are different things and the system wants the second.

        Smoothed, so a post with two likes and one reply does not outrank a neighbourhood
        thread with a hundred of each on a ratio computed from nothing.

        Replies are excluded as sources. Reading the replies of a reply is a thread walk, which
        is `HarvestTarget.THREAD` and a different decision.

        Posts already pulled are excluded through `comment_targets`, which reads whichever
        field that platform's Actor uses. Reading one field name directly meant three of four
        platforms never excluded anything and re-bought the same replies every round.

        Matched against both the permalink and the `platform_id`, because X addresses a thread
        by tweet id while the rest address a post by URL — and a tweet id is exactly what
        `platform_id` holds. One `Q` covers both spaces without branching on the platform.
    """
    already = HarvestJob.objects.filter(
        event=event, target_kind=HarvestTarget.COMMENTS, platform=platform
    ).values_list("actor_input", flat=True)
    pulled = {target for actor_input in already for target in comment_targets(actor_input or {})}

    replies = Coalesce(Cast("metrics__comments", IntegerField()), 0)
    likes = Coalesce(Cast("metrics__likes", IntegerField()), 0)

    return (
        Observation.objects.filter(
            event=event,
            platform=platform,
            is_reply=False,
            extraction__classification__in=ACTIONABLE,
        )
        .exclude(permalink="")
        .exclude(Q(permalink__in=pulled) | Q(platform_id__in=pulled))
        .annotate(
            conversation=ExpressionWrapper(
                replies * 1.0 / (replies + likes + SMOOTHING), output_field=FloatField()
            ),
            replies=replies,
        )
        .order_by("-conversation", "-replies")
    )


def queue_comment_pulls(event: Event, limit: int | None = None) -> int:
    """
    Queue a comment pull for the posts most likely to be worth one.

    Args:
        event (Event): The emergency.
        limit (int | None): Cap on jobs created this round.

    Returns:
        int: Jobs created. Zero when nothing new qualifies, which is the steady state once the
            engaged posts have all been read.

    Note:
        Runs for every platform in `COMMENT_PLATFORMS`, which is all of them until a fair
        comparison says otherwise.
    """
    created = 0

    for platform in COMMENT_PLATFORMS:
        posts = list(worth_reading(event, platform)[:POSTS_PER_JOB])
        if not posts:
            continue
        if limit is not None and created >= limit:
            break

        permalinks = [post.permalink for post in posts]
        HarvestJob.objects.create(
            event=event,
            node=None,
            platform=platform,
            target_kind=HarvestTarget.COMMENTS,
            apify_actor=COMMENT_ACTOR_BY_PLATFORM[Platform(platform)],
            actor_input=build_comment_input(platform, permalinks, COMMENTS_PER_POST),
            decided_by=DecisionSource.RULE,
            rationale=(
                f"Replies under {len(posts)} {platform} posts people answered rather than "
                f"applauded. A search returns what ranks; the asking happens one level down."
            ),
            status=JobStatus.PENDING,
        )
        created += 1

    if created:
        logger.info("queued %s comment pulls for event %s", created, event.pk)
    return created
