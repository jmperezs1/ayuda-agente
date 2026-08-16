# Operating AyudAgente

How to run the system, watch it work, and stop it spending. For what it does and why, read
[`../CLAUDE.md`](../CLAUDE.md) and [`../HANDBOOK.md`](../HANDBOOK.md).

## What costs money

This is the only thing worth memorising, because every command below is safe or expensive on
these grounds alone.

| Stage | Command | Cost |
|---|---|---|
| Detect an emergency | `watch` | free — reads a public USGS feed |
| Authorise it | `arm` | free — queues jobs, scrapes nothing |
| Fetch posts | `harvest` | **Apify credit, per query** |
| Read posts | `pipeline` | **OpenAI, one multimodal call per post** |
| Match and draw | `graph` | free — NetworkX and a spatial join |
| Load places | `gazetteer` | free — a GeoNames download |
| Fixtures | `seed` | free — writes rows directly |

Detection and authorisation are deliberately separate acts. A feed cannot decide to spend your
credit; a human does, once, by arming an event.

## Where a command runs

One vocabulary, two places:

```
make events        # your machine, against the local stack
make prod.events   # api.ayudagente.help
```

`make help` lists both. The `prod.` targets work from your laptop over SSH and from inside the
server unchanged — they detect which one they are on. Pass extra flags through `ARGS`:

```
make prod.pipeline ARGS="1 --limit 50 --yes"
```

## The life of an emergency

```
watch  ──►  paused  ──►  arm  ──►  active  ──►  harvest  ──►  pipeline  ──►  graph
 free                    free                    Apify         OpenAI        free
```

```bash
make prod.watch                    # propose whatever USGS reported
make prod.events                   # see what is waiting, and what may spend
make prod.arm ARGS="3"             # authorise event 3 and queue its sweep
make prod.harvest ARGS="3 --yes"   # run the queued jobs
make prod.pipeline ARGS="3 --yes"  # read what came back
make prod.graph ARGS="--event 3"   # match and redraw
```

`arm` loads the event's country from GeoNames when nothing local covers it, so a quake in a
country you have never touched arms without preparation. It refuses if that load comes back
empty: a sweep with no toponym queries other countries' disasters.

`pipeline` rebuilds the graph when it finishes, so the last step is only needed after something
else wrote requirements.

## A fresh database

Deploying creates no rows. It leaves a migrated database and nothing else, so every write stays
a deliberate act:

```bash
make prod.deploy ARGS="--reset"    # wipe the volume, migrate, stop
make prod.taxonomy                 # the resource catalog — every environment needs it
make prod.gazetteer ARGS=CO        # a country's places
make prod.seed                     # the pilot corpus and the demo scenario
```

**The gazetteer is not optional before `watch`.** Arming downloads the country it needs, but
detection resolves a quake's country by finding loaded places near its epicentre — with an
empty gazetteer `watch` proposes nothing at all and says so only in the log. Load a country and
`watch` sees the quakes in it; load none and it sees none.

`make prod.unseed` removes the fixtures again. It deletes the seeded events and everything that
hangs off them, and leaves the taxonomy and the gazetteer alone — which is what makes it the
right thing to run before going live.

The seeded pilot corpus arrives **unread** — 922 posts with no extraction. That is deliberate:
reading them costs money. Turn them into requirements with `make prod.pipeline ARGS="1 --yes"`,
or a slice of them with `--limit`.

## Going live

The fixtures and a live run are not meant to share a database. Seeded events carry a corpus
somebody already harvested; a live run is the system finding its own. Clear the first before
starting the second:

```bash
make prod.unseed                   # the seeded events go, the reference data stays
make prod.watch                    # USGS proposes what it currently reports
make prod.events                   # see what is waiting
make prod.arm ARGS="<id>"          # authorise it
make prod.workers                  # worker + beat
make prod.tick                     # do not wait TICK_SECONDS for the first beat
```

The pilot event carries the real USGS id of the quake it came from, so a `watch` run while the
fixtures are loaded recognises it rather than proposing the same emergency twice. Seeding after
a watch still makes two, because the fixture matches on name and the proposal on id — so seed
first, or unseed before watching.

## Watching it

```bash
make prod.narrate                  # the loop in prose, for a screen
make prod.narrate ARGS="--once"    # a snapshot of what is already there
make prod.events                   # status, harvest permission, spend
make prod.logs                     # the API log
make prod.report ARGS="--event 1"  # what each harvesting route actually produced
```

`narrate` polls every second and speaks in sentences:

```
  harvested  Quibdó · x — 151 posts, 147 of them new
  read       38 posts — 12 needs, 6 offers
  proposed   Agua: Comunidad Cristo Rey → Coliseo Mayor de Quibdó
```

It starts level with the database, so it narrates what arrives rather than what is already
there. Start it before the thing you want to watch.

## Running the loop unattended

```bash
make prod.workers          # worker + beat
make prod.tick             # beat it once, now
make prod.workers-down     # stop them
```

**Beat runs its first tick a whole `TICK_SECONDS` after it starts, not on startup.** Bringing
the workers up in front of an audience therefore shows nothing at all until that interval has
passed. `make prod.tick` fires the same beat on demand, which is how a demonstration starts.

One beat is: dispatch the harvest jobs already queued, pull comments, then let the frontier
agent decide what to queue next. Harvest first — a round that decides before its predecessor's
jobs have run reads a scoreboard where nothing has moved.

Three clocks decide how alive it looks, and only one of them is the screen:

| Setting | What it paces |
|---|---|
| `narrate --interval` | how often the display refreshes (1s) |
| `TICK_SECONDS` | how often the loop harvests and decides |
| `WATCH_SECONDS` | how often USGS is polled for new emergencies |

A slow `TICK_SECONDS` cannot be hidden by a fast narrator: the screen refreshes every second
and has nothing new to say. For a demonstration somebody is watching, drop it to 60.

## Stopping it spending

Two circuit breakers, neither of them a budget. Nothing weighs cost against yield; they exist
so a loop left running overnight stops instead of billing until somebody wakes up.

| Setting | Scope | What tripping does |
|---|---|---|
| `HARVEST_SPEND_CEILING_USD` | one event | pauses that event |
| `HARVEST_SPEND_TOTAL_CEILING_USD` | every event | refuses at the gate, changes no state |

The global one is checked immediately before Apify is called, so it holds regardless of who
queued the job or when — including jobs queued while an event was still active. A refused job
stays `pending`, which is what makes the breaker liftable mid-demonstration:

```bash
make prod.ceiling USD=10   # raise it and recreate the workers so they read it
make prod.ceiling          # report the current one
make prod.events           # spend so far, and whether it is blocking
```

Because refusing changes no state, a blocked system still shows every event as harvestable.
`make prod.events` prints the global ceiling at the foot of the listing for exactly that reason.

## When something looks wrong

**The API reports requirements and the map is empty.** The graph is served from a stored
snapshot. Writes mark it `stale` and a read rebuilds a stale one, so this heals itself — but
`make prod.graph ARGS="--event 1"` forces it.

**Harvest jobs do nothing.** Either the event is not armed (`make prod.events`, the `harvest`
column) or a ceiling is refusing them. The log says which:

```bash
make prod.logs | grep "refused"
```

**A deploy reports `MISSING` or `DRIFTED` keys.** It compared `/opt/ayudagente/.env` against
`deploy/env.prod.example` from the commit it just pulled. `MISSING` means a setting was added
to the template and the deployment has never had it; `DRIFTED` means a non-secret default
changed. It only reports — edit `/opt/ayudagente/.env` yourself.

**Nothing scrapes and you expected it to.** `deploy.sh` never starts the workers profile. The
loop runs only after `make prod.workers`, and that is on purpose.
