# Agent tools

How the deterministic service layer becomes a surface an agent can call, where each piece
lives, and what every tool is contractually allowed to do.

Model detail in [`data-model.md`](data-model.md); search strategy in
[`../HANDBOOK.md`](../HANDBOOK.md). This document is the contract between the two.

**Status:** nine tools built, registered and tested. The agents that consume them are not.

---

## The design principle

**Judgment belongs to the model; functions belong to code.** Every tool is a deterministic
Python function over Postgres/PostGIS/OSRM with no LLM inside it. The agent decides *when*
to call them and writes prose from their results. The model is never asked to compute a
distance, aggregate a quantity, order a set of stops or compose a search query.

This is the same rule as the pipeline's fixed route, applied to the other side of the
system: the pipeline is code deciding the order of model calls, and the tool layer is code
deciding what a model call is able to do at all.

---

## Three layers, not two

The mistake to avoid is a tool module that grows business logic because it was convenient.

| Layer | Location | Knows about | Never |
|---|---|---|---|
| **Service** | `ayudagente/<app>/services/` | Django models, PostGIS, OSRM | imports LangChain, formats for a model |
| **Tool** | `agent_tools/<name>/` | services, JSON serialization, truncation | contains a domain rule or touches the ORM |
| **Agent** | `agent_tools/agents/` *(not built)* | tools, prompts, deepagents composition | imports a service directly |

Two rules keep the layers from collapsing into each other:

1. A domain `if` inside a tool belongs in `services/`. If a tool decides *whether* something
   is allowed, the policy has escaped its home.
2. An agent never imports from `services/`. That is what keeps every service callable from
   a Celery task, a management command and a shell without dragging LangChain along.

The payoff is concrete: `run_matching_pass` runs identically from a schedule and from a
shell, and `propose_match` enforces the same guards whether the batch pass or an agent
calls it.

### Why the tool layer is its own top-level package

`agent_tools/` sits beside `ayudagente/` and `backend/` rather than inside the app whose
models it reads. The reasoning is directional: the tool layer depends on the apps, and
nothing in the apps depends on it. Delete `agent_tools/` and the Django project still runs,
migrates and serves — that one-way dependency is the property worth protecting.

The trade-off, stated plainly: this is the one package outside `ayudagente/`, so CLAUDE.md's
layout rule needs an explicit clause for it. That rule exists so Django *apps* never sprawl
at the root, and `agent_tools/` is not an app — no models, no `AppConfig`, never in
`INSTALLED_APPS`.

```
agent_tools/
    shared.py               failure(), ToolInputError, the resolvers tools share
    registry.py             TOOLSETS
    test_toolset.py         cross-tool contracts
    find_requirements/
        __init__.py
        constants.py        limits and derived enum listings
        input.py            the pydantic schema the model reads
        output.py           row serialization
        find_requirements.py
        tests.py
    get_balance/  get_actor_contacts/  road_distance/  plan_trip_stops/
    propose_match/  draft_outreach/  get_frontier/  create_harvest_job/
```

One folder per tool, named after it. Smaller tools collapse `constants`/`input`/`output`
into the tool module; the split earns its place only when there is enough of each.

### Wiring a package that is not under `ayudagente/`

Three keys in `pyproject.toml` assume the two original top-level packages and must list
`agent_tools`, or it escapes every check the repo runs — `[tool.ruff.lint.isort]
known-first-party`, `[tool.pyrefly] project-includes`, and `[tool.pytest.ini_options]
testpaths`. All three are set.

Django must be configured before any tool module is imported, because importing a service
imports models. Anything loading `agent_tools` outside `manage.py` — a Celery worker, a
LangGraph server, a notebook — calls `django.setup()` first.

Test files must be named `test_*.py` or `tests.py` to be collected.

### What deepagents changes, and what it does not

`create_deep_agent(tools=[...])` consumes ordinary LangChain tools, so the decision to use
deepagents does not change the shape of the tool layer at all. What it adds is a harness:
filesystem middleware, subagent middleware, rubrics.

One caution specific to it. `SubAgentMiddleware` is exactly the capability CLAUDE.md forbids
for pipeline work. Subagents are legitimate for frontier exploration — an open-ended search
over where to look next. Classification, extraction, geocoding and actor resolution stay
Celery tasks on the fixed route. A subagent there buys nothing and costs determinism.

---

## The registry

Grouping is not organization, it is enforcement. The frontier agent must be structurally
incapable of reading a post, and that is guaranteed by what is absent from a list rather
than by a sentence in a prompt asking it not to.

```python
# agent_tools/registry.py
FRONTIER_TOOLS = [get_frontier, create_harvest_job]

COORDINATION_TOOLS = [
    get_balance,
    find_requirements,
    get_actor_contacts,
    road_distance,
    plan_trip_stops,
    propose_match,
    draft_outreach,
]

TOOLSETS = {"frontier": FRONTIER_TOOLS, "coordination": COORDINATION_TOOLS}
```

The two sets share nothing, and a test fixes that. `FRONTIER_TOOLS` reaches `FrontierNode`,
`Event` and `HarvestJob`, and cannot reach an `Observation` through any of them.

`run_matching_pass` is deliberately in no toolset. It rewrites every proposal for an event,
so an agent calling it mid-conversation would invalidate the pairings it had just reasoned
about. It runs on a schedule; the agent reads its output.

---

## Rules every tool follows

- **Translate, never decide.** Ids and primitives in, dicts out. Any branch that is not
  type coercion or truncation is a service concern.
- **Never return a Django object or a QuerySet.** A model instance serialized by accident
  leaks every field into the context window.
- **Cap the rows and say so.** Reads return ten to forty rows with `truncated`, so the
  agent narrows its filter instead of assuming it saw everything.
- **Fail as a value, never an exception.** A raised exception becomes an opaque trace in the
  message history. Every failure returns `{"error": ..., "hint": ...}` plus whatever empty
  collection the tool's contract promises, so a caller reading the list without checking
  `error` sees nothing rather than crashing.
- **Carry the data needed to retry.** An unknown resource returns `available`; an ambiguous
  place returns `candidates`. A hint that says "use a valid key" without saying which are
  valid costs the agent a turn it did not have to spend.
- **The docstring is the prompt.** It is what the model reads when deciding to call. Write
  it for the model: what it answers, when to prefer it over a neighbouring tool, what the
  units are, and what an empty result does *not* mean.

### Argument types

The model can only send JSON scalars, so every tool takes ids and primitives and the
wrapper resolves them. `agent_tools/shared.py` holds the resolvers used by more than one:

| Service takes | Tool takes | Resolver |
|---|---|---|
| `ResourceType` | `resource_key: str` | `resolve_resource_arg` — slug, slugified text, or name without accents |
| `AdminUnit` | `place: str` | `resolve_place_arg` — name or national code, scoped to the event's country |
| `Event` | `event_id: int` | `get_event` |
| `Point` | `lat`, `lon` | `Point(lon, lat, srid=4326)` — lon first |
| `Requirement` | `need_id: int` | fetched with `select_related` in the tool |

Bad arguments raise `ToolInputError`, which each tool catches once and turns into its
failure payload. That is not a violation of "fail as a value": the exception never leaves
the tool. It exists because returning `(value, error)` pairs left every resolved value
permanently `Optional` and defeated type checking.

---

## The tools

### Coordination

**`get_balance(event_id, place?, resource_key?, only_deficits?)`** — the orientation call.
One row per resource, place and unit with `needed`, `offered` and `net`; deepest deficit
first. Its `resource_key` values are exactly what `find_requirements` accepts, which is how
the agent learns the vocabulary without guessing a slug.

Quantities in different units are never added together, so the same resource can appear
twice. `unknown_quantity` counts requirements that stated no amount: a row reading
`needed: 0` with a non-zero `unknown_quantity` means "people are asking but nobody said how
much", which is not the same as covered.

**`find_requirements(event_id, direction, resource_key?, place?, text?, lat?, lon?,
radius_km?, min_precision?, limit?)`** — the detail. Most urgent first, or nearest first
when given a point. `text` searches the original wording for the specificity the coarse
taxonomy deliberately does not carry ("leche de fórmula" lives inside `alimentos`); it
filters and never reorders, because ranking by textual similarity buries a critical need
under a chattier post.

**`get_actor_contacts(actor_id, include_unusable?)`** — channels ordered by
`preference_rank`, and **never the contact value**. The agent gets `contact_point_id`, the
kind and the confidence; `draft_outreach` reads the address itself. A phone number that
never enters a prompt cannot be echoed into a message body or logged by a provider. A
merged actor resolves to the one that absorbed it, so contacts and outreach history stay
together.

**`road_distance(...)`** and **`plan_trip_stops(requirement_ids)`** — the only tools that
leave the process. Both return `error` with a stated fallback when OSRM is unavailable,
because the public server is rate limited and will fail during a demo. `road_distance`
tells the agent the straight-line figure is a floor, never an overestimate.
`plan_trip_stops` orders stops and does not choose them; ten is the ceiling, since OSRM's
trip solver is exponential in the worst case. Route geometry is never returned — hundreds
of coordinate pairs belong on a map, not in a prompt.

**`propose_match(need_id, offer_id, rationale, via_transport_id?)`** — writes a proposal for
a human to review; delivers nothing and contacts nobody. Refused on a crossed direction, a
cross-event pair, a location too coarse to deliver to, a `via_transport` that is not an
offer of transport, or a pairing a human has already acted on. `rationale` is refused when
too short: a coordinator reads it to decide.

**`draft_outreach(contact_point_id, purpose, body, subject?, match_id?,
about_requirement_id?)`** — the last step before a real person. **Nothing is ever sent.**
The result is a draft plus a `target_url`: a `wa.me` or `mailto:` link with the text
prefilled, or a permalink for channels that cannot be. A human clicks it.

Drafting the same message twice returns the existing row rather than creating a second, so
a retry cannot make "we already contacted ten people" untrue — which also means a rewritten
body does not replace an existing draft. Refused when the actor asked not to be contacted,
when the channel cannot carry a message, or when the recipient is not a party to the match.
The body is Spanish: the documented exception to the English rule, because the recipients
are the people in the emergency.

### Frontier

**`get_frontier(event_id, limit?)`** — the only read this agent performs. A few dozen rows,
ordered by `yield_rate`, and not a post among them. `is_unexplored` is sent explicitly
rather than left to be inferred from `passes == 0`, because a share of every round must go
to targets with no history and the agent cannot honour a rule whose input it has to derive.
Timestamps become ages in minutes: "43 minutes ago" is a decision input, an ISO string is
not. Exhausted and paused targets are absent — they are not decisions left to make.

**`create_harvest_job(event_id, node_id, rationale, target_kind?)`** — the agent's only
write. Note what is **not** an argument: the query string and the Apify actor. Both are
built in the service from the event lexicon and a platform map. Two invariants depend on
that — every query carries a real toponym, and other concurrent emergencies' terms are
excluded — and a hallucinated query loses both silently. `rationale` is mandatory; it is
the only record of why a pass was spent here.

---

## The flow these compose into

```
"¿qué falta en Quibdó?"
  → get_balance(event_id, place="Quibdó", only_deficits=True)
      [{resource_key: "agua", needed: 2600, offered: 0, net: -2600, unit: "litros"}]
  → find_requirements(event_id, "needs", resource_key="agua", place="Quibdó")
      the rows, with actor_id and quantities
  → get_actor_contacts(actor_id)          → contact_point_id, no values
  → road_distance(from_requirement_id, to_requirement_id)
  → propose_match(need_id, offer_id, rationale)
  → draft_outreach(contact_point_id, purpose, body)   → a link a human clicks
```

The agent never invents a slug, a coordinate or a query. **The vocabulary travels in the
output of the tools**, which is why every row carries the identifiers the next tool accepts:
`resource_key`, `actor_id`, `contact_point_id`, `node_id`. A tool that returned a pretty
name where the next one expects an id would break the chain, and the model would fill the
gap by inventing.

---

## Where the guarantees live

Not in the tools. A tool that enforced a rule would let the shell and the Celery task
bypass it.

| Guarantee | Enforced in |
|---|---|
| What counts as still actionable — open, window not closed, actor not merged, not saturated | `services.requirements.routable` |
| Minimum precision for a delivery (invariant 7) | `services.matching.MIN_MATCH_PRECISION` |
| Never cross an event boundary | `propose_match`, and `event_id` on every query |
| A match past `proposed` is never rewritten | `Match.is_frozen`, checked in `propose_match` |
| One message per actor per purpose per anchor | `Outreach.idempotency_key` |
| Which channel a human is offered first | `ContactPoint.preference_rank` |
| A query always carries a toponym and excludes rivals | `services.frontier.build_search_query` |

`routable` is the one to watch. `get_balance` and `find_requirements` both go through it,
which is what stops them contradicting each other — before that, the balance counted closed
centres the detail view hid, and the agent read a deficit it could not act on. A test fixes
the agreement.

---

## Open decisions

| Decision | Options | Blocks |
|---|---|---|
| One offer covering several needs | `linear_sum_assignment` is 1:1, so a truckload of water cannot cover three shelters in one pass. Capacity-split virtual rows, or min-cost-flow | realistic demo seeds |
| Time windows and perishability in matching | `ResourceType.perishable`, `window_start`/`window_end` appear in neither `score_pair` nor candidate generation | delivery realism |
| Stale-proposal deletion | The pass deletes every `proposed` row it did not re-produce, including one a human just created by hand | manual proposals |
| Postgres in the default test run | Mark service tests `live`, or narrow `live` to the model provider and Apify | whether `make check` covers the services |
| CLAUDE.md's NetworkX claim | The code uses scipy Hungarian; `networkx` is a dependency nothing imports | doc accuracy |
| CLAUDE.md's layout rule | Needs a clause for `agent_tools/` | doc accuracy |

---

## What is next

`agent_tools/agents/` — the deepagents composition, one module per agent plus its prompt.
It comes last on purpose: by now the toolsets are stable and the prompts can be written
against tools that already behave.

Two things belong in those prompts rather than in the tools, because they are behaviour and
not data: that the catalog is Spanish and must not be translated, and that `get_balance`
comes before `find_requirements`.

---

## Environment

```bash
uv sync
make up                    # PostGIS + pgvector on 5433
uv run manage.py migrate   # extensions, schema, then demo seeds
make check                 # ruff + format + pyrefly + pytest
```

- Extensions are created by migration, not only by `docker/init-extensions.sql`, so the
  database pytest builds carries them.
- `django.contrib.postgres` must stay in `INSTALLED_APPS`. The migration creates the
  extensions, but the `unaccent` and trigram *lookups* are only registered by the app, and
  `place` and `text` fail without them.
- Hosts with a duplicate GDAL pin the working pair in `.env`:
  `GDAL_LIBRARY_PATH=/usr/lib64/libgdal.so.36`, `GEOS_LIBRARY_PATH=/usr/lib64/libgeos_c.so.1`.
- `OSRM_BASE_URL` defaults to the public server. Self-host with a Geofabrik extract before
  relying on it for a demo.
