"""
Keeping our own copy of the images a post carried.

Platform media URLs are signed and expire within hours. `Media.source_url` is what the scraper
returned and it is already rotting when it arrives — a live harvest stored 769 of them and the
frontend renders `null` for every one, because nothing ever downloaded the bytes.

That costs two things. The obvious one is a dashboard of broken images. The quieter one is that
the vision step cannot be re-run: when the prompt improves, re-reading a post means re-reading
its photo, and by then the URL is gone.

Note:
    Bytes go to the filesystem under `MEDIA_ROOT`, never into Postgres. Hundreds of megabytes
    of images in the database is hundreds of megabytes in every backup, forever.

    `sha256` is computed on the way in and catches the same photo recycled across posts and
    across emergencies. It is the cheapest defence there is against image-based
    misinformation, and it is free here because the bytes are already in hand.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from django.conf import settings
from django.db.models import QuerySet

from ayudagente.radar.models import Media, Observation

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 30.0

# Past this a single file is not a photo of a collapsed bridge, it is a video nobody asked for
MAX_BYTES = 15 * 1024 * 1024

BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AyudAgente/1.0)"}


@dataclass
class Downloaded:
    """
    What one pass fetched.

    Attributes:
        stored (int): Files written.
        reused (int): Files already held under the same hash, linked rather than rewritten.
        failed (int): URLs that could not be fetched, usually because they had expired.
        bytes (int): Total written.
    """

    stored: int = 0
    reused: int = 0
    failed: int = 0
    bytes: int = 0


def pending(event_id: int | None = None) -> QuerySet:
    """
    The media rows with no local copy yet.

    Args:
        event_id (int | None): Restrict to one emergency.

    Returns:
        QuerySet[Media]: Newest first, so a partial run covers what is still current — an
            expired URL cannot be recovered, and the oldest are the likeliest to be gone.
    """
    queryset = Media.objects.filter(blob_path="").exclude(source_url="")
    if event_id is not None:
        queryset = queryset.filter(observation__event_id=event_id)
    return queryset.select_related("observation").order_by("-observation__posted_at")


def download(media: Media, client: httpx.Client) -> bool:
    """
    Fetch one file and record where it landed.

    Args:
        media (Media): The row to fill in.
        client (httpx.Client): Shared connection pool.

    Returns:
        bool: True when bytes were written or an identical file was already held.

    Note:
        Failure is logged and swallowed. An expired URL is the normal case rather than an
        error, and a harvest that aborted on the first dead link would store nothing from any
        post older than a day.
    """
    try:
        response = client.get(media.source_url, headers=BROWSER_HEADERS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("media %s could not be fetched: %s", media.pk, exc)
        return False

    payload = response.content
    if not payload or len(payload) > MAX_BYTES:
        logger.info("media %s skipped: %s bytes", media.pk, len(payload))
        return False

    digest = hashlib.sha256(payload).hexdigest()
    path = _path_for(digest, media)
    absolute = Path(settings.MEDIA_ROOT) / path

    reused = absolute.exists()
    if not reused:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(payload)

    media.blob_path = path
    media.sha256 = digest
    media.size_bytes = len(payload)
    media.save(update_fields=["blob_path", "sha256", "size_bytes"])
    return True


def fetch_media_for(observation: Observation) -> int:
    """
    Make sure one post's images are on disk before anything reads it.

    Args:
        observation (Observation): The post about to be extracted.

    Returns:
        int: Files stored. Zero when there were none, or when every URL had already expired.

    Note:
        Called from the pipeline rather than left to a separate pass, because the ordering is
        the whole point: the extractor inlines stored copies, and a platform URL expires within
        hours of the harvest. A post read before its photo landed is read as text forever.
    """
    rows = list(Media.objects.filter(observation=observation, blob_path="").exclude(source_url=""))
    if not rows:
        return 0

    stored = 0
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as client:
        for media in rows:
            stored += int(download(media, client))
    return stored


def download_pending(event_id: int | None = None, limit: int | None = None) -> Downloaded:
    """
    Fetch every media row still missing its local copy.

    Args:
        event_id (int | None): Restrict to one emergency.
        limit (int | None): Cap on files this pass.

    Returns:
        Downloaded: Counts, including the failures, which are expected rather than exceptional.
    """
    result = Downloaded()
    rows = list(pending(event_id)[:limit] if limit else pending(event_id))
    if not rows:
        return result

    with httpx.Client(timeout=DOWNLOAD_TIMEOUT) as client:
        for media in rows:
            before = media.sha256
            if not download(media, client):
                result.failed += 1
                continue

            result.bytes += media.size_bytes or 0
            if before == media.sha256 and before:
                result.reused += 1
            else:
                result.stored += 1

    logger.info(
        "media: %s stored, %s reused, %s failed", result.stored, result.reused, result.failed
    )
    return result


def _path_for(digest: str, media: Media) -> str:
    """
    Where a file lives, relative to `MEDIA_ROOT`.

    Note:
        Named by content hash and fanned out two levels, so the same photo posted by forty
        accounts is one file on disk and no directory ever holds a hundred thousand entries.
    """
    suffix = Path(media.source_url.split("?")[0]).suffix[:5] or ".bin"
    return f"{digest[:2]}/{digest[2:4]}/{digest}{suffix}"
