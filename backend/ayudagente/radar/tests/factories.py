"""Minimal object builders for service tests. Plain functions, no factory library."""

from django.contrib.gis.geos import Point
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from ayudagente.radar.choices import (
    ActorKind,
    ContactKind,
    Direction,
    GeocodeSource,
    HazardKind,
    LocationPrecision,
    MediaKind,
    OutreachChannel,
    OutreachPurpose,
    Platform,
    Urgency,
)
from ayudagente.radar.models import (
    Actor,
    ContactPoint,
    Event,
    Location,
    Media,
    Observation,
    Outreach,
    Requirement,
    ResourceType,
)

# Real coordinates, lon/lat
PEREIRA = Point(-75.6961, 4.8133, srid=4326)
DOSQUEBRADAS = Point(-75.6727, 4.8318, srid=4326)
QUIBDO = Point(-76.6611, 5.6947, srid=4326)
CALI = Point(-76.5320, 3.4516, srid=4326)

API_KEY = "test-api-key"


@override_settings(API_KEYS=[API_KEY])
class ApiTestCase(TestCase):
    """
    Base for endpoint tests: a client that already carries a valid API key.

    Note:
        The key is injected as a client default rather than passed per call, so a test that
        forgets it still exercises the endpoint. Whether the middleware refuses without one
        is its own test, not a condition every other test has to keep restating.
    """

    def setUp(self):
        super().setUp()
        self.client = Client(headers={"x-api-key": API_KEY})


def make_event(**kwargs) -> Event:
    defaults = {
        "hazard": HazardKind.EARTHQUAKE,
        "name": "Sismo de prueba",
        "occurred_at": timezone.now(),
        "detection_source": "manual",
        "country_code": "CO",
        "epicenter": PEREIRA,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def make_resource(key: str, name: str | None = None, parent=None, **kwargs) -> ResourceType:
    return ResourceType.objects.create(
        key=key, name=name or key.replace("_", " ").title(), parent=parent, **kwargs
    )


def make_location(point: Point, text: str, **kwargs) -> Location:
    defaults = {
        "precision": LocationPrecision.NEIGHBORHOOD,
        "raw_text": text,
        "text_norm": text.lower(),
        "source": GeocodeSource.MANUAL,
    }
    defaults.update(kwargs)
    return Location.objects.create(point=point, **defaults)


def make_actor(event: Event, name: str, **kwargs) -> Actor:
    now = timezone.now()
    defaults = {
        "kind": ActorKind.PERSON,
        "canonical_name": name,
        "name_norm": name.lower(),
        "first_seen_at": now,
        "last_seen_at": now,
    }
    defaults.update(kwargs)
    return Actor.objects.create(event=event, **defaults)


def make_requirement(
    event: Event,
    actor: Actor,
    resource: ResourceType,
    location: Location,
    direction: str = Direction.NEEDS,
    **kwargs,
) -> Requirement:
    defaults = {
        "urgency": Urgency.HIGH,
        "last_seen_at": timezone.now(),
        "confidence": 0.8,
    }
    defaults.update(kwargs)
    return Requirement.objects.create(
        event=event,
        actor=actor,
        resource=resource,
        location=location,
        direction=direction,
        **defaults,
    )


def make_observation(event: Event, text: str = "necesitamos agua", **kwargs) -> Observation:
    platform_id = kwargs.pop("platform_id", None) or str(Observation.objects.count() + 1)
    defaults = {
        "platform": Platform.X,
        "platform_id": platform_id,
        "permalink": f"https://x.com/u/status/{platform_id}",
        "posted_at": timezone.now(),
        "author_handle": "@vecino",
        "author_name": "Vecino",
        "raw": {},
    }
    defaults.update(kwargs)
    return Observation.objects.create(event=event, text=text, **defaults)


def make_media(observation: Observation, **kwargs) -> Media:
    defaults = {
        "kind": MediaKind.IMAGE,
        "source_url": "https://cdn.example/photo.jpg",
        "blob_path": "pilot/photo.jpg",
        "platform_alt_text": "Puente colapsado",
    }
    defaults.update(kwargs)
    return Media.objects.create(observation=observation, **defaults)


def make_contact(actor: Actor, value: str = "+573002377012", **kwargs) -> ContactPoint:
    defaults = {"kind": ContactKind.WHATSAPP, "raw_value": value}
    defaults.update(kwargs)
    return ContactPoint.objects.create(actor=actor, value=value, **defaults)


def make_outreach(actor: Actor, contact: ContactPoint, **kwargs) -> Outreach:
    defaults = {
        "purpose": OutreachPurpose.CONNECT,
        "channel": OutreachChannel.WHATSAPP,
        "body": "Hola, tenemos agua disponible cerca.",
        "target_url": "https://wa.me/573002377012?text=Hola",
        "drafted_by": "gpt-5.6-sol",
    }
    defaults.update(kwargs)
    defaults.setdefault(
        "idempotency_key",
        Outreach.build_idempotency_key(
            actor.id, defaults["purpose"], defaults["channel"], Outreach.objects.count() + 1
        ),
    )
    return Outreach.objects.create(target_actor=actor, contact_point=contact, **defaults)
