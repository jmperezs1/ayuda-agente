"""
Registry of loadable development fixtures.

One entry per dataset rather than one management command per dataset, so adding a scenario
means adding a module here instead of another command that does the same thing under a
different name. Every seed owns the name of the `Event` it hangs off, which is what makes
clearing it a single cascading delete.

Nothing here runs in production. Reference data the application needs to function — the
resource catalog, the gazetteer — is loaded by its own command, because a dataset a
deployment depends on has no business sharing a `--clear` flag with a fixture.
"""

from ayudagente.radar.seeds import pilot
from ayudagente.radar.seeds.base import Counts, Seed, Writer

SEEDS: dict[str, Seed] = {seed.name: seed for seed in (pilot.SEED,)}

__all__ = ["SEEDS", "Counts", "Seed", "Writer"]
