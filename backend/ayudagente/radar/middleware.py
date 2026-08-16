"""
API key authentication for the JSON API.

The frontend runs on its own origin, holds no Django session and stands for no user, so the
API authenticates the *client* rather than a person: one shared key per consumer, listed in
the environment. Everything outside the API prefix is left alone — the admin already
authenticates by session, and layering a second scheme over it only breaks the login page.

Note:
    An empty key list closes the API rather than opening it. A configuration that was never
    written and one that was stripped look identical from here, and the safe reading of that
    ambiguity is that nobody gets in.
"""

import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare

logger = logging.getLogger(__name__)

HEADER = "X-API-Key"
EXEMPT_METHODS = frozenset({"OPTIONS"})  # a CORS preflight cannot carry the key


class ApiKeyMiddleware:
    """
    Refuse requests to the API that do not carry a known key.

    The key travels in `X-API-Key` or as `Authorization: Bearer <key>`, whichever the client
    finds easier. Comparison is constant time, so a wrong key says nothing about the right one
    through how long the check took.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Check the key before the request reaches a view, or pass it straight through."""
        if not _is_protected(request):
            return self.get_response(request)

        keys = [key.strip() for key in settings.API_KEYS if key.strip()]
        if not keys:
            logger.error("API_KEYS is empty; refusing %s", request.path)
            return _refuse("the API has no keys configured", 503)

        presented = _presented_key(request)
        if presented is None:
            return _refuse(f"missing API key; send it in {HEADER}", 401)
        if not any(constant_time_compare(presented, key) for key in keys):
            logger.warning("rejected an unknown API key on %s", request.path)
            return _refuse("unknown API key", 403)

        return self.get_response(request)


def _is_protected(request: HttpRequest) -> bool:
    """Whether this request needs a key at all."""
    if request.method in EXEMPT_METHODS:
        return False
    return request.path.startswith(tuple(settings.API_KEY_PROTECTED_PREFIXES))


def _presented_key(request: HttpRequest) -> str | None:
    """
    The key the client sent, from whichever of the two accepted headers carries it.

    Returns:
        str | None: The key, or None when neither header holds one.
    """
    key = request.headers.get(HEADER, "").strip()
    if key:
        return key

    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.casefold() == "bearer" and value.strip():
        return value.strip()
    return None


def _refuse(message: str, status: int) -> JsonResponse:
    """The body a rejected client gets, shaped like every other error the API returns."""
    return JsonResponse({"error": message}, status=status)
