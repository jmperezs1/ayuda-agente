"""Load the canonical resource catalog."""

from django.core.management.base import BaseCommand

from ayudagente.radar.models import ResourceType
from ayudagente.radar.services.taxonomy import RESOURCES, load


class Command(BaseCommand):
    """
    Bring the resource catalog to the state `services/taxonomy.py` declares.

    Note:
        Reference data, not a fixture. `Requirement.resource` is a foreign key into this
        table, and the hierarchy is what lets an offer of food cover a need for groceries.
        Without it the pipeline still runs — `ingest` invents a flat entry for any key the
        extractor produces — but every resource becomes an island and matching quietly
        stops substituting.

        Idempotent, and safe to re-run after editing `RESOURCES`: it creates what is
        missing, names the entries the pipeline invented, enforces the hierarchy and folds
        away duplicates left by earlier versions of the catalog.
    """

    help = "Load or refresh the canonical resource catalog. Required in every environment."

    def handle(self, *args, **options) -> None:
        """Load the catalog and report what changed."""
        counts = load(self.stdout.write)

        total = ResourceType.objects.count()
        style = self.style.SUCCESS if total >= len(RESOURCES) else self.style.WARNING
        self.stdout.write(style(f"  {total} resource types in the catalog"))
        if not any(counts.values()):
            self.stdout.write("  nothing to change")
