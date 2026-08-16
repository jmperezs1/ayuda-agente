"""
Loading a country's administrative divisions from GeoNames.

`AdminUnit` does something a geocoder cannot: it *enumerates*. Google resolves a string you
already have; this answers "which searchable places exist in Colombia", which is the question
the sweep and the frontier are built on. It is also what keeps the agent from inventing place
names — every query carries a toponym that exists.

The source is GeoNames' per-country dump, one file per ISO country code, so the same command
loads Colombia today and Indonesia tomorrow. Nothing here is Colombia-specific.

Note:
    Only ADM1 and ADM2 are loaded — departments and municipalities in Colombia, states and
    counties elsewhere. ADM3 exists in the dump but a sweep never queries at that level: you
    find a vereda because somebody mentions it in a post the municipal query returned, not by
    enumerating thirty thousand hamlets.

    Population is loaded because it is the only cold-start ranking signal there is. Before any
    harvest, "which municipality is worth a query" has no answer except how many people live
    there and how close it is to the epicenter.
"""

import csv
import io
import logging
import zipfile
from collections import Counter
from dataclasses import dataclass

import httpx
from django.contrib.gis.geos import Point

from ayudagente.radar.choices import AdminLevel
from ayudagente.radar.models import AdminUnit
from ayudagente.radar.services.text import normalize

logger = logging.getLogger(__name__)

DUMP_URL = "https://download.geonames.org/export/dump/{country}.zip"
DOWNLOAD_TIMEOUT = 120.0

# GeoNames' tab-separated columns, by position. The dump carries no header row.
GEONAME_ID, NAME, ALTERNATE_NAMES, LATITUDE, LONGITUDE, FEATURE_CODE = 0, 1, 3, 4, 5, 7
ADMIN1_CODE, ADMIN2_CODE, POPULATION = 10, 11, 14

FEATURE_TO_LEVEL = {"ADM1": AdminLevel.ADMIN_1, "ADM2": AdminLevel.ADMIN_2}

# Share of a level's names a word must appear in to count as administrative rather than a place
COMMON_WORD_SHARE = 0.15


class GazetteerError(RuntimeError):
    """The dump could not be fetched or read, with the country named."""


@dataclass
class Loaded:
    """
    What a load produced.

    Attributes:
        created (int): Units inserted.
        updated (int): Units already present whose name, centroid or population changed.
        skipped (int): Rows the dump carries that this loader does not store.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0


def fetch_dump(country_code: str, client: httpx.Client | None = None) -> list[list[str]]:
    """
    Download and unzip one country's GeoNames dump.

    Args:
        country_code (str): ISO 3166-1 alpha-2, upper case.
        client (httpx.Client | None): Override for tests.

    Returns:
        list[list[str]]: Every row, already split on tabs.

    Raises:
        GazetteerError: On a failed download or an archive without the expected member.
    """
    url = DUMP_URL.format(country=country_code.upper())
    try:
        with client or httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as http:
            response = http.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GazetteerError(f"could not download the gazetteer for {country_code}: {exc}") from exc

    member = f"{country_code.upper()}.txt"
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            text = archive.read(member).decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise GazetteerError(f"{url} is not a readable dump containing {member}: {exc}") from exc

    return list(csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def load_country(country_code: str, rows: list[list[str]] | None = None) -> Loaded:
    """
    Store a country's ADM1 and ADM2 units, parents before children.

    Args:
        country_code (str): ISO 3166-1 alpha-2.
        rows (list[list[str]] | None): Pre-fetched dump rows, for tests.

    Returns:
        Loaded: Counts. Re-running updates in place rather than duplicating.

    Raises:
        GazetteerError: When the dump cannot be read.

    Note:
        ADM1 is loaded first and kept in memory so ADM2 can be parented without a query per
        municipality. A country has a few dozen first-level divisions, so the map is small and
        the alternative is eleven hundred lookups.
    """
    code = country_code.upper()
    rows = rows if rows is not None else fetch_dump(code)

    result = Loaded()
    by_admin1: dict[str, AdminUnit] = {}

    for feature, level in FEATURE_TO_LEVEL.items():
        at_level = [row for row in rows if len(row) > POPULATION and row[FEATURE_CODE] == feature]
        common = administrative_words([" ".join(row[NAME].split()) for row in at_level])

        for row in at_level:
            parent = by_admin1.get(row[ADMIN1_CODE]) if feature == "ADM2" else None
            if feature == "ADM2" and parent is None:
                result.skipped += 1  # a municipality whose department is not in the dump
                continue

            unit, created = _store(code, row, level, parent, common)
            if feature == "ADM1":
                by_admin1[row[ADMIN1_CODE]] = unit
            result.created += int(created)
            result.updated += int(not created)

    logger.info(
        "gazetteer %s: %s created, %s updated, %s skipped",
        code,
        result.created,
        result.updated,
        result.skipped,
    )
    return result


def administrative_words(names: list[str]) -> set[str]:
    """
    The words that label a division rather than name it.

    Args:
        names (list[str]): Every unit name at one level of one country.

    Returns:
        set[str]: Lowercased words shared by many of them — "departamento", "de", "del" in
            Colombia, "province" or "governorate" elsewhere.

    Note:
        Derived from frequency rather than from a list, which is what keeps this global. A
        toponym appears once in a country; the word for "department" appears in almost every
        name. No dictionary to maintain, and it works in a language nobody here reads.
    """
    counts = Counter(word.casefold() for name in names for word in set(name.split()))
    threshold = max(2, len(names) * COMMON_WORD_SHARE)
    return {word for word, count in counts.items() if count >= threshold}


def search_name(official: str, alternates: str, common: set[str] | None = None) -> str:
    """
    The name people actually write, which is what a query has to carry.

    Args:
        official (str): The dump's primary name, often the administrative long form.
        alternates (str): GeoNames' comma-separated alternates column.
        common (set[str] | None): Administrative words for this country and level.

    Returns:
        str: The searchable toponym, or the official name when nothing better is available.

    Note:
        This decides whether the sweep finds anything at all. Nobody posts "Departamento del
        Huila"; they post "Huila", and a query carrying the official form returns nothing.

        Two passes, and the order was settled by running both over Colombia's 33 departments.
        Stripping administrative words off the edges handles the common shape and keeps
        qualifiers that matter: "Departamento del Valle del Cauca" becomes "Valle del Cauca",
        not "Valle" — and not "Cauca", which is a different department. What that pass cannot
        touch — "Distrito Capital de Bogotá", "Quindío Department" — falls through to the
        shortest alternate that is a run of whole words. Whole words are load-bearing there:
        by substring, "Uila" is a valid shortening of "Huila".
    """
    name = " ".join(official.split())
    words = name.split()
    common = common or set()

    start, end = 0, len(words)
    while start < end and words[start].casefold() in common:
        start += 1
    while end > start and words[end - 1].casefold() in common:
        end -= 1
    if (start or end < len(words)) and words[start:end]:
        return " ".join(words[start:end])

    runs = {
        " ".join(words[left:right])
        for left in range(len(words))
        for right in range(left + 1, len(words) + 1)
    }
    listed = {" ".join(part.split()) for part in alternates.split(",") if part.strip()}
    viable = [candidate for candidate in listed & runs if len(candidate) < len(name)]
    return min(viable, key=len) if viable else name


def _store(
    country_code: str,
    row: list[str],
    level: str,
    parent: AdminUnit | None,
    common: set[str],
) -> tuple[AdminUnit, bool]:
    """
    Insert or refresh one administrative unit.

    Returns:
        tuple[AdminUnit, bool]: The unit, and whether it was created.

    Note:
        Matched on `(country, level, code)` rather than on `geonames_id`, because that triple
        is the uniqueness constraint the rest of the system joins on. A unit whose GeoNames id
        changed between dumps is the same place.
    """
    national_code = row[ADMIN2_CODE] if level == AdminLevel.ADMIN_2 else row[ADMIN1_CODE]
    name = search_name(row[NAME], row[ALTERNATE_NAMES], common)

    return AdminUnit.objects.update_or_create(
        country_code=country_code,
        level=level,
        code=national_code or row[GEONAME_ID],
        defaults={
            "geonames_id": int(row[GEONAME_ID]),
            "name": name,
            "name_norm": normalize(name),
            "parent": parent,
            "centroid": _point(row),
            "population": int(row[POPULATION] or 0) or None,
        },
    )


def _point(row: list[str]) -> Point | None:
    """The unit's centroid, or None when the dump has no usable coordinates."""
    try:
        return Point(float(row[LONGITUDE]), float(row[LATITUDE]), srid=4326)
    except (TypeError, ValueError):
        return None
