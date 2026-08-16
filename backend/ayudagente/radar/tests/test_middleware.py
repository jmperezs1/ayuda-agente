"""
Tests for the API key gate.

The interesting cases are the ones where the middleware has to refuse: no key, a wrong key,
and a deployment where nobody configured any. That last one is the reason this file exists —
an empty key list must close the API, and a regression there opens it to the internet without
failing anything else.
"""

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ayudagente.radar.tests.factories import API_KEY, ApiTestCase, make_event


@override_settings(API_KEYS=[API_KEY])
class ApiKeyTests(TestCase):
    def setUp(self):
        self.url = reverse("radar:event-list")

    def test_a_request_without_a_key_is_refused(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertIn("X-API-Key", response.json()["error"])

    def test_a_wrong_key_is_refused(self):
        response = self.client.get(self.url, headers={"x-api-key": "not-the-key"})

        self.assertEqual(response.status_code, 403)

    def test_the_key_header_is_accepted(self):
        response = self.client.get(self.url, headers={"x-api-key": API_KEY})

        self.assertEqual(response.status_code, 200)

    def test_a_bearer_token_is_accepted_too(self):
        response = self.client.get(self.url, headers={"authorization": f"Bearer {API_KEY}"})

        self.assertEqual(response.status_code, 200)

    def test_another_authorization_scheme_is_not_a_key(self):
        response = self.client.get(self.url, headers={"authorization": f"Basic {API_KEY}"})

        self.assertEqual(response.status_code, 401)

    def test_a_preflight_passes_without_a_key(self):
        # The browser cannot attach the header to a preflight, so requiring it forbids CORS
        response = self.client.options(self.url, headers={"origin": "http://localhost:3000"})

        self.assertNotIn(response.status_code, (401, 403))

    def test_the_admin_is_left_to_its_own_authentication(self):
        response = self.client.get("/admin/login/")

        self.assertEqual(response.status_code, 200)


class UnconfiguredApiTests(TestCase):
    """A deployment with no keys is closed, not open."""

    @override_settings(API_KEYS=[])
    def test_no_configured_key_refuses_everyone(self):
        response = self.client.get(reverse("radar:event-list"), headers={"x-api-key": API_KEY})

        self.assertEqual(response.status_code, 503)

    @override_settings(API_KEYS=["   "])
    def test_a_blank_key_does_not_count_as_configuration(self):
        response = self.client.get(reverse("radar:event-list"), headers={"x-api-key": "   "})

        self.assertEqual(response.status_code, 503)


class MultipleKeyTests(ApiTestCase):
    """One key per consumer, so revoking the frontend's does not lock out the dashboard."""

    @override_settings(API_KEYS=[API_KEY, "second-consumer"])
    def test_every_configured_key_is_accepted(self):
        make_event()
        client = Client(headers={"x-api-key": "second-consumer"})

        self.assertEqual(client.get(reverse("radar:event-list")).status_code, 200)
