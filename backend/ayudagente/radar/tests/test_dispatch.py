"""
Tests for the one endpoint that writes.

What matters is saturation. `covered_quantity` counts from `contacted` onward, so a dispatch
nobody recorded leaves a collection point looking untouched and the system keeps proposing it
to the next ten people. Every case here is about that count staying honest.
"""

from django.urls import reverse

from ayudagente.radar.choices import ActorKind, OutreachChannel, OutreachStatus
from ayudagente.radar.models import Outreach
from ayudagente.radar.tests.factories import (
    ApiTestCase,
    make_actor,
    make_contact,
    make_event,
    make_outreach,
)


class DispatchTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.event = make_event()
        self.actor = make_actor(self.event, "Coliseo", kind=ActorKind.COLLECTION_CENTER)
        self.outreach = make_outreach(self.actor, make_contact(self.actor))

    def _post(self, body=None, outreach=None):
        return self.client.post(
            reverse("radar:outreach-dispatch", args=[(outreach or self.outreach).pk]),
            data=body or {},
            content_type="application/json",
        )

    def test_a_click_records_the_dispatch(self):
        response = self._post()

        self.outreach.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.outreach.status, OutreachStatus.DISPATCHED)
        self.assertIsNotNone(self.outreach.dispatched_at)

    def test_the_updated_draft_comes_back_so_the_ui_need_not_refetch(self):
        payload = self._post().json()

        self.assertEqual(payload["status"], OutreachStatus.DISPATCHED)
        self.assertEqual(payload["id"], self.outreach.pk)
        self.assertTrue(payload["target_url"])

    def test_a_draft_can_be_dismissed_instead(self):
        self._post({"status": "dismissed"})

        self.outreach.refresh_from_db()
        self.assertEqual(self.outreach.status, OutreachStatus.DISMISSED)
        self.assertIsNone(self.outreach.dispatched_at)

    def test_a_double_click_leaves_one_dispatch(self):
        self._post()
        first = Outreach.objects.get(pk=self.outreach.pk).dispatched_at

        self._post()

        self.outreach.refresh_from_db()
        self.assertEqual(self.outreach.dispatched_at, first)

    def test_a_status_only_the_system_may_set_is_refused(self):
        for status in ("answered", "failed", "draft"):
            with self.subTest(status=status):
                response = self._post({"status": status})
                self.assertEqual(response.status_code, 400)

    def test_a_malformed_body_is_reported_as_such(self):
        response = self.client.post(
            reverse("radar:outreach-dispatch", args=[self.outreach.pk]),
            data="{[",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_an_unknown_draft_is_a_404(self):
        self.assertEqual(self._post(outreach=_Missing()).status_code, 404)

    def test_a_get_is_not_allowed(self):
        response = self.client.get(reverse("radar:outreach-dispatch", args=[self.outreach.pk]))

        self.assertEqual(response.status_code, 405)

    def test_it_needs_the_api_key_like_everything_else(self):
        from django.test import Client

        response = Client().post(
            reverse("radar:outreach-dispatch", args=[self.outreach.pk]),
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_a_comment_reply_is_dispatched_the_same_way(self):
        # It cannot carry text in the URL, but the click is still what a human did
        reply = make_outreach(
            self.actor,
            make_contact(self.actor, "@coliseo", kind="handle"),
            channel=OutreachChannel.COMMENT_REPLY,
        )

        self._post(outreach=reply)

        reply.refresh_from_db()
        self.assertEqual(reply.status, OutreachStatus.DISPATCHED)


class _Missing:
    """Stands in for an outreach id nothing matches."""

    pk = 999_999
