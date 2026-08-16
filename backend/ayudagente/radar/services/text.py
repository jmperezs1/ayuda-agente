"""Shared text normalization, so a cache key and a name comparison agree on what is equal."""

import unicodedata


def normalize(text: str) -> str:
    """
    Reduce a string to a stable comparison key.

    Args:
        text (str): Raw text as written.

    Returns:
        str: Lowercased, unaccented and whitespace-collapsed, so "Quibdó" and "quibdo " are
            one key rather than two.
    """
    stripped = unicodedata.normalize("NFKD", text.strip().casefold())
    without_accents = "".join(char for char in stripped if not unicodedata.combining(char))
    return " ".join(without_accents.split())
