"""Stable catalogs: administrative geography and the resource taxonomy."""

from django.contrib.gis.db import models as gis
from django.contrib.postgres.fields import ArrayField
from django.db import models

from ayudagente.radar.choices import AdminLevel


class AdminUnitManager(models.Manager):
    """Looks a place up by the fields that identify it everywhere but in this database."""

    def get_by_natural_key(self, country_code: str, level: str, code: str):
        """Resolve the unique constraint, which is what a fixture carries instead of an id."""
        return self.get(country_code=country_code, level=level, code=code)


class ResourceTypeManager(models.Manager):
    """Looks a resource up by its slug, which is stable across databases."""

    def get_by_natural_key(self, key: str):
        """Resolve the slug a fixture carries instead of an id."""
        return self.get(key=key)


class AdminUnit(models.Model):
    """
    An administrative division of any country, loaded from a global gazetteer (GeoNames).

    It is the backbone of geographic scoping, and it does something a geocoder cannot: it
    *enumerates*. Google resolves a string you already have into coordinates; this answers
    "which searchable places exist in this country", which is what the frontier iterates over.
    Walking real entities is also what keeps the agent from inventing place names.

    Note:
        The hierarchy is country → admin_1 → admin_2 → admin_3 via `parent`, which is the
        GeoNames convention and therefore works the same in Colombia, Indonesia or Turkey.
        The `centroid` lets us rank places by distance to the epicenter without geocoding
        anything.

    See:
        `ayudagente.radar.models.frontier.FrontierNode`,
        `ayudagente.radar.models.actors.Location`.
    """

    objects = AdminUnitManager()

    geonames_id = models.IntegerField(unique=True, null=True, blank=True)
    country_code = models.CharField(max_length=2, db_index=True)  # ISO 3166-1 alpha-2
    code = models.CharField(max_length=20)  # national code where one exists
    name = models.CharField(max_length=200)
    name_norm = models.CharField(max_length=200, db_index=True)  # unaccented, lowercased
    level = models.CharField(max_length=20, choices=AdminLevel.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    centroid = gis.PointField(geography=True, null=True, blank=True)
    population = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "level", "code"], name="admin_unit_unique"
            )
        ]
        indexes = [models.Index(fields=["country_code", "level", "name_norm"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.country_code}/{self.level})"

    def natural_key(self) -> tuple[str, str, str]:
        """The unique constraint, so a fixture can name a place without knowing its id."""
        return (self.country_code, self.level, self.code)


class ResourceType(models.Model):
    """
    Hierarchical taxonomy of what people need and offer.

    It is a table rather than an enum so resources can be added mid-emergency without a
    migration. The hierarchy allows matching by category when there is no exact match: a
    need for "sleeping mats" can be met by an offer of "bedding" when nothing closer exists.

    Note:
        `perishable` constrains transport matching — a perishable resource requires a
        short delivery window.

        `alternate_keys` is what stops the catalog from splitting. The extractor guesses a
        slug per item and the guesses drift — "colchonetas" one day, "sleeping_mats" the
        next — and each drift used to become its own island that matched nothing. Resolving
        a drifted guess records it here, so the second post costs a lookup instead of a
        model call, and the two never become two rows.
    """

    objects = ResourceTypeManager()

    key = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    alternate_keys = ArrayField(models.SlugField(max_length=60), default=list, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    default_unit = models.CharField(max_length=30, blank=True)
    perishable = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.name

    def natural_key(self) -> tuple[str]:
        """The slug, so a fixture can name a resource without knowing its id."""
        return (self.key,)

    def ancestors(self) -> list["ResourceType"]:
        """
        Return the chain of parent categories, nearest first.

        Returns:
            list[ResourceType]: Ancestors in order; empty when this is a root resource.
        """
        chain, current = [], self.parent
        while current is not None:
            chain.append(current)
            current = current.parent
        return chain
