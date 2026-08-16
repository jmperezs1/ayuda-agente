# Search Frontier

**Operating handbook** — how an autonomous agent finds, scopes and pursues actionable
information during a disaster, without indiscriminately crawling the entire network.

> Validated against the Chocó earthquake (M7.4, 10 August 2026).
> 712 posts across X, Instagram, Facebook and TikTok, plus 227 comments, for $1.32 of Apify
> credit. Hackathon CTW 2026 · v1 · 15 August 2026.

---

## The premise: sweep broadly, choose where to go deep

An earlier version of this handbook argued that a national sweep was unaffordable — one query
per municipality, times four platforms, times 48 passes a day. That arithmetic was wrong,
because that is not how you search.

You do not run one query per place. You batch toponyms into one query per platform × zone ×
axis:

```
(Pereira OR Quimbaya OR Quibdó OR Cali OR Manizales OR …) AND …
```

The pilot covered a whole country that way: ten queries, 400 posts, **$0.10**. A full sweep is
20–40 queries per pass, roughly **$0.50–2**, so every 30 minutes costs **$25–100 a day**.
Breadth is affordable. Collect everything.

What is *not* free is **depth**: mining the eight thousand comments under a viral video,
pulling an account's whole timeline, re-checking one place every fifteen minutes. Depth
multiplies, and that is the only allocation decision worth making.

So the frontier does not exist to ration a budget. It exists to answer one question:
**where is the content good enough to justify going deeper?** The signal is quality — actionable
items per hundred — not price.

> **Governing principle.** Detection is cheap and global. Broad harvesting is cheap and
> batched. Depth is expensive and focused. Never let harvesting do detection's job, and never
> go deep where breadth already told you there is nothing.

---

## Stage 01 — Global watch

*Every 30 minutes, over official feeds. Cost: zero.*

The watch job **does not scrape social media**. Social is where you go *after* you know
something happened. To find out, there are public, structured, free seismic and humanitarian
feeds that already give you magnitude, coordinates, depth and a severity estimate.

| Source | Coverage | Why |
|---|---|---|
| `GDACS` | Multi-hazard | Earthquakes, cyclones, floods, volcanoes. Already carries a green/orange/red alert score. The best single starting source. |
| `USGS FDSN` | Global seismic | Canonical catalog, no API key, queryable by magnitude and time window. |
| `EMSC` | Global seismic | Has a real-time WebSocket: it pushes the event instead of you polling for it. |
| `ReliefWeb` | Humanitarian | Agency reports. Slower, but validates severity and names the organizations responding. |
| National service | Country | SGC and UNGRD in Colombia. Always more precise and faster than global sources for their own territory. |

The trigger threshold is yours to set. Magnitude alone is not enough: an M7.4 at 600 km depth
under the ocean does no harm, and a shallow M5.8 under a city does. Trigger on a combination
of **magnitude, depth and population within the shaking radius** — or delegate to GDACS's
alert level, which already models that.

### The watch does not switch off on detection

Disasters cascade. Four days after the earthquake, Quibdó — already hit — flooded from
overnight rain, and there was a windstorm in Casacará (Agustín Codazzi, Cesar). TikTok caught
it, not the seismic feeds. An already-affected area taking a second hit changes its priority
completely, so the watch keeps running against the active event and can reorder what gets
watched mid-operation.

> ⚠️ **Verify before wiring.** The four scraping probes in this handbook are measured against
> real data. These detection feeds are prior knowledge: confirm endpoints, formats and quota
> limits before building on them.

---

## Stage 02 — Ground truth anchoring

*Fires once per event. Builds the vocabulary everything else depends on.*

Before touching social media, the agent assembles an **Event Record**: exact time, epicenter
coordinates, depth, magnitude, and — most importantly — the **official list of affected
administrative units**.

This is not bureaucracy. It is the search vocabulary. Without the real proper names of
municipalities and rural districts, queries have nothing to anchor to and return garbage. In
the pilot, ground truth came from press and primary sources in minutes at zero cost, and
produced the list that made everything else work: Pereira, Quimbaya, Quibdó, Cali, Manizales,
Buenaventura, San José del Palmar, Calima-El Darién.

### What the record holds

- **Physical core** — epicenter, time, magnitude, depth, aftershocks.
- **Affected units** — official administrative codes from the global gazetteer (GeoNames),
  not text strings.
- **Downed infrastructure** — airports, roads, hospitals. Closed roads predict where cut-off
  communities will be.
- **Event lexicon** — what people call it. Hashtags, nicknames, the name of the building that
  collapsed.
- **Official responders** — accounts for city halls, civil defense, red cross. High-credibility
  seeds for the frontier.

---

## Stage 03 — Geographic scoping

*Two zones, not one. The most expensive mistake is treating the country as a single territory.*

An unaffected municipality is not noise. Barranquilla did not suffer the earthquake, and it
still showed up organizing a truckload of donations for Pereira. Fusagasugá sent medical
supplies to Quibdó. Soacha "adopted" Vijes. Intact cities are **supply nodes**, and you have
to search them — with different queries.

| | **Impact zone** → demand axis | **Support zone** → supply axis |
|---|---|---|
| **What it holds** | Places on the official affected list, plus their neighbours by distance to the epicenter | The rest of the country, prioritizing urban centers and neighboring municipalities with road access |
| **What to search for** | Unmet needs · cut-off communities · overwhelmed shelters · missing persons · concrete shortages (water, medicine, tents) | Collection points with address and hours · vehicles and logistics offered · fundraising campaigns and accounts · volunteers and brigades · companies donating in kind |

Geographic expansion runs over the country's official administrative division, not over what
the model happens to remember. You load the gazetteer once, and the agent iterates real
entities with codes, coordinates and a *country → admin_1 → admin_2 → admin_3* hierarchy —
the GeoNames convention, so it works the same in any country. That is how you reach the long
tail without hallucinating place names.

> **Distance is the cold-start prior.** Before any yield data exists, order impact-zone places
> by distance to the epicenter and cross that with population. It predicts where signal
> *should* be, which is where the first passes go. After that, measured quality takes over.

---

## Stage 04 — Query synthesis

*One query per platform × zone × axis combination. Never a generic query.*

### The toponym anchoring rule

**Every query carries a place name from the event's country.** This was the highest-impact rule in the pilot.
Without anchoring, searches were contaminated by earthquakes in Venezuela, Peru, Indonesia,
Ecuador and Granada; even a Peruvian business surfaced for saying "punto de acopio". Geography
is not a filter you apply afterward: it is part of the query.

**Demand axis, impact zone**

```
// (symptom) × (toponym) × (window) × (language)
("no ha llegado ayuda" OR "estamos incomunicados" OR damnificados)
AND (Pereira OR Quimbaya OR Quibdó OR "San José del Palmar")
AND lang:es
AND since:2026-08-10_12:00:00_UTC until:2026-08-16_00:00:00_UTC
```

**Supply axis, support zone**

```
// (resource) × (unaffected toponym) × (window)
("punto de acopio" OR "recibimos donaciones" OR "sale un camión")
AND (Bogotá OR Medellín OR Barranquilla OR Bucaramanga)
AND (terremoto OR sismo OR damnificados)
AND lang:es
```

**Rural long tail, Facebook only**

```
// rural administrative vocabulary, no specific toponym:
// the terms themselves anchor the search to Colombia
vereda corregimiento resguardo incomunicada sismo ayuda humanitaria
```

### What worked and what did not

| Pattern | Verdict | Why |
|---|---|---|
| `"punto de acopio"` + municipality | **Excellent** | Address, hours and accepted goods, usually in the same post. |
| `vereda / corregimiento / resguardo` | **Excellent** | The only one that reached the rural long tail. Found Herveo and the Wounaan reserve. |
| `damnificados` + municipality | Good | Mixes press with first-hand accounts, but the first-hand ones carry fine-grained locations. |
| `albergue / mercados / carpas` | Good | A useful bridge between the two axes: whoever mentions shortages is usually on the ground. |
| `"estamos incomunicados"` with no municipality | Bad | Massive contamination from other countries. Useless without anchoring. |
| `"tengo camioneta"`, `"presto mi"` | Useless | Near-pure false positives. People use those phrases to boast, not to offer. |
| Event hashtags | Weak | Media and political reach. High volume, low actionability. |

The lesson about the own-resources axis is the most useful one: **do not search for the
capacity, search for the call to action**. Someone lending their truck announces it as
"a truck leaves tomorrow for Pereira", not "I have a truck".

---

## Stage 05 — Harvest and structuring

*Four platforms, four distinct roles. They are not interchangeable.*

| Platform | Sample | Actionable | $ / actionable | Geo | Role |
|---|---:|---:|---:|---:|---|
| X | 400 | 39 · 9.8% | **$0.0026** | 1.5% | Cheap broad sweep. Urban demand. |
| Instagram | 112 | 38 · 34% | $0.0077 | **34% city** | Commercial collection-point directory. Structured geo. |
| Facebook | 100 | **37 · 37%** | $0.0070 | — | Rural long tail. Payment details. Real demand. |
| TikTok | 100 | 9 · 9% | $0.041 | 19% district | Precision in ground zero. On-foot coordinators. |
| Comments | 227 | ~6% | — | — | Discovery, not harvest. |

**Actors used**

| Platform | Actor | Price |
|---|---|---|
| X | `kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest` | $0.00025 / tweet |
| Instagram | `apify/instagram-hashtag-scraper` | $0.0026 / post |
| Facebook | `scraper_one/facebook-posts-search` | $0.0025 / post |
| TikTok | `clockworks/tiktok-scraper` | $0.0037 / video |
| Comments | `clockworks/tiktok-comments-scraper` | $0.00125 / comment |

**X** is the cheapest per useful item and the only one with solid volume and time-window
control: use it to sweep. **Instagram** is almost purely supply — shops and nonprofits
announcing collection points — but it carries structured location at city level. **Facebook**
is the one that finds who nobody else is looking at: the small municipalities that never make
the press, and it frequently carries bank accounts and full addresses.

### TikTok: expensive to sweep with, irreplaceable at ground zero

16× more expensive per useful item, so it is no good for sweeping — but its geolocation
reaches rural districts and neighborhoods rather than cities, and its authors are on-foot
coordinators. It is the only platform that produced operational intelligence of this kind:

> *"Help is needed in this area. In Cuba, Pereira. Person in charge 300 2377012 Janeth (you
> have to come up through Villa Ligia, because if you come up through Leningrado there are
> people who are not victims taking advantage and keeping the goods)"*

Where the need is, who coordinates it, their phone, and which route to take so the aid is not
diverted. No official source publishes that last part.

Use it as a **precision instrument on ring T0**: few queries, high frequency, impact zone
only. There its granularity justifies the price.

### Comments are a discovery layer, not a harvest layer

Mining comments yields poor density — of 227 analyzed, zero phone numbers and only five with
fine-grained locations. As a source of structured items it does not compete with Facebook. But
it holds something no other layer does:

| **Unmet demand** | **Blocked supply** |
|---|---|
| "hi I have children and food ran out where can I go" | "do they still need volunteers at the Coliseo Mayor???" |
| "for people who need food where can they go?" | "do they need people for logistics support?" |
| "go to the north side of Quibdó, La Victoria neighborhood" | "we're bringing drinks today, DM me" |

Comments are where **unmet demand for information** lives: people who need help and do not
know where to go, people who want to help and do not know where. They are the product's users
writing their need in plain text. And locals name underserved neighborhoods that no other
layer reported. Use them to **feed the frontier new toponyms**, not to fill the database.

One operational detail: contact almost always moves to a direct message ("DM me"). The phone
number rarely stays in the public comment.

### Per-item extraction

| Field | Source | Note |
|---|---|---|
| `axis` | Classifier | demand · supply · informational · **discard** |
| `category` | Classifier | water, food, medicine, shelter, transport, rescue, animals, mental health |
| `location` | NER + geocoding | Almost always in free text, not in metadata |
| `contact` | Regex | Phone, Nequi, Bancolombia, Bre-B key, address |
| `window` | Extraction | "until 16 August", "leaving tomorrow" — expires the item |
| `status` | Derived | active · fulfilled · expired · unverified |

> ⚠️ **A discard class is mandatory.** A large share of the volume is political argument about
> the government's handling of aid. Without an explicit discard class for it, the frontier
> floods: high engagement, zero actionability, and the scoring rewards it by mistake.

Geolocation is the hard work and must be planned for. Only 1.5% of tweets carry a location
field. The useful location almost always sits in the text — *"Gamma and La Villa sector, by
the stadium"*, *"Cra 5 norte con calle 34"* — and has to be extracted with NER and geocoded
against the administrative catalog.

---

## Stage 06 — The frontier

*Where to spend again, how often, and when to stop spending.*

Every discovered source — an account, a hashtag, a municipality, a query — enters the frontier
with a score that updates on every harvest. The score determines the cadence.

The signal is quality, and there is no divisor:

| Factor | Definition |
|---|---|
| `yield_rate` | **The decision signal.** Actionable items per 100 collected over the last N passes. |
| `credibility` | High for city halls, civil defense and local media; medium for accounts with verified history; low for new or history-less accounts. |
| `distance_km` | Cold-start prior only — where to look first before any yield data exists. |
| `freshness` | Decay since the last useful find. A target quiet for six hours falls on its own. |

Cost is recorded on the job and the event because Apify returns it for free and a runaway loop
should be visible, but nothing reads it to decide. A target that yields nothing across several
passes goes to `exhausted`. That is the whole rule.

### What the frontier actually allocates

Breadth runs on a flat cadence — every zone, every pass, batched toponyms. The frontier decides
**depth**:

| Deep pass | When it is worth it |
|---|---|
| `profile` — an account's whole timeline | The account already produced actionable items more than once |
| `comments` — a post's comment tree | The post is local, high-engagement and about aid rather than damage footage |
| `thread` — replies under one post | Someone asked a question the graph can answer |
| Re-check a place at high frequency | Its `yield_rate` is high and it went quiet recently |

Accounts and places live in the same table (`FrontierNode` watches one or the other), because
they compete for the same attention.

### Two rules that keep the frontier from closing

- **Forced exploration.** Reserve a fixed share of the budget — 10% works — for sources with
  no history. Without this the agent locks onto what it already knows and never finds the
  rural district nobody was posting about twenty minutes ago, which is precisely the
  highest-value case.
- **Deduplicate against what you have seen, not against what you accepted.** Store everything
  already evaluated, including what you discarded. If you deduplicate only against confirmed
  items, rejected ones reappear every round and the loop never converges.

---

## Event disambiguation

*How the agent avoids conflating two earthquakes.*

During the pilot at least four seismic events competed in the same Spanish-language search
space: Colombia, Venezuela, Indonesia and Granada, plus a historical one in Peru. 5% of the
sample arrived contaminated. Three defenses, in order of effectiveness:

1. **Toponym anchoring in the query.** Cheap, and it solves most of it. The place name goes
   inside the search, not in a downstream filter.
2. **Strict time window.** Bounded to the exact event time in the record. Eliminates history
   and most anniversaries.
3. **Verification against the event record.** For whatever survives: the item must be
   consistent with the active event's magnitude, date and geography. This is the only one that
   catches cross-mentions — a Colombian post discussing the Venezuelan quake.

If you run several events at once, each gets its own record, its own frontier and its own
budget. They share no state beyond the geographic catalog.

---

## Failure modes

*What will break, in order of likelihood.*

1. **Expired information presented as live.** The most damaging failure, because it diverts
   real resources. A collection point that already closed is worse than no data. Every item
   needs a validity window and a status; without that, the product lies with confidence.
2. **Silently wrong geolocation.** "La Villa" exists in several municipalities. When geocoding
   is inconclusive, mark the item as approximate rather than inventing coordinates.
3. **Frontier collapse.** Without forced exploration the agent converges onto half a dozen
   high-engagement accounts and stops discovering. Detectable because the rate of newly
   discovered sources per hour drops to zero.
4. **A dead Actor that reports success.** The most treacherous one. The cheapest TikTok
   scraper in the catalog (`apidojo/tiktok-scraper`) returned `SUCCEEDED` with ten items of a
   single `noResults` field — for every query, including a control with a generic word and no
   filters. A run like that *looks* like "no signal in that area" and the scoring penalizes a
   municipality that did have signal. Diagnose it with a periodic **control query** whose
   result you know, and fail over to another Actor on that signature. Always have a substitute
   identified.
5. **Input schema drift.** Actors change their schema without notice. In this same pilot,
   Facebook's `searchType` rejected a reasonable value (`"posts"`; it only accepts `top` or
   `latest`). The agent must read the schema and, on a validation error, retry after reading
   the permitted values — not die.
6. **Budget leakage through retries.** An Actor that half-fails can still charge. Cap spend per
   event and per pass, with a hard cutoff.
7. **Misinformation amplification.** A false alert about trapped people already circulated in
   Pereira during this event. Every rescue item needs independent corroboration before it can
   be promoted.

---

## The full loop

```
every 30 min  global watch over official feeds                $0
              └─ severity threshold crossed?
                    │
once          ├─ anchor ground truth → Event Record          $0
once          ├─ resolve geographic scope
              │     ├─ impact zone   → demand axis
              │     └─ support zone  → supply axis
              │
per ring      └─ harvest loop
                    ├─ synthesize queries (platform × zone × axis)
                    ├─ run Actors, deduplicate against everything seen
                    ├─ extract, classify, geocode
                    ├─ match supply ↔ demand
                    ├─ rescore the frontier  (90% exploit / 10% explore)
                    └─ reassign cadence per ring
                          │
                          └─ event quiet for N hours? → archive
```

---

## Integration architecture

**MCP to explore, direct client to execute.** The discovery loop genuinely needs MCP:
`search-actors` and `fetch-actor-details` at runtime let the agent use tools nobody coded in
advance. But once the agent decides "this account gets scraped every 15 minutes", that drops
to a deterministic task using `apify-client`. An LLM on the critical path of a cron that runs
500 times a day is expensive, slow and non-deterministic.

**Apify runs are asynchronous.** `call-actor` over MCP blocks. With a frontier of 200 sources
that does not scale: production needs jobs launched with `waitSecs: 0` plus webhooks.

---

*Platform figures, densities and prices measured empirically on 15 August 2026 against the
Chocó earthquake (M7.4, 10 August): 712 posts across X, Instagram, Facebook and TikTok, plus
227 comments, for $1.32 of Apify credit. The global detection sources in stage 01 are
recommendations not verified in this round.*
