# AyudAgente

**An autonomous agent for the first 72 hours of a disaster.** It watches for emergencies,
decides where to look, reads what people are posting, and turns it into a map of who needs
what and who is offering it — then hands a coordinator a link to connect the two.

Django 5 · PostgreSQL 16 with PostGIS and pgvector · Celery · OpenAI · Apify

---

## What it does

```text
  USGS feed          a human arms it          social platforms
      │                     │                        │
      ▼                     ▼                        ▼
   detect  ────────────►  active  ────────────►  harvest  ────────────►  read
    free                                          Apify                 OpenAI
                                                                          │
                                            ┌─────────────────────────────┘
                                            ▼
                                    needs  ⟷  offers        ────►   a link a human clicks
                                     matched on the graph            nothing is ever sent
                                                                      automatically
```

Two decisions are made by an LLM agent: **where to harvest next**, and **whether two mentions
are the same real-world actor**. Everything between them is a fixed pipeline — classify,
extract, read the image, geocode, resolve, match — because those steps are high-volume and the
order never changes.

Detection and spending are deliberately separate acts. A feed can propose an emergency; only a
human arms one, and only an armed event may spend a cent.

Read [`CLAUDE.md`](CLAUDE.md) for the decisions behind that and [`HANDBOOK.md`](HANDBOOK.md)
for the search strategy, with per-platform yields measured against a real earthquake.

---

## Quickstart

You need [uv](https://docs.astral.sh/uv/), Docker, and the GEOS/GDAL/PROJ libraries that
`django.contrib.gis` links against (`sudo dnf install gdal geos proj`, or your platform's
equivalent).

Every command below runs from this directory — `cd backend` first if you are at the root of
the monorepo.

```bash
cd backend
make init      # .venv, dependencies, .env from .env.example
make up        # Postgres with PostGIS + pgvector, and Redis
make migrate
make run       # http://127.0.0.1:8000
```

Put your `OPENAI_API_KEY` in `.env` — the demo below reads posts with it. `APIFY_TOKEN` is
only needed to harvest new ones. `make help` lists every command; the database publishes
**5433** so it never collides with a Postgres on the host.

The map that consumes this API lives in [`../frontend`](../frontend/README.md): mint a key with
`make apikey`, then point its `VITE_API_KEY` at it.

---

## The demo, end to end

The repository ships a real corpus: **939 items harvested from the Chocó earthquake**
(M7.4, 10 August 2026) across Facebook, Instagram, TikTok and X. They arrive **unread**, so
the demo is the pipeline actually reading them.

```bash
make taxonomy              # the resource catalog — the skeleton new resources hang from
make gazetteer ARGS=CO     # Colombia's places, from GeoNames
make seed                  # the 939 posts, hanging off the pilot event
```

Then read a slice of them and draw the result:

```bash
make pipeline ARGS="1 --limit 25 --yes"   # spends OpenAI: one multimodal call per post
make graph ARGS="--event 1"               # match needs to offers, redraw the map
```

`--limit 25` is what makes this cheap. Drop the flag to read all 939.

Watch it happen from a second terminal — `narrate` starts level with the database, so it
speaks what arrives rather than what is already there:

```bash
make narrate               # start this first, then run the pipeline
```

```text
  read       25 posts — 12 needs, 6 offers
  proposed   Agua: Comunidad Cristo Rey → Coliseo Mayor de Quibdó
```

### Query it

```bash
make apikey                # mints a key into .env; restart make run to pick it up
curl -H "X-API-Key: <key>" http://127.0.0.1:8000/api/events/1/requirements/
```

The full contract is in [`docs/api.md`](docs/api.md), and the streaming agent endpoints — the
ones a frontend asks *"¿qué falta en Quibdó?"* — in [`docs/agent-api.md`](docs/agent-api.md).
The Django admin at `/admin/` (`make superuser`) is the fastest way to browse the rows.

### Start over

```bash
make unseed                # removes the fixtures, keeps the taxonomy and the gazetteer
```

### Let it run itself

Nothing above is required for the loop; it is how you see the loop's output without waiting.
To run it unattended against a live emergency, run `make unseed` first — the pilot event is
active, and a loop left running would harvest it for real — then arm a real one:

```bash
make watch                 # poll USGS — free, proposes paused events, scrapes nothing
make events                # what is waiting, and what may spend
make arm ARGS="<id>"       # authorise it, and queue its first sweep
make worker                # and `make beat` in another terminal
make tick                  # beat once now, instead of waiting TICK_SECONDS
```

From there it paces itself on novelty and stops on the spend ceiling.
[`docs/operating.md`](docs/operating.md) covers both.

---

## Development

```bash
make check                 # ruff lint + format, comment style, pyrefly, pytest
```

Individually: `make lint`, `make format`, `make comments`, `make types`, `make test`.

Tests live next to the code they cover. Anything reaching Postgres, OpenAI or Apify is marked
`live` and excluded from the default run, so `make test LIVE=1` is the opt-in.

Formatting is ruff's defaults at 100 columns. Type checking is pyrefly with `django-stubs`,
pinned to Python 3.12 to match `requires-python` rather than the local interpreter. Add
dependencies with `uv add <package>`.

## Layout

```text
backend/          Django project config
ayudagente/       product package — every app lives in here, never at the repo root
    radar/        harvest, graph and search frontier (app label: radar)
agent_tools/      the tools the agents call
data/pilot/       the committed corpus, as the Actors returned it
docs/             architecture, API and runbooks
docker/           database image and init scripts
```

## Documentation

| Document | What it covers |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | the architecture and the invariants that must not drift |
| [`HANDBOOK.md`](HANDBOOK.md) | the search strategy, with measured yields and costs |
| [`docs/data-model.md`](docs/data-model.md) | what each model is for and how they relate |
| [`docs/api.md`](docs/api.md) | the read API the frontend codes against |
| [`docs/agent-api.md`](docs/agent-api.md) | the agent endpoints and their event stream |
| [`docs/agent-tools.md`](docs/agent-tools.md) | what the agents can actually do |
| [`docs/operating.md`](docs/operating.md) | running the loop, watching it, stopping it spending |
| [`docs/deploy.md`](docs/deploy.md) | the deployment and its runbook |
