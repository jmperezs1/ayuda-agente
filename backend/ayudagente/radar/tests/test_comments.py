"""
Tests for reading the replies under a post.

This exists because a live sweep left 90% of requirements in quarantine and the number was
right. Toponym searches return what ranks — press and aggregators — so almost everything was a
third party reporting somebody else's need. The asking happens in the replies, and these
protect the selection that gets us there.
"""

from django.test import TestCase

from ayudagente.radar.choices import (
    DecisionSource,
    ExtractionClass,
    HarvestTarget,
    JobStatus,
    Platform,
)
from ayudagente.radar.models import Extraction, HarvestJob
from ayudagente.radar.services.apify_inputs import build_comment_input, comment_targets
from ayudagente.radar.services.comments import (
    COMMENT_PLATFORMS,
    POSTS_PER_JOB,
    queue_comment_pulls,
    worth_reading,
)
from ayudagente.radar.tests.factories import make_event, make_observation


class CommentsBase(TestCase):
    def setUp(self):
        self.event = make_event()

    def _post(
        self,
        platform_id: str,
        *,
        likes: int = 100,
        replies: int = 20,
        classification: str = ExtractionClass.NEED,
        platform: str = Platform.TIKTOK,
        is_reply: bool = False,
    ):
        observation = make_observation(
            self.event,
            f"post {platform_id}",
            platform=platform,
            platform_id=platform_id,
            permalink=f"https://www.tiktok.com/@x/video/{platform_id}",
            metrics={"likes": likes, "comments": replies},
            is_reply=is_reply,
        )
        if classification:
            Extraction.objects.create(
                observation=observation,
                model="test",
                prompt_version="v8",
                classification=classification,
                confidence=0.9,
                payload={},
            )
        return observation


class SelectionTests(CommentsBase):
    def test_the_post_people_answered_outranks_the_one_they_applauded(self):
        # Engagement ranking picked press and entertainment, whose replies are reaction
        viral = self._post("1", likes=200_000, replies=2_000)
        conversation = self._post("2", likes=500, replies=150)

        self.assertEqual(list(worth_reading(self.event, Platform.TIKTOK)), [conversation, viral])

    def test_a_tiny_post_does_not_win_on_a_ratio_computed_from_nothing(self):
        self._post("1", likes=1, replies=2)
        real = self._post("2", likes=500, replies=150)

        self.assertEqual(worth_reading(self.event, Platform.TIKTOK).first(), real)

    def test_a_quiet_post_is_still_read(self):
        # It may be the one person offering a truck; the score orders, it never excludes
        self._post("1", likes=0, replies=0)

        self.assertTrue(worth_reading(self.event, Platform.TIKTOK).exists())

    def test_a_post_the_pipeline_discarded_is_skipped(self):
        self._post("1", classification=ExtractionClass.DISCARD)

        self.assertFalse(worth_reading(self.event, Platform.TIKTOK).exists())

    def test_an_unread_post_is_skipped_until_the_pipeline_judges_it(self):
        self._post("1", classification="")

        self.assertFalse(worth_reading(self.event, Platform.TIKTOK).exists())

    def test_a_reply_is_not_a_source_of_replies(self):
        # Reading the replies of a reply is a thread walk, which is a different decision
        self._post("1", is_reply=True)

        self.assertFalse(worth_reading(self.event, Platform.TIKTOK).exists())

    def test_ranking_reads_the_response_not_what_the_post_itself_yielded(self):
        applauded = self._post("1", likes=800, replies=20, classification=ExtractionClass.OFFER)
        answered = self._post("2", likes=100, replies=60, classification=ExtractionClass.OFFER)

        ranked = list(worth_reading(self.event, Platform.TIKTOK))

        self.assertEqual(ranked, [answered, applauded])


class QueueTests(CommentsBase):
    def test_a_pull_batches_several_posts_into_one_job(self):
        for index in range(POSTS_PER_JOB + 2):
            self._post(str(index), likes=100, replies=10 + index)

        self.assertEqual(queue_comment_pulls(self.event), 1)

        job = HarvestJob.objects.get(target_kind=HarvestTarget.COMMENTS)
        self.assertEqual(len(job.actor_input["postURLs"]), POSTS_PER_JOB)
        self.assertEqual(job.decided_by, DecisionSource.RULE)
        self.assertIsNone(job.node)
        self.assertTrue(job.rationale)

    def test_the_same_post_is_never_pulled_twice(self):
        self._post("1")
        queue_comment_pulls(self.event)

        self.assertEqual(queue_comment_pulls(self.event), 0)

    def test_every_platform_excludes_what_it_already_pulled(self):
        """
        The round trip that pays for itself.

        Note:
            The exclusion used to read `actor_input__postURLs`, which is TikTok's field name.
            For the other three it was always null, so nothing was ever excluded and the same
            five posts were bought every round — $0.41 of Instagram comments already held.
        """
        for platform in Platform.values:
            with self.subTest(platform=platform):
                event = make_event(name=f"Sismo {platform}")
                observation = make_observation(
                    event,
                    "punto de acopio",
                    platform=platform,
                    platform_id=f"{platform}-1",
                    permalink=f"https://{platform}.example/user/status/{platform}-1",
                    metrics={"likes": 100, "comments": 20},
                )
                Extraction.objects.create(
                    observation=observation,
                    model="test",
                    prompt_version="v9",
                    classification=ExtractionClass.OFFER,
                    confidence=0.9,
                    payload={},
                )

                self.assertEqual(queue_comment_pulls(event), 1)
                self.assertEqual(queue_comment_pulls(event), 0)

    def test_what_a_pull_asks_for_is_what_reading_it_back_returns(self):
        # If these two ever disagree the exclusion silently stops excluding
        links = ["https://example.com/user/status/12345"]

        for platform in Platform.values:
            with self.subTest(platform=platform):
                targets = comment_targets(build_comment_input(platform, links))

                self.assertTrue(
                    targets & {links[0], "12345"},
                    f"{platform} input cannot be read back: {targets}",
                )

    def test_a_newly_read_post_earns_its_own_pull(self):
        self._post("1")
        queue_comment_pulls(self.event)

        self._post("2", likes=500)

        self.assertEqual(queue_comment_pulls(self.event), 1)

    def test_nothing_to_read_queues_nothing(self):
        self.assertEqual(queue_comment_pulls(self.event), 0)

    def test_only_the_platforms_worth_reading_get_pulled(self):
        # Instagram replies measured 6% actionable and X 7%, against TikTok's 33%
        for platform in Platform.values:
            self._post(f"{platform}-1", platform=platform)

        self.assertEqual(queue_comment_pulls(self.event), len(COMMENT_PLATFORMS))

        pulled = set(HarvestJob.objects.values_list("platform", flat=True))
        self.assertEqual(pulled, set(COMMENT_PLATFORMS))

    def test_the_job_is_marked_as_a_comment_pull_so_the_right_normalizer_runs(self):
        self._post("1")

        queue_comment_pulls(self.event)

        job = HarvestJob.objects.get()
        self.assertEqual(job.target_kind, HarvestTarget.COMMENTS)
        self.assertEqual(job.status, JobStatus.PENDING)


class InputTests(TestCase):
    def test_the_payload_is_what_the_comments_actor_declares(self):
        payload = build_comment_input(Platform.TIKTOK, ["https://tiktok.com/@a/video/1"], 50)

        self.assertEqual(payload["postURLs"], ["https://tiktok.com/@a/video/1"])
        self.assertEqual(payload["commentsPerPost"], 50)

    def test_a_pull_with_no_post_is_refused(self):
        with self.assertRaises(ValueError):
            build_comment_input(Platform.TIKTOK, [])

    def test_each_platform_gets_the_field_names_its_actor_declares(self):
        links = ["https://example.com/p/1"]
        expected = {
            Platform.X: "searchTerms",
            Platform.INSTAGRAM: "directUrls",
            Platform.FACEBOOK: "startUrls",
            Platform.TIKTOK: "postURLs",
        }

        for platform, field in expected.items():
            with self.subTest(platform=platform):
                self.assertIn(field, build_comment_input(platform, links))

    def test_x_asks_for_one_thread_per_post_rather_than_batching(self):
        # Batching with OR would merge the threads and lose which post each reply hung under
        payload = build_comment_input(
            Platform.X,
            ["https://x.com/a/status/111", "https://x.com/b/status/222"],
        )

        self.assertEqual(payload["searchTerms"], ["conversation_id:111", "conversation_id:222"])

    def test_facebook_wraps_each_link_the_way_its_actor_wants(self):
        payload = build_comment_input(Platform.FACEBOOK, ["https://facebook.com/1"])

        self.assertEqual(payload["startUrls"], [{"url": "https://facebook.com/1"}])
        self.assertFalse(payload["includeNestedComments"])
