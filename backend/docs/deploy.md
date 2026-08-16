# Deploying AyudAgente

The stack is three always-on services and two opt-in ones. The split matters: `web`, `db` and
`redis` serve what has already been found and cost nothing per request. `worker` and `beat`
spend OpenAI and Apify credit from the moment they start, which is why they sit behind a
compose profile rather than starting with everything else.

## Bring it up

```bash
git clone <repo> /opt/ayudagente && cd /opt/ayudagente
cp .env.example .env          # then fill it in — see below
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
```

Reference data is loaded once and is not optional. Without the taxonomy every resource resolves
to nothing; without a gazetteer a sweep carries no toponym and pulls in other countries'
disasters, which is invariant 9.

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py load_taxonomy
docker compose -f docker-compose.prod.yml exec web python manage.py load_gazetteer CO
```

Never run `seed` on a deployment. It loads the pilot corpus, and its `--clear` flag is
destructive.

## The settings that must not stay at their defaults

| Variable | Why |
| --- | --- |
| `SECRET_KEY` | Defaults to a known dev string. Sessions and signatures are forgeable until it changes. |
| `DEBUG` | Must be `False`. It also opens CORS to every origin. |
| `ALLOWED_HOSTS` | Defaults to `*`. Set it to the API's hostname. |
| `API_KEYS` | Empty means every request gets a 503. That is deliberate — see below. |
| `CORS_ALLOWED_ORIGINS` | The frontend's origin, since `DEBUG=False` closes the wildcard. |
| `DB_PASSWORD` | Postgres binds to `127.0.0.1`, but the password is still the only thing between a local process and the data. |

Mint the API key with `make apikey`; it writes into `.env`. An empty `API_KEYS` fails closed on
purpose: a stripped configuration and one nobody ever wrote look identical from inside, and
answering requests in that state is worse than refusing them.

## Turning the loop on

Nothing harvests until two separate things are true, and keeping them separate is the whole
cost control.

```bash
# 1. the workers exist
docker compose -f docker-compose.prod.yml --profile workers up -d

# 2. an event is armed
docker compose -f docker-compose.prod.yml exec web python manage.py events
docker compose -f docker-compose.prod.yml exec web python manage.py arm_event <id> \
    --hashtags sismo,chocó --demand "necesitamos,urgente" --supply "punto de acopio,donaciones"
```

`beat` drives two schedules. `radar-watch` polls USGS every `WATCH_SECONDS` and proposes
`paused` events — free, and it never touches Apify. `radar-tick` every `TICK_SECONDS` runs the
harvest loop, and it only ever sees events somebody armed, because every writer checks
`Event.is_harvestable`.

`HARVEST_SPEND_CEILING_USD` is a circuit breaker rather than a budget: nothing weighs it against
anything, it exists so a runaway loop at three in the morning pauses the event instead of
billing until somebody wakes up.

## Turning it off in a hurry

```bash
docker compose -f docker-compose.prod.yml --profile workers stop worker beat
```

The API keeps serving everything already found. To stop one event rather than everything, pause
it — `Event.is_harvestable` is the switch every writer already consults, so pausing takes effect
without restarting anything.

## What to watch

- `make events` — the `harvest` column says which events may spend, and `spent` says how much.
- `HarvestJob` rows stuck in `running` mean a worker died mid-job. A running job counts as in
  flight, so its target is never harvested again until the row is moved to `failed`.
- `GraphSnapshot.stale` staying true means writes are marking it and nothing is rebuilding.
  Reads rebuild inline, so this is slow rather than broken — but it says the worker is down.
- `unread_observations` in the event summary is the honest measure of whether the pipeline is
  keeping up with the harvest.

## Media

Images are downloaded to `MEDIA_ROOT` and never into Postgres, so `${MEDIA_DIR}` on the host is
real state and belongs in whatever backs up. Platform URLs expire within hours; a lost media
directory cannot be re-fetched.
