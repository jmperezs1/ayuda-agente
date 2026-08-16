# HTTP API

The contract the web frontend codes against. Twelve read endpoints and one authentication
scheme over all of them.

The two agent endpoints have their own document — [`agent-api.md`](agent-api.md) covers the
stream format, the tool events and the failure modes worth handling. This one covers
everything else. Models in [`data-model.md`](data-model.md).

---

## Base URL and authentication

```
http://localhost:8000/api/
```

Every path under `/api/` requires a key. Send it in either header, whichever is easier:

```http
X-API-Key: <key>
```
```http
Authorization: Bearer <key>
```

Keys are issued per consumer, not per user — the frontend holds one, a dashboard would hold
another. There is no login, no session and no cookie: the API authenticates the *client*, and
who the human is has no meaning to it yet.

The key belongs in the server that proxies your requests, not in browser JavaScript. Anything
shipped to the browser is public, and a key in a bundle is a key on the internet.

| Status | Body | Meaning |
|---|---|---|
| 400 | `{"error": "unknown status: nope. Expected [...]"}` | A filter value outside its enumeration |
| 401 | `{"error": "missing API key; send it in X-API-Key"}` | Neither header carried one |
| 403 | `{"error": "unknown API key"}` | The key is not on the server's list |
| 404 | Django's own page, not JSON | No such id, or no such path |
| 503 | `{"error": "the API has no keys configured"}` | `API_KEYS` is empty on the server |

That 503 is a server misconfiguration, not your bug. An unconfigured deployment closes the API
rather than opening it, so a stripped environment file fails loudly instead of publishing the
data.

`/admin/` is not under `/api/` and keeps its own session login. CORS preflight (`OPTIONS`) is
never challenged — a browser cannot attach a custom header to a preflight, and requiring one
would forbid cross-origin calls entirely.

### CORS

While `DEBUG=True` every origin is allowed. For anything else, set `CORS_ALLOWED_ORIGINS` on
the server to a comma-separated list. `x-api-key` is on the allowed-headers list, so the
preflight passes it through.

---

## The endpoints

Collections hang off their event, because every question in this system is asked about one
emergency. Single entities sit at the root: an id is globally unique, and nesting it would let
you build a URL whose event and id disagree.

| Path | Returns |
|---|---|
| `GET /events/` | Active events |
| `GET /events/{id}/` | One event with summary counts |
| `GET /events/{id}/graph/` | The whole graph — nodes, edges, open requirements |
| `GET /events/{id}/requirements/` | Needs and offers, filtered and paged |
| `GET /events/{id}/actors/` | Actors, filtered and paged |
| `GET /events/{id}/matches/` | Proposed and accepted pairings |
| `GET /events/{id}/outreach/` | Drafted messages and their links |
| `GET /events/{id}/observations/` | The raw post feed |
| `GET /requirements/{id}/` | One requirement with its evidence and matches |
| `GET /actors/{id}/` | One actor with contacts and requirements |
| `GET /observations/{id}/` | One post with what the model read in it |
| `GET /resource-types/` | The resource catalog, for filter menus |

Nothing writes. Confirming a match or dismissing a draft goes through the admin for now.

### Paging

Every list endpoint answers with the same envelope:

```json
{"count": 128, "limit": 100, "offset": 0, "results": [ … ]}
```

`count` is the total matching the filters, not the size of the page. `limit` defaults to 100
and is capped at 500. The graph endpoint is the one exception — it is not paged, because half
a city's needs is a worse picture than none.

---

## `GET /events/`

Active events, newest first. The entry point: everything else is scoped to one `event_id`.

```json
{
  "events": [
    {
      "id": 14,
      "name": "Chocó earthquake M7.4",
      "hazard": "earthquake",
      "status": "active",
      "occurred_at": "2026-08-10T12:34:27+00:00",
      "magnitude": 7.4,
      "epicenter": {"lat": 4.99, "lon": -76.29}
    }
  ]
}
```

## `GET /events/{id}/`

The same fields plus `depth_km`, `country_code`, `languages`, `detection_source`, and:

```json
{
  "summary": {
    "actors": 5, "needs": 0, "offers": 6, "requirements": 6, "matches": 0,
    "observations": 925, "unread_observations": 905, "outreach_drafts": 0
  }
}
```

`needs` and `offers` count *open* requirements — what is outstanding, not what has ever been
seen. `unread_observations` is the one that says whether the pipeline is behind: posts
harvested but not yet read by the model.

---

## `GET /events/{id}/requirements/`

The busiest endpoint. It answers "draw every open need in this municipality", "show me
critical water requests" and "what is inside the box the user drew on the map" — all the same
query with different filters.

| Parameter | Repeatable | Notes |
|---|---|---|
| `direction` | yes | `needs` / `offers` |
| `status` | yes | Defaults to `open` and `partial` |
| `urgency` | yes | `critical` / `high` / `medium` / `low` |
| `resource` | yes | By `resource_key`, not by name |
| `actor_kind` | yes | See [Actor kinds](#actor-kinds) |
| `min_precision` | no | Drops anything coarser. See [Location precision](#location-precision) |
| `q` | no | Substring of the free text or the actor's name |
| `bbox` | no | `minLon,minLat,maxLon,maxLat` |
| `near` + `radius_km` | no | `lat,lon` and a radius, default 25 km |
| `order` | no | `urgency` (default), `recent`, `confidence` |
| `limit` / `offset` | no | Paging |

Repeat a parameter to widen the filter: `?status=open&status=partial`. `bbox` and `near`
together are a 400 rather than an intersection nobody meant to ask for.

**`near` is `lat,lon`; `bbox` is longitude first.** That is not a mistake — `near` matches
what you paste from a map, `bbox` matches what every geospatial tool emits.

```json
{
  "id": 916,
  "direction": "offers",
  "resource": "Alimentos",
  "resource_key": "food",
  "free_text": "centro de acopio de ayudas humanitarias",
  "urgency": "high",
  "status": "open",
  "quantity": null,
  "covered_quantity": 0.0,
  "outstanding": null,
  "unit": "",
  "destination": null,
  "confidence": 0.98,
  "is_saturated": false,
  "window_start": null,
  "window_end": null,
  "last_seen_at": "2026-08-10T16:53:13+00:00",
  "actor": {"id": 912, "name": "Pacto Histórico Quindío", "kind": "community",
            "is_organization": false, "credibility": 0.5, "verified": false},
  "location": {
    "point": {"lat": 4.5346502, "lon": -75.6700599},
    "precision": "exact_point",
    "text": "la Cra 13 # 15-01 en la ciudad de Armenia, CO",
    "admin_unit": null
  }
}
```

**A null `quantity` is the normal case, not missing data.** Most posts say "necesitamos agua",
not "100 L". Sort by `urgency` and treat quantity as a bonus; a UI that needs a number to draw
a row will show almost nothing.

**Do not draw a marker without checking `precision`.** A `country` point is the centroid of a
nation. Treat anything coarser than `admin_2` as an area, not a place — `min_precision` exists
so the map layer can just exclude them.

## `GET /requirements/{id}/`

Everything above, plus `destination_location`, `created_at`, and the two things a detail panel
is for:

```json
{
  "evidence": [
    {
      "id": 7930,
      "platform": "x",
      "permalink": "https://x.com/MiguelGrisalesS/status/2086858418610946559",
      "posted_at": "2026-08-10T16:53:13+00:00",
      "text": "COMUNICADO: …",
      "transcript": "",
      "language": "es",
      "author": {"handle": "@MiguelGrisalesS", "name": "Miguel Grisales",
                 "avatar_url": "…", "followers": 12400, "verified": false},
      "metrics": {"likes": 31, "shares": 4, "comments": 2},
      "is_reply": false,
      "media": [{"id": 6490, "kind": "cover", "url": null,
                 "source_url": "https://…", "alt_text": "", "position": 0}]
    }
  ],
  "matches": [ … ]
}
```

**`evidence` is a list, and the reason matters.** One post can produce several requirements —
a post listing three collection centers produces three — and one requirement can be
corroborated by several posts. A UI that assumes one post per requirement will show the wrong
screenshot as often as the right one.

`matches` covers both sides. A collection center is the need of one match and the offer of
another, and showing one side makes half its activity invisible.

### Media

`url` is our own stored copy; `source_url` is the platform's. **Prefer `url`** — platform media
URLs are signed and expire within hours, so a frontend rendering `source_url` shows broken
images on anything more than a day old.

`url` is currently `null` on every row: the harvest stores the link but does not yet download
the file. Until it does, `source_url` is all there is, and it will rot. Render a placeholder
when both are unusable rather than a broken image.

---

## `GET /events/{id}/actors/`

| Parameter | Repeatable | Notes |
|---|---|---|
| `kind` | yes | See [Actor kinds](#actor-kinds) |
| `organizations` | no | `true` drops individuals |
| `q` | no | Substring of the name |
| `limit` / `offset` | no | Paging |

Merged duplicates never appear. Rows carry `location`, `last_seen_at`, `requirements` (open
only), and two summary fields instead of the contact details:

```json
{"contact_count": 1, "can_be_reached": false}
```

`can_be_reached` is false when the only contacts are payment accounts or postal addresses —
those are details, not channels. A list of two hundred actors carrying every phone number is a
payload nobody needs; the detail endpoint is one click away.

## `GET /actors/{id}/`

Adds `alternate_names`, `credibility_source`, `max_followers`, `first_seen_at`, the full
`location`, every open `requirement`, and:

```json
{
  "contacts": [
    {
      "id": 31, "kind": "whatsapp", "value": "+573002377012",
      "raw_value": "300 237 7012", "platform": "", "payment_network": "",
      "times_seen": 3, "confidence": 0.8, "verified": false, "reachable": true,
      "can_carry_a_message": true, "preference_rank": 1, "attempts": 0
    }
  ]
}
```

Contacts arrive already sorted by `preference_rank` — least intrusive and most expected first
(email, WhatsApp, handle, form, website, phone). Offer them in that order.

`times_seen` is the trust signal worth showing. A number written across five posts is almost
certainly real; one that appeared once may be an extraction error, and a person about to call
deserves to know which they are looking at.

**A merged id resolves rather than 404s.** Identity resolution keeps running after you have
rendered a link, so requesting an actor that was since merged returns the survivor plus
`requested_id` — the id you asked for. Update your link when you see that field.

---

## `GET /events/{id}/matches/`

Filters: `status` (repeatable, defaults to the four the graph draws), `order`
(`score` default, `recent`, `distance`), `limit`, `offset`.

```json
{
  "id": 7, "status": "proposed", "score": 0.84, "distance_km": 12.4,
  "committed_quantity": 25.0,
  "rationale": "Centro de acopio a 12 km con agua disponible",
  "created_at": "2026-08-11T09:14:00+00:00",
  "need":  {"requirement_id": 118, "actor": {…}, "resource": "Agua",
            "resource_key": "water", "location": {…}},
  "offer": {"requirement_id": 204, "actor": {…}, "resource": "Agua",
            "resource_key": "water", "location": {…}},
  "via_transport": null
}
```

A non-null `via_transport` means the delivery needs a carrier: the match is really two hops.
Draw it as one line through that node rather than as a separate edge, or the graph doubles.

Past `proposed`, a human is already involved and the matching pass will not rewrite it.

## `GET /events/{id}/outreach/`

Filters: `status` (defaults to `draft`), `purpose`, `channel`, `limit`, `offset`.

```json
{
  "id": 3, "purpose": "connect", "channel": "whatsapp", "status": "draft",
  "subject": "", "body": "Hola, tenemos agua disponible cerca.",
  "target_url": "https://wa.me/573002377012?text=Hola%2C%20tenemos…",
  "text_is_prefilled": true,
  "drafted_by": "gpt-5.6-sol",
  "created_at": "…", "dispatched_at": null,
  "target_actor": {…}, "contact": {…},
  "match_id": 7, "requirement_id": 118, "in_reply_to_id": null
}
```

**`target_url` is the entire dispatch mechanism.** Nothing is ever sent by this system — render
it as a button a person clicks. `text_is_prefilled` says whether the body travels inside the
link (WhatsApp, email) or whether you need to show a copy button beside it (a comment reply
cannot carry text in a URL).

---

## `GET /events/{id}/observations/`

The raw radar feed, newest first. This is the answer to "how do you know that", which is the
first question anyone asks of a system that reads social media during an emergency.

| Parameter | Repeatable | Notes |
|---|---|---|
| `platform` | yes | `x` / `instagram` / `facebook` / `tiktok` |
| `classification` | yes | `need` / `offer` / `both` / `discard` — what the model made of it |
| `q` | no | Substring of the text |
| `has_media` | no | `true` keeps posts with an image or video |
| `unread` | no | `true` keeps posts the pipeline has not read |
| `limit` / `offset` | no | Paging |

Rows are the same shape as `evidence` above.

## `GET /observations/{id}/`

Adds `hashtags`, `mentions`, `external_links`, `platform_geo`, `harvested_at`, the
requirements it produced, and the audit trail:

```json
{
  "extraction": {
    "classification": "offer",
    "confidence": 0.97,
    "visual_summary": "Flyer con dirección de un punto de acopio",
    "text_image_conflict": false,
    "geocode_query": "Cra 13 # 15-01, Armenia, CO",
    "model": "gpt-5.6-sol",
    "prompt_version": "v6",
    "created_at": "…"
  }
}
```

`null` when the pipeline has not read the post yet.

**`text_image_conflict` is the field worth surfacing.** It means the photo does not match what
the text claimed — the signature of recycled imagery. A coordinator who can see that flag will
treat the item very differently from one who cannot.

---

## `GET /resource-types/`

The catalog, for a filter menu. Not paged, not scoped to an event.

```json
{"resource_types": [
  {"key": "water", "name": "Agua", "parent": null, "default_unit": "L", "perishable": false}
]}
```

**Filter on `key`, never on `name`.** Keys are English and stable; names are Spanish because
they reach an end user, and they will be rewritten.

24 entries, parents before children. `parent` is what lets an offer of a category satisfy a
need for a specific item — `pet_food` sits under `food`, so a generic food offer covers it.
Group the menu by `parent` and a top-level entry stands on its own.

The catalog is shared by every event and grows: when the extractor meets a resource nobody
declared, it creates the entry rather than dropping the item. A new one arrives with its `name`
equal to its `key` until somebody names it, which is the only case where you will see an
English label.

---

## `GET /events/{id}/graph/`

Actors as nodes, matches as edges, open requirements attached to their node. Computed fresh on
every call, so "real time" here means no snapshot lag rather than a socket.

```json
{
  "event": {"id": 14, "name": "…", "epicenter": {"lat": 4.99, "lon": -76.29}},
  "nodes": [
    {"id": 42, "name": "Coliseo Mayor de Pereira", "kind": "collection_center",
     "credibility": 0.72, "verified": false,
     "location": {"lat": 4.8133, "lon": -75.6961},
     "precision": "neighborhood", "admin_unit": "Pereira",
     "requirements": [ … ]}
  ],
  "edges": [
    {"id": 7, "from_actor": 42, "to_actor": 19, "resource": "Agua",
     "status": "proposed", "score": 0.84, "distance_km": 12.4,
     "committed_quantity": 25.0, "via_transport_actor": null,
     "rationale": "…"}
  ]
}
```

A node's `location` falls back to its first open requirement's point when the actor has none of
its own, so a node never silently vanishes from the map. Edges always point offer → need.

Prefer this endpoint for the map's first paint and `/requirements/` for anything filtered — the
graph deliberately ignores every query parameter.

---

## `POST /agent/coordination/` and `POST /agent/frontier/`

Streaming conversations. Body, event format and error handling in
[`agent-api.md`](agent-api.md).

Two things that document predates: both endpoints require the API key like everything else,
and `EventSource` cannot be used — it is GET-only and cannot set headers. Use `fetch` with a
body reader.

---

## Enumerations

Values are stable slugs. Labels are yours to write — every user-facing string is a product
decision, in Spanish, that belongs in the frontend.

### Hazard
`earthquake` · `flood` · `landslide` · `cyclone` · `wildfire` · `windstorm` · `other`

### Actor kinds
`person` · `collection_center` · `nonprofit` · `company` · `public_entity` · `media_outlet` ·
`community` · `church` · `school` · `volunteer_group`

`is_organization` is true for all of them except `person` and `community`.

### Direction
`needs` · `offers`

### Urgency
`critical` · `high` · `medium` · `low`

### Requirement status
`open` · `partial` · `covered` · `expired` · `unverified` · `discarded`

Lists default to the first two.

### Match status
`proposed` · `contacted` · `confirmed` · `delivered` · `failed` · `discarded`

Lists default to the first four.

### Outreach
Status: `draft` · `dispatched` · `answered` · `dismissed` · `failed` (lists default to `draft`).
Purpose: `connect` · `answer` · `verify` · `request_detail`.
Channel: `email` · `whatsapp` · `comment_reply` · `direct_message` · `phone_call`.

### Contact kinds
`handle` · `phone` · `whatsapp` · `email` · `website` · `form` · `payment` · `street_address`

### Platform
`x` · `instagram` · `facebook` · `tiktok`

### Location precision
Coarse to fine, and the order is load-bearing:

`country` · `admin_1` · `admin_2` · `admin_3` · `neighborhood` · `street_address` ·
`exact_point`

---

## What is not here yet

- **No write endpoints.** Confirming a match, dismissing a draft, correcting a bad extraction —
  all through the admin.
- **No downloaded media.** `Media.blob_path` is empty on every row, so `url` is always null.
- **No sockets.** Poll the endpoints; at this size it is cheap.
- **The graph is not paged.** Fine at a few hundred nodes, a problem at ten thousand.

---

## Local setup

```bash
make up          # Postgres and Redis
make migrate
uv run manage.py load_taxonomy   # the resource catalog, needed everywhere
make seed        # development only: loads the pilot corpus, so the graph is not empty
make apikey      # mints a key into .env
make run
```

`make apikey` adds a key without touching the ones already there; `make apikey ARGS="--replace"`
drops them. `API_KEYS` is a comma-separated list, so each consumer can hold its own and be
revoked without locking out the rest.

```http
GET http://localhost:8000/api/events/
X-API-Key: ayk_…
```
