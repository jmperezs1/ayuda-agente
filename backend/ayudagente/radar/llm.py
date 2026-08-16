"""
OpenAI clients, and which model each job gets.

One API key, one client, and a role-to-model map. Roles exist because the jobs differ in what
they need: deciding where to harvest next is judgment worth a strong model, while checking
whether an image is worth a closer look is a job for the cheapest one available. Sending every
call to the same model either overpays for triage or underpowers the agent.
"""

from enum import StrEnum
from functools import cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from openai import OpenAI


class Role(StrEnum):
    """What a model is used for, which is also how a model name is chosen."""

    REASONING = "reasoning"  # the frontier agent, and actor adjudication
    EXTRACTION = "extraction"  # the structured multimodal pass over an observation
    TRIAGE = "triage"  # cheap pass deciding whether an image deserves a closer read
    EMBEDDING = "embedding"  # actor name vectors, the fourth signal of the cascade


@cache
def client() -> OpenAI:
    """
    Build the shared client.

    Returns:
        OpenAI: A client reused across calls.

    Raises:
        ImproperlyConfigured: If no API key is set. It fails here rather than at request
            time so a missing variable surfaces immediately instead of halfway through a
            harvest.
    """
    if not settings.OPENAI_API_KEY:
        raise ImproperlyConfigured("OPENAI_API_KEY must be set in .env")
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def model_for(role: Role) -> str:
    """
    Resolve the model name configured for a role.

    Args:
        role (Role): Which job the model is for.

    Returns:
        str: The model id to pass to the API.

    Raises:
        ImproperlyConfigured: If that role has no model configured.
    """
    name = settings.OPENAI_MODELS.get(role.value, "")
    if not name:
        raise ImproperlyConfigured(f"no model configured for role {role.value!r}")
    return name


def is_configured() -> bool:
    """
    Report whether OpenAI is set up, so callers can skip instead of crashing.

    Returns:
        bool: True when an API key is present.
    """
    return bool(settings.OPENAI_API_KEY)


def upload_image(path: str) -> str:
    """
    Upload a stored image and get an id the model can read.

    Args:
        path (str): Local path or readable file path of the image.

    Returns:
        str: The file id to pass as `input_image`.

    Note:
        This is how images survive. Platform media URLs are signed and expire within hours,
        so passing them through is only safe for something scraped minutes ago; anything we
        want to re-read later has to be our own copy.
    """
    with open(path, "rb") as handle:
        uploaded = client().files.create(file=handle, purpose="vision")
    return uploaded.id
