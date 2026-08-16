FROM postgis/postgis:16-3.4

# The model needs PostGIS (proximity queries) and pgvector (actor resolution by embedding)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
