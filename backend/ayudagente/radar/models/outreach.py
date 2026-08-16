"""The action surface: turning what was found into a message a human can send in one click."""

import hashlib
from urllib.parse import quote

from django.conf import settings
from django.db import models

from ayudagente.radar.choices import OutreachChannel, OutreachPurpose, OutreachStatus
from ayudagente.radar.models.actors import Actor, ContactPoint
from ayudagente.radar.models.harvest import Observation
from ayudagente.radar.models.requirements import Match, Requirement


class Outreach(models.Model):
    """
    One proposed message about one finding, from draft through to reply.

    Nothing is ever sent by the system. Every channel resolves to the same pattern: a URL
    that drops a human in the right place, plus the text the model wrote. `wa.me` and
    `mailto:` carry the text in the URL; a post permalink does not, so the dashboard offers
    a copy button. That single pattern removes the exception that direct messages and comment
    replies used to be — they cannot be automated, but they can be assisted exactly like the
    rest.

    Note:
        `purpose` is what lets any observation become actionable, not only a match. Answering
        someone who asked where to go, or checking whether a collection point is still open,
        are messages worth proposing even when nothing has been paired yet.

        Three anchors, all optional, say what the message is *about*: `match` when two parties
        are being introduced, `in_reply_to` when responding to a specific post or comment, and
        `about_requirement` when verifying or asking after one need. A message has at least
        one of them in practice, but the model does not force it — a plain introduction to an
        actor is legitimate.

        `idempotency_key` is what keeps saturation counting honest. A task can be retried
        three times, but the unique constraint means the proposal is created once, so "ten
        people already contacted" stays true rather than approximately true.

    See:
        `ayudagente.radar.models.actors.ContactPoint.preference_rank` for channel ordering.
    """

    target_actor = models.ForeignKey(
        Actor, on_delete=models.CASCADE, related_name="received_outreach"
    )
    contact_point = models.ForeignKey(
        ContactPoint, on_delete=models.PROTECT, related_name="messages"
    )
    purpose = models.CharField(max_length=20, choices=OutreachPurpose.choices)
    channel = models.CharField(max_length=20, choices=OutreachChannel.choices)

    match = models.ForeignKey(
        Match, null=True, blank=True, on_delete=models.SET_NULL, related_name="outreach"
    )
    in_reply_to = models.ForeignKey(
        Observation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outreach_replies",
    )
    about_requirement = models.ForeignKey(
        Requirement,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outreach",
    )

    subject = models.CharField(max_length=200, blank=True)  # email only
    body = models.TextField()
    target_url = models.URLField(max_length=1000)  # where the human's click lands
    drafted_by = models.CharField(max_length=80)  # model id that wrote it

    status = models.CharField(
        max_length=20, choices=OutreachStatus.choices, default=OutreachStatus.DRAFT
    )
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatched_outreach",
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    reply_text = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)

    idempotency_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "outreach"
        indexes = [models.Index(fields=["status", "purpose", "created_at"])]

    def __str__(self) -> str:
        return f"{self.purpose} via {self.channel} → {self.target_actor_id} ({self.status})"

    @property
    def text_is_prefilled(self) -> bool:
        """True when the link already carries the body, so no copy step is needed."""
        return self.channel in (OutreachChannel.WHATSAPP, OutreachChannel.EMAIL)

    @staticmethod
    def build_idempotency_key(
        actor_id: int, purpose: str, channel: str, anchor_id: int | None = None
    ) -> str:
        """
        Build the key that prevents proposing the same message to the same actor twice.

        Args:
            actor_id (int): Recipient actor.
            purpose (str): An `OutreachPurpose` value.
            channel (str): An `OutreachChannel` value.
            anchor_id (int | None): Match, observation or requirement the message is about.

        Returns:
            str: Hex SHA-256 digest, stable across retries of the same task.
        """
        raw = f"{actor_id}|{purpose}|{channel}|{anchor_id or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def build_whatsapp_url(phone_e164: str, body: str) -> str:
        """
        Build a `wa.me` link that opens WhatsApp with the number and message already set.

        Args:
            phone_e164 (str): Number in E.164 form; the leading `+` is dropped by wa.me.
            body (str): Message text, URL-encoded here.

        Returns:
            str: A link that needs no API, no authentication and no app review.
        """
        return f"https://wa.me/{phone_e164.lstrip('+')}?text={quote(body)}"

    @staticmethod
    def build_mailto_url(email: str, subject: str, body: str) -> str:
        """
        Build a `mailto:` link, which needs no domain, mail server or deliverability setup.

        Args:
            email (str): Recipient address.
            subject (str): Subject line.
            body (str): Message text.

        Returns:
            str: A link that opens the human's own mail client with everything filled in.
        """
        return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"
