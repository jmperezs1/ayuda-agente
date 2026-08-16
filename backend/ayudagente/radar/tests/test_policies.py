"""
Hermetic tests for the rules that must not drift, none of which need a database.

These cover the invariants documented in `docs/data-model.md`: organization derivation,
channel ordering, location precision ordering, deep-link construction, idempotency key
stability, and the resource hierarchy.
"""

import pytest

from ayudagente.radar.choices import (
    ActorKind,
    ContactKind,
    LocationPrecision,
    OutreachChannel,
    OutreachPurpose,
)
from ayudagente.radar.models import Actor, ContactPoint, Location, Outreach, ResourceType
from ayudagente.radar.models.actors import CHANNEL_PREFERENCE, ORGANIZATION_KINDS


def contact(kind, *, actor_kind=ActorKind.COLLECTION_CENTER, reachable=True):
    """Build an unsaved contact point on an unsaved actor, for policy checks only."""
    return ContactPoint(
        actor=Actor(kind=actor_kind),
        kind=kind,
        value="x",
        raw_value="x",
        reachable=reachable,
    )


class TestOrganizationDerivation:
    """`is_organization` is derived so it cannot go stale or be skipped by bulk_create."""

    def test_a_collection_center_is_an_organization(self):
        assert Actor(kind=ActorKind.COLLECTION_CENTER).is_organization

    def test_a_person_is_never_an_organization(self):
        assert not Actor(kind=ActorKind.PERSON).is_organization
        assert ActorKind.PERSON not in ORGANIZATION_KINDS

    def test_every_organization_kind_agrees_with_the_property(self):
        for kind in ORGANIZATION_KINDS:
            assert Actor(kind=kind).is_organization


class TestContactChannels:
    """Not every contact detail can carry a message, and order matters for the dashboard."""

    @pytest.mark.parametrize("kind", [ContactKind.PAYMENT, ContactKind.STREET_ADDRESS])
    def test_payment_and_postal_details_are_not_channels(self, kind):
        assert not contact(kind).can_carry_a_message

    @pytest.mark.parametrize("kind", list(CHANNEL_PREFERENCE))
    def test_every_preference_entry_is_a_usable_channel(self, kind):
        assert contact(kind).can_carry_a_message

    def test_an_unreachable_channel_cannot_carry_a_message(self):
        assert not contact(ContactKind.EMAIL, reachable=False).can_carry_a_message

    def test_email_outranks_phone(self):
        assert (
            contact(ContactKind.EMAIL).preference_rank()
            < contact(ContactKind.PHONE).preference_rank()
        )

    def test_unusable_details_sort_last(self):
        worst = contact(ContactKind.EMAIL).preference_rank()
        assert contact(ContactKind.PAYMENT).preference_rank() > worst


class TestLocationPrecision:
    """Precision has to be ordered, or matching cannot enforce a minimum."""

    def test_a_street_address_satisfies_a_second_level_requirement(self):
        location = Location(precision=LocationPrecision.STREET_ADDRESS)
        assert location.is_at_least(LocationPrecision.ADMIN_2)

    def test_a_first_level_does_not_satisfy_a_neighborhood_requirement(self):
        location = Location(precision=LocationPrecision.ADMIN_1)
        assert not location.is_at_least(LocationPrecision.NEIGHBORHOOD)

    def test_a_precision_always_satisfies_itself(self):
        for value in LocationPrecision.values:
            assert Location(precision=value).is_at_least(value)


class TestDeepLinks:
    """Every channel resolves to a URL a human can click; nothing is sent by the system."""

    def test_whatsapp_link_drops_the_plus_and_encodes_the_body(self):
        url = Outreach.build_whatsapp_url("+573002377012", "hola Janeth & equipo")
        assert url.startswith("https://wa.me/573002377012?text=")
        assert "%26" in url and " " not in url

    def test_mailto_link_carries_subject_and_body(self):
        url = Outreach.build_mailto_url("ayuda@ong.org", "Punto de acopio", "¿siguen abiertos?")
        assert url.startswith("mailto:ayuda@ong.org?subject=")
        assert "body=" in url and " " not in url

    def test_whatsapp_and_email_prefill_the_text(self):
        for channel in (OutreachChannel.WHATSAPP, OutreachChannel.EMAIL):
            assert Outreach(channel=channel).text_is_prefilled

    def test_a_comment_reply_needs_a_copy_step(self):
        assert not Outreach(channel=OutreachChannel.COMMENT_REPLY).text_is_prefilled


class TestIdempotencyKey:
    """Retrying a task must not produce a second proposal to the same person."""

    def test_same_inputs_produce_the_same_key(self):
        args = (7, OutreachPurpose.CONNECT, OutreachChannel.EMAIL, 42)
        assert Outreach.build_idempotency_key(*args) == Outreach.build_idempotency_key(*args)

    def test_purpose_changes_the_key(self):
        connect = Outreach.build_idempotency_key(7, OutreachPurpose.CONNECT, "email", 42)
        verify = Outreach.build_idempotency_key(7, OutreachPurpose.VERIFY, "email", 42)
        assert connect != verify

    def test_channel_changes_the_key(self):
        email = Outreach.build_idempotency_key(7, OutreachPurpose.CONNECT, "email", 42)
        whatsapp = Outreach.build_idempotency_key(7, OutreachPurpose.CONNECT, "whatsapp", 42)
        assert email != whatsapp

    def test_an_anchorless_message_differs_from_an_anchored_one(self):
        loose = Outreach.build_idempotency_key(7, OutreachPurpose.ANSWER, "email")
        anchored = Outreach.build_idempotency_key(7, OutreachPurpose.ANSWER, "email", 42)
        assert loose != anchored


class TestResourceHierarchy:
    """Category fallback depends on walking parents correctly."""

    def test_ancestors_are_returned_nearest_first(self):
        shelter = ResourceType(key="shelter", name="Shelter")
        bedding = ResourceType(key="bedding", name="Bedding", parent=shelter)
        mats = ResourceType(key="mats", name="Sleeping mats", parent=bedding)
        assert mats.ancestors() == [bedding, shelter]

    def test_a_root_resource_has_no_ancestors(self):
        assert ResourceType(key="water", name="Water").ancestors() == []
