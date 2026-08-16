# AyudAgente — backend

An autonomous agent that discovers, scopes and prioritizes actionable information during a
disaster, then connects the people who need help with the people offering it. Django 5 +
PostgreSQL 16 with PostGIS and pgvector.

Read [`HANDBOOK.md`](HANDBOOK.md) for the search strategy and [`docs/data-model.md`](docs/data-model.md)
for how the models relate. This file is the short version plus the rules that must not drift.

## Commands

```bash
make init                          # .venv, deps, .env from .env.example
make up                            # Postgres (PostGIS + pgvector) and Redis
make migrate
uv run manage.py load_taxonomy     # resource catalog — every environment needs it
uv run manage.py load_gazetteer CO # a country's places, per country
uv run manage.py watch_events      # poll USGS, propose new events — free, scrapes nothing
uv run manage.py arm_event <id>    # give a proposed event permission to be harvested
uv run manage.py start_event ...   # open an emergency by hand and queue its sweep
make check                         # ruff + comment style + pyrefly + pytest
```

`make seed` is **development only**: it loads the pilot corpus. Anything a deployment depends
on has its own loader, because reference data has no business sharing a `--clear` flag with a
development fixture.

`make help` lists everything. The database container publishes **5433**, not 5432, so it does
not collide with a Postgres installed on the host.

## Layout

```
backend/          Django project config
ayudagente/       product package — every app lives in here, never at the repo root
    radar/        harvest, graph and search frontier (app label: radar)
        choices.py    all TextChoices, centralized
        models/       split by layer, re-exported from the package
        views/        the read API, split by entity — plain Django, no DRF
        tests/        next to the code they cover
docs/             architecture documentation
docker/           database image and init scripts
```

New apps go inside `ayudagente/` and register as `ayudagente.<name>` in `INSTALLED_APPS`.

## The architecture decision that governs everything

**The code owns the route. The LLM owns the judgment inside each step.**

Every observation walks the same fixed sequence: classify → extract → read image → build the
geocoding string → geocode → resolve actor → match. Five of those steps call a model; the
*order* is plain Python. Steps 1–4 are deliberately **one** multimodal call with a JSON schema,
because the per-model rate limit is the real bottleneck and because the model resolves
text-vs-image contradictions better when it sees both at once.

An LLM agent runs in exactly two places:

1. **The frontier agent** decides what to harvest next — which municipality, which platform,
   which query, how much to spend. It reads only `FrontierNode` (~50 rows, ~2K tokens) and
   writes `HarvestJob` rows. **It never sees a post.**
2. **Actor adjudication** decides whether two mentions are the same real-world entity, and only
   for pairs the cheap signals could not settle.

Do not turn pipeline steps into agents or subagents. They are high-volume and fixed, not
open-ended, so a reasoning loop buys nothing and costs latency, money and determinism. The
payoff of the fixed route is concrete: a 429 from OpenAI retries one task instead of corrupting
an agent's message history, 50 observations process in parallel under a concurrency cap, and a
bad field is diagnosed by looking at one step's output rather than re-reading a trace.

## Scope is global

The pipeline is not Colombia-specific. `AdminUnit` is loaded from GeoNames and uses
`country → admin_1 → admin_2 → admin_3`, `Event` carries `country_code` and `languages`, and
payment rails are one `ContactKind.PAYMENT` with the network as data (nequi, pix, mpesa, upi).

What does *not* transfer is the calibration: which platform yields what, which queries work,
what an actionable item costs. Those numbers come from one Spanish-language Colombian
earthquake and have to be re-measured per country and language.

## The loop runs unattended, and novelty is what paces it

`tick` beats every `TICK_SECONDS`: dispatch the harvests already decided, then let the frontier
agent decide more. Harvest first — a round that runs before its predecessor's jobs have been
executed reads a scoreboard where nothing has moved.

**Every round is a fresh conversation.** The checkpointer keeps state in Postgres, so reusing
one thread would carry every previous round's tool calls into the next prompt — sixteen rounds
overnight and the agent reads its own history instead of the scoreboard. Nothing is lost by
forgetting, because the memory that matters is in the database: `job_in_flight` says what is
already queued and `create_harvest_job` refuses a target harvested minutes ago.

An emergency has no natural end, so the loop needs a reason to wait, and the honest one is
**novelty**. `HarvestJob.items_new` counts what survived deduplication; a pass returning two
hundred items of which five are new has exhausted its queries for the moment. That is a quality
signal, the same kind as `yield_rate`, so the cost invariant below still holds.

Low novelty is a wait, not an end. The measurement is taken over recent jobs, so without an
escape a quiet hour would keep the loop asleep for good — past `PROBE_AFTER` a round runs
regardless, because the only way to learn the world moved is to look.

Three things stop it outright: a paused event, a queue deep enough that harvest is the
bottleneck, and `HARVEST_SPEND_CEILING_USD`. That last one is a circuit breaker, not a budget —
nothing weighs it against anything, it exists so a runaway loop at three in the morning stops
instead of billing until someone wakes up. Tripping it pauses the event, because
`Event.is_harvestable` is already the kill switch every writer checks.

## Cost is recorded, never used to decide

Sweeping is cheap — you batch toponyms into one query per platform × zone × axis, not one
query per municipality. The pilot covered a country in ten queries for $0.10. So there is no
budget to allocate, and `FrontierNode` has no cost-weighted score.

`Event.spent_usd` and `HarvestJob.actual_cost_usd` are recorded because Apify returns them
free and a runaway loop should be visible. Nothing reads them to make a decision. What decides
is `yield_rate`: keep looking where the content is good, stop where it is noise.

The one allocation decision that survives is **depth**: `HarvestJob.target_kind` separates a
cheap `search` from a `profile`, `thread` or `comments` pull.

## Invariants

Breaking any of these is a bug even when tests pass.

1. `Observation` is never updated or deleted. New information is a new row.
2. `Extraction` never writes into `Observation`.
3. A `Match` past `proposed` is never rewritten by the matching pass — a human is already
   involved.
4. **Nothing is ever sent automatically.** Every channel resolves to a deep link a human
   clicks. `Outreach.target_url` plus the body is the entire dispatch mechanism.
5. `Outreach` is only ever created through its idempotency key.
6. Actor merges set `merged_into`; they never delete.
7. Every `Location` carries its precision, and matching enforces a minimum.
8. A `FrontierNode` watches exactly one of an `admin_unit` or an `actor`.
9. Every scraping query carries a toponym from the event's country, or it pulls in other
   countries' disasters.
10. Cost is recorded, never used to decide.
11. A requirement nothing corroborates is `unverified`: matched and shown like any other,
    carrying the label. It is marked, never hidden.
12. A second sighting of the same actor, resource and direction attaches as evidence. It
    never becomes a second row.

## Identity resolution is a cascade

Deterministic keys → blocking by municipality → trigram similarity (`pg_trgm`) → embeddings
(`pgvector`) → LLM adjudication. **Embeddings are the fourth signal, not the first**:
"Coliseo Mayor" and "Coliseo Menor" are nearly identical as vectors and are different places.

The same cascade runs over `ResourceType` in `services/resources.py`, minus the embedding
stage — an emergency produces dozens of distinct resources, not thousands of actors, so the
layer would cost a vector column to save almost nothing. Every resolution is written back to
`alternate_keys`, so a drifted guess costs one model call per emergency rather than one per
post.

## The resource catalog is open, and every arrival is placed

A flood asks for sandbags and a wildfire for N95 masks; neither is in the seeded categories,
so an unfamiliar key becomes a real resource rather than being dropped. What the seed provides
is not the list but the **skeleton** — the categories a new arrival hangs from.

That placement is the part that matters. `resource_family` is a resource plus its ancestors
and descendants, so a resource created without a parent can only ever match itself: a need
nobody will be proposed to fill, failing silently. New resources are therefore created under a
parent the model chooses, never as roots.

The catalog has already split once in production — `agua` beside `water`, `transporte` beside
`transport`, four more — and the fix was a hand-written `LEGACY_KEYS` list. Those exact keys
now resolve on trigram alone. Anything that writes `ResourceType` directly instead of going
through `resolve_resource` reintroduces that split.

## The person asking is a member of the public

Not an emergency professional watching a dashboard. A neighbour with a truck, a family with no
water, someone holding a bag of clothes who does not know where to take it. They read one
sentence in a chat and then drive somewhere or call someone.

That decides several things. The agent has to *say* how solid an answer is, because nobody is
going to filter a list — "lo vi en un solo post y nadie lo ha confirmado" is part of the
answer, not metadata. It has to hand over the contact so they can check before travelling. And
a wrong answer costs a real trip, which is why the tools carry `confirmed`, `sources` and
`actor_verified` on every row.

## Weak evidence is labelled, not gated

A requirement is born `unverified` unless something backs it: two independent posts, a
platform-verified account, enough credibility, or an organisation reporting a need. That label
travels with it and is what the frontend shows. It is **not** a gate — the row is matched,
proposed and drawn like any other.

This was a gate once, and against a live corpus it hid 90% of everything found. The gate was
also unnecessary, and invariant 4 is why: **nothing is ever sent automatically**. Every message
resolves to a link a human clicks, and `covered_quantity` counts only once they have. A wrong
requirement therefore cannot deliver anything, cannot saturate a real need and cannot reach a
stranger. It costs a coordinator a glance — and one who can see how well backed a thing is
spends that glance well.

**Needs and offers still differ.** A false need wastes a trip; a false offer makes a real need
look covered. So the bar for calling an offer well-backed is higher, and the frontend has that
difference to show.

The rule to keep: **filter for what cannot be acted on, never for what might be wrong.** A
`discard`, a confidence under the floor and a place that does not resolve are all things
nothing can be done with. Everything else is a judgement, and judgement belongs to whoever is
looking at the screen.

## Corroboration only exists because rows are merged

A second post about the same actor, resource and direction attaches to the existing
requirement instead of creating another. Before that, corroboration was 0% *by construction* —
a live run produced 162 rows from 60 posts of which 38 were repeats, four identical "punto
oficial de acopio" rows for one actor. Every repeat was a node the map drew twice, and no
requirement could ever have two pieces of evidence.

Merging fixes three things at once: the graph shrinks, `covered_quantity` starts meaning
something, and the only automatic route out of quarantine becomes reachable.

## Marking the graph behind is not the same act as rebuilding it

The graph is served from `GraphSnapshot`. Writes mark it `stale` with one synchronous UPDATE
that cannot fail, and *then* ask a worker to rebuild. Whoever reads next rebuilds it inline if
nothing did.

The two used to be one act, and the failure had no error in it: an event reported 803
requirements through its summary and none through its graph, because the summary counts rows
while the graph served a cache that only rebuilt when no cache existed at all. The rebuild had
been delegated to a worker the deployment deliberately did not run, so nothing rebuilt it and
nothing recorded that it needed rebuilding. Neither endpoint was wrong by its own logic.

**A correctness guarantee may not live only in a process that might not be running.** The
worker makes the rebuild timely; it must never be what makes it happen at all. The same holds
for the inline pipeline, which rebuilds before it returns because it is the one path that knows
it changed everything and has nobody to tell.

## Matching runs on an in-memory graph

Postgres is the source of truth; the matching pass loads open requirements into NetworkX,
solves an allocation problem, writes results back and discards the graph. Pairwise greedy
matching leaves needs uncovered that had a solution. Connected-component analysis on that graph
gives the most valuable alert in the system: needs with no reachable supply.

No graph database. The most complex query is a two-hop join with a spatial index.

## Code style

**Everything in files is English** — identifiers, docstrings, comments, docs, commit messages,
log lines, API keys, database identifiers. The exception is content an end user reads: outreach
message bodies and UI strings are Spanish, because the recipients are Colombian. LLM prompts are
written in English; their *output* follows the audience.

Formatting and linting are enforced by `make check`:

- ruff format defaults — double quotes, 100 columns
- ruff lint `E, F, I, UP, B, SIM, RUF`; no `D`, because the repo documents in sectioned prose
- `*/migrations/*` is excluded — Django writes those
- `RUF012` is off inside `models/` — Django's `Meta` is a declarative API, not mutable state
- pyrefly with `django-stubs`, `min-severity = "warn"`, pinned to Python 3.12 to match
  `requires-python`

### Comments

One line, short, saying *what* is done. End-of-line or a single line above. Never multi-line
blocks explaining rationale or alternatives — they bury the logic. If the code already says it,
drop the comment. When rationale matters, compress it into the docstring's `Note:` section.

```python
self.is_organization = self.kind in ORGANIZATION_KINDS
root.setLevel("WARNING")  # quiet by default, no list of names to maintain
```

### Docstrings

Sections, never a wall of prose. A short summary first, then whichever of these apply:
`Args:` / `Returns:` / `Raises:` / `Note:` / `See:`. Args entries carry the type in parens.

Model docstrings say what the model is *for* and why its boundaries fall where they do — the
design decision, not a restatement of the fields. The fields are already in the code.

```python
def allows_automatic_outreach(self) -> bool:
    """
    Decide whether the system may write through this channel without human approval.

    Returns:
        bool: True only when every condition holds. This fails closed, because the
            default when writing to someone during an emergency has to be not to write.
    """
```

### Tests

Next to the code they cover. The default run is hermetic; anything reaching Postgres, OpenAI or
Apify is marked `live` and excluded, so `make test LIVE=1` is the opt-in.

## Status

**Built:** data model (16 models, migrated), the deterministic service layer
(`services/matching.py`, `outreach.py`, `requirements.py`, `routing.py`, `graph.py`), the
harvest → normalize → extract → geocode → identity → ingest pipeline and its Celery tasks,
both agents, the HTTP API behind an API key ([`docs/api.md`](docs/api.md)), tooling, docker
stack and its runbook ([`docs/deploy.md`](docs/deploy.md)).

**Not built yet:** nothing from the original slice list. The watch stage landed in
`services/watch.py`, promotion and automatic `exhausted` in `services/promotion.py`, plus media
download and the dispatch write endpoint. The `unverified` quarantine was dropped rather than
built: it is a label, for the reasons above.

**Detecting and arming are two acts, and that is the cost control.** `watch_events` reads USGS
— free, no key — and writes candidates as `paused`, which every writer already refuses through
`Event.is_harvestable`. So a candidate costs nothing and `arm_event` is the single place a human
decides to spend. Do not let detection activate anything, however obvious the disaster: the
guarantee that a feed cannot start billing is worth more than the minutes it saves.

A candidate whose country has no gazetteer is reported and *not* recorded. It could not have
been swept anyway, and a row nothing can act on looks like progress.

**Promotion is built and starved.** `promote_accounts` requires an `Actor` behind the posting
handle, and that link is only written when the model reads the account as the author. Against a
live corpus that found fifteen accounts worth following and promoted none. The gate is right —
inventing an `Actor` would put a press aggregator on the map as a place to drive to — so what
has to improve is the authorship reading, not the gate.

**296 posts were read before their photos were on disk** and went through as text only. The
files exist now; re-reading those observations is the one action that would recover what the
model never saw. The ordering that caused it is fixed in `process_observation`.

**The harvest loop is closed and that is load-bearing.** `services/harvest.py` runs a job and
writes back through `record_harvest`; `tasks.process_observation` credits the target through
`record_actionable_find`. Without both, `FrontierNode` never changes and an agent running every
half hour reads identical rows and queues identical jobs — it repeats rather than learns. Any
change that bypasses those two writers reintroduces that.

**Novelty is the pacing signal, not cost.** `HarvestJob.items_new` says how much a pass added
after deduplication. A round that returns 200 items of which 195 are already held has exhausted
that query for now, and the answer is to wait, not to spend more. This stays consistent with
"cost is recorded, never used to decide": novelty is quality, not price.

**Known gap to respect when building extraction:** `Requirement.evidence` is many-to-many to
`Observation` because one post can legitimately produce several requirements. A post listing
three collection centers produces three. Code that assumes one-post-one-requirement will
silently drop two of them.

**Not validated:** Instagram/Facebook stories. The pilot covered posts, comments and accounts;
stories are ephemeral and usually need an authenticated session, so treat any story support as
unproven until someone spends real credit on it.
