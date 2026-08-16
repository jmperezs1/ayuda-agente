"""
Create the Postgres extensions the schema depends on.

`docker/init-extensions.sql` only runs on the container's first boot, so it never reaches
the database pytest creates. Declaring them here makes the schema self-contained: any
database Django builds — test, CI, a fresh clone — gets them.

`run_before` points at `0001_initial` because that migration creates the `vector` column,
which cannot exist before the extension does. On a database where the extensions were
already installed by the init script, every operation here is a no-op.
"""

from django.contrib.postgres.operations import (
    CreateExtension,
    TrigramExtension,
    UnaccentExtension,
)
from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    run_before = [("radar", "0001_initial")]

    operations = [
        CreateExtension("postgis"),  # proximity queries and spatial indexes
        VectorExtension(),  # actor resolution by embedding
        TrigramExtension(),  # name similarity, the signal ahead of embeddings
        UnaccentExtension(),  # normalizing place and actor names
    ]
