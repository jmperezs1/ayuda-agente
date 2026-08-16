# Notes — Apify MCP hypothesis validation (Hackathon CTW 2026)

## Goal

Validate that an agent can autonomously and effectively discover actionable information about
a natural disaster (Colombia earthquake, 10 August 2026) by scraping social media: people who
need help, affected places, collection points, companies and volunteers offering resources —
and connect supply with demand.

**Black box test**: the only starting fact given was the date of the event.

---

## Setup

Remote Apify MCP, `local` scope (in `~/.claude.json`, does not touch the repo).

```bash
claude mcp add --transport http apify \
  "https://mcp.apify.com?tools=actors,docs,storage,runs" \
  --header "Authorization: Bearer $APIFY_TOKEN" \
  --scope local
```

- Endpoint: `https://mcp.apify.com` (streamable HTTP). SSE is deprecated as of 1 April 2026.
- Auth via Bearer token or OAuth. Token was used because the session cannot complete the
  OAuth flow.
- Requires restarting Claude Code: MCP servers load at startup.
- Rate limit: 30 req/s per user.
- Validation token — rotate or delete when the hackathon ends.

---

## Apify architecture: two layers

**Layer 1 — Store (the Actors).** ~6,000 scraping programs, each doing one thing. They live in
Apify's cloud, nothing is installed. Each Actor has its own *input schema* and its own price.

**Layer 2 — MCP (the bridge).** It does not expose scrapers; it exposes generic tools for
operating the catalog:

| Tool | Function |
|---|---|
| `search-actors` | discover Actors in the Store |
| `fetch-actor-details` | read an Actor's input schema |
| `call-actor` | run it with a JSON input → returns a `datasetId` |
| `get-dataset-items` | read the results |
| `get-actor-run` / `get-actor-log` | debugging |

Actors are discovered **at runtime**. That property is what makes the black-box test possible:
nothing needs to be coded in advance about which tool gets used.

---

## Costs

- Apify free tier: **$5/month** of credit. Plans: Starter $29, Scale $199, Business $999.
- Pay-per-result pricing for the actors used:
  - `kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest` — $0.00025 / tweet
  - `apify/instagram-hashtag-scraper` — $0.0026 / post
  - `scraper_one/facebook-posts-search` — $0.0025 / post
  - `clockworks/tiktok-scraper` — $0.0037 / video
  - `clockworks/tiktok-comments-scraper` — $0.00125 / comment
- Pilot budget: **$5** (the whole free tier).

---

## Agent design (production)

Autonomous discovery and prioritization orchestrator: starts from an event, scrapes, stores
entities (posts, comments, profiles, stories, hashtags, locations), scores relevance,
credibility, activity and proximity to the event, and decides what to explore next. Maintains
a prioritized search frontier and reallocates budget dynamically.

Pattern: **focused crawler with a prioritized frontier** plus *multi-armed bandit* allocation.

Planned stack: deepagents (LangGraph) + Apify MCP + Azure OpenAI.

### MCP vs direct API — hybrid

- **MCP** for the discovery loop: the agent needs `search-actors` and `fetch-actor-details` at
  runtime to use tools nobody coded in advance.
- **`apify-client` directly** for what is already decided: once the agent settles on "this
  account gets scraped every 15 minutes", that becomes a deterministic task. An LLM on the
  critical path of a cron running 500 times a day is expensive, slow and non-deterministic.

### Three design problems

1. **The scarce resource is credit, not time.** The score must be cost-benefit, not just
   relevance. Without cost in the function, the agent burns the budget on expensive sources.
2. **Cold start.** A new account has no score. If the agent only exploits what is already
   scored, it locks itself into a bubble and never finds the cut-off rural district nobody was
   talking about twenty minutes ago — the highest-value case. Forced exploration is required
   (ε-greedy or similar).
3. **Apify runs are asynchronous.** `call-actor` over MCP blocks. With a frontier of 200
   sources it does not scale: production needs launched runs plus webhooks.

---

## Hypothesis risks

1. **The bottleneck is verification, not scraping.** Pulling 20K posts is trivial. A collection
   point that already closed, or a "we need water" that was already solved, is *worse than
   nothing*: it diverts real resources. Freshness and status are the hard problem.
2. **Geolocation.** Almost no post carries geo. It has to be inferred from text ("vereda El
   Palmar, Líbano"). This is where the time goes.
3. **X may not be where this happens in Colombia.** Real disaster coordination in Latin
   America lives on WhatsApp and Facebook groups. X provides the media and NGO layer. If
   confirmed, that is the most important finding — it changes where the product points.

---

## Pilot plan

**Budget: $5. Scope: X (Twitter) + Instagram.** Facebook deferred until after validation.

**Phase 0 — Ground truth (free).** Primary sources (SGC, USGS) for magnitude, epicenter and
municipalities. Without this there is no search vocabulary: the real names of rural districts
and municipalities are what people actually write.

**Phase 1 — Harvest along two axes.**
- *Demand*: "necesitamos", "no ha llegado ayuda", "estamos incomunicados", "se cayó",
  "damnificados", "urgente" + municipality.
- *Supply*: "punto de acopio", "recibimos donaciones", "tengo camioneta", "llevo mercados",
  "voluntarios", "cuenta de recolección".

**Phase 2 — Structuring.** Per item: axis (supply/demand), category (water, food, medicine,
shelter, transport, rescue), location, contact, timestamp, confidence.

**Phase 3 — Matching** supply ↔ demand. This is where the product lives.

### Success criteria

With ≤$5 of credit, the pilot is **viable** if it produces:

- ≥30 structured, actionable items (with a concrete contact or location)
- ≥60% precision on manual verification
- ≥5 plausible supply ↔ demand matches

If the result is 500 press tweets saying "magnitude X earthquake" and zero people asking for
concrete help, the hypothesis fails — and that is a valid result too.

---

# PILOT RESULTS (15 August 2026)

**Verdict: hypothesis validated. Spend: $0.39 of $5.**

## Ground truth (Phase 0, no cost)

Magnitude 7.4 earthquake on Monday 10 August 2026, 07:34 local (12:34 UTC). Epicenter at San
José del Palmar, Chocó (4.99 N, -76.29 W), depth ~103 km. 294+ dead, 3,970+ injured, 379+
missing, 47 aftershocks, 21 affected municipalities.

Worst hit: Pereira (66 dead, curfew), Cali (Vanessa building, HUV hospital partially
collapsed), Quimbaya (450 houses destroyed, 800 displaced families), Quibdó, Manizales,
Buenaventura. Airports suspended in Pereira, Manizales, Quibdó, Armenia, Cartago, Buenaventura
and Cali. Roads closed: Cali-Loboguerrero, Quimbaya-Montenegro.

## Execution

| Platform | Actor | Result | Cost |
|---|---|---|---|
| X | `kaitoeasyapi/twitter-x-data-tweet-scraper-...` | 400 tweets, 10 queries, 339 unique authors | $0.10 |
| Instagram | `apify/instagram-hashtag-scraper` | 112 posts, 5 hashtags | $0.29 |

## Against the success criteria

| Threshold | Result |
|---|---|
| ≥30 actionable items | **77** (39 on X + 38 on IG) |
| ≥60% precision | ~85% on the manually reviewed sample |
| ≥5 supply↔demand matches | **8** |

### Matches found

1. Pereira, Gamma and La Villa sector, "people with nothing to eat" (15 Aug) ↔ truck
   Barranquilla→Pereira leaving on the 16th, collection point Metro Plaza Local 101
2. Cali, Bueno Madrid, Cra 5 norte con calle 34, 8+ evicted families with no mats or tents ↔
   Ciudadela Petronio, Unidad Deportiva Alberto Galindo, Cali
3. Quibdó, Cabí sector, families awaiting assessment ↔ Palacio de los Deportes, Calle 63
   #54A-06 Bogotá, collection exclusively for Chocó until 16 August
4. Manizales, "aid has run out" ↔ truck Medellín→Manizales for Villamaría and the La Nueva
   Primavera and San Julián rural districts; Coliseo Menor receiving donations
5. Cali collection points with surplus ↔ private individual offering his buses to redistribute
   to municipalities in northern Valle
6. Pereira, blankets and hands for debris removal ↔ +57 310 4142969, Taller el Adorno, Pereira
7. Calima-El Darién ↔ truck leaving Sunday the 16th, asking for mats and sheets
8. Pereira, two destroyed dog shelters asking for construction material ↔ PMU Animal Cali and
   animal rescue collectives heading to Chocó

## Findings that change the design

**1. X and Instagram cover different axes; they are not redundant.**
X carries **both supply and demand**; it is the only source where a private individual says
"there are people with nothing to eat in this neighborhood". Instagram is almost **supply
only**: shops, nonprofits and brands announcing collection points with address and hours. Both
must be scraped, with different queries. Facebook will probably reinforce the demand axis —
worth a next round.

**2. Every query needs a Colombian toponym anchor.**
Without a municipality name, searches were contaminated by earthquakes in Venezuela, Peru,
Indonesia, Granada and Ecuador (19/400 in the probe). "Punto de acopio" alone also pulled
noise from Peru. Geographic anchoring is not an optional filter: it is part of the query.

**3. Geolocation confirmed as the hard problem.**
Only 6 of 400 tweets (1.5%) carry a `place` field. Instagram does much better: 37 of 110
(34%) carry `locationName`. But the useful location almost always sits in the **text**
("Gamma and La Villa sector, by the stadium", "Cra 5 norte con calle 34") and has to be
extracted and geocoded with NER.

**4. Freshness: demand is still live five days later.**
The Pereira post about "people with nothing to eat" is from 15 August, five days after the
quake. The useful window is much longer than the initial design assumed.

**5. An unforeseen kind of noise appears: political argument.**
A substantial share of the volume is debate about the government's handling of aid, not
operational information. The classifier needs an explicit discard class for this, or it floods
the search frontier.

**6. Instagram's hashtag scraper does not filter by date.**
It returned posts from 2019. Filter on `timestamp` in post-processing.

## Query performance (X)

Working: "punto de acopio" + municipality, "damnificados" + municipality, shelter/supplies.
Failing: generic unanchored phrases ("estamos incomunicados", "sin agua") and own-resource
phrasing ("tengo camioneta") — near-pure false positives. Hashtags give media coverage, not
actionable items.

---

# ROUND 2 — FACEBOOK (15 August 2026)

Actor: `scraper_one/facebook-posts-search`, $0.0025 per result. Three queries, 100 posts,
$0.26. Note: `searchType` only accepts `top` or `latest`, not `posts` — validation fails
otherwise.

**37 of 100 actionable: the best density of the three platforms.**

## Facebook solves the long-tail problem

This is the finding of this round. The rural-district query surfaced small municipalities that
appeared on neither X nor Instagram nor in press coverage:

- **Herveo, Tolima** — 80 affected families, the mayor asking for national help. Tolima did
  not even appear in the ground-truth list of affected departments.
- **Tocordo Balsalito reserve, Litoral del San Juan, Chocó** — Wounaan communities, families
  without housing. Rural indigenous area, invisible to the press.
- **Guaimía rural district, corregimiento 8 of Buenaventura** — an Afro-Colombian women's
  organization mobilizing.
- **Vijes (Valle)** — "adopted" by the Soacha city hall.

One of the sources says it outright: *"several small municipalities that also had
infrastructure emergencies have not had the public visibility needed to receive aid"*. Media
bias toward capitals is exactly the gap the product fills.

## Facebook also carries payment details

Bancolombia accounts, Nequi, Davivienda and Bre-B keys appear literally in the text, which
barely happens on X. And it carries full collection-point addresses with opening hours.

## The Soacha→Vijes model

Unaffected municipalities "adopting" affected ones: Soacha→Vijes, Fusagasugá→Quibdó,
Medellín→Pereira, Barranquilla→Pereira, Bogotá→Chocó. This confirms the two-zone model:
unaffected cities are not noise, they are **supply nodes**. They must be scraped with
different queries.

---

# ROUND 3 — TIKTOK (15 August 2026)

## A dead actor, and how to detect it

`apidojo/tiktok-scraper` ($0.0003/post, the cheapest in the Store) returns `noResults` for
everything, including a control using the keyword `"podcast"` with no filters. It is not a
query problem: the actor is broken or blocked. **A run returning `SUCCEEDED` with
`itemCount: 10` and a single `noResults` field is not a legitimate empty run — it is a silent
failure.** The agent has to detect that signature and fail over to another actor, not read it
as "no signal here".

Failover: `clockworks/tiktok-scraper`, $0.0037 per video. 12× more expensive but it works.

## Results

100 videos, **9 actionable in captions (9%)** — the lowest density of the four platforms. Cost
per actionable: **$0.041**, 16× worse than X. TikTok is not for sweeping.

**But the quality of what it does return is unmatched.** Two examples:

> 📍Vereda Kilómetro 41 📲Contact number: 314 483 7303 Yensi Paola, person with direct contact
> to the affected families

> Help is needed in this area. In Cuba, Pereira. Person in charge 300 2377012 Janeth (you have
> to come up through Villa Ligia, because if you come up through Leningrado there are people
> who are not victims taking advantage and keeping the goods)

The second is operational intelligence that exists in no official source: not just where the
need is and who coordinates it, but **which route to take so the aid is not diverted**. No
city hall publishes that.

## Geolocation: the finest granularity

`locationMeta.locationName` arrives on 19% of videos, below Instagram (34%), but the
granularity is far better: `"Vereda Kilómetro 41"`, `"Cuchilla de los Castros, Cuba,
PEREIRA"`. Instagram gives you a city; TikTok gives you a rural district or a neighborhood.
For the demand axis that is worth more than the coverage rate.

## Cascading emergency signal

TikTok was the only source that surfaced the secondary disasters: **flooding in Quibdó** on
the night of the 14th following the earthquake, and a **windstorm in the Casacará
corregimiento (Agustín Codazzi, Cesar)**. The agent must treat this as a trigger to
re-evaluate the event, not as noise: an already-hit area that then floods changes priority
completely.

## Comments: 227 analyzed

The original hypothesis about mining comments was tested. Nuanced result:

| Signal | Hits |
|---|---|
| Contact phone number | 0 / 227 |
| Fine-grained location (neighborhood, rural district) | 5 / 227 |
| Expressed need | 14 / 227 |
| Person looking for how to help | 2 / 227 |

**As a source of structured items, comments are poor** (~6% density, far below FB or IG). But
they contain something no other layer has:

> "hi I have children and food ran out where can I go"
> "for people who need food, where can they go?"
> "do they still need volunteers at the Coliseo Mayor???"
> "go to the north side of Quibdó, La Victoria neighborhood"
> "my municipality in Chocó department needs help"

Comments are where **unmet demand for information** lives. People who need help and do not
know where to go; people who want to help and do not know where. They are literally the
product's users, writing their need in plain text. And locals name underserved neighborhoods
that no other layer reported.

**Conclusion: comments are not a harvest layer, they are a discovery layer.** Use them to feed
the frontier new toponyms and to measure product demand, not to fill the item database.

Operational note: contacts migrate to direct message ("DM me"). The phone number almost never
stays in the public comment.

## TikTok's role

Not a sweeping platform. It is a **precision instrument for ring T0**: few queries, high
frequency, impact zone only, where its rural-district granularity and its access to on-foot
local coordinators justify paying 16× more per item.

---

# FINAL PLATFORM COMPARISON

| | X | Instagram | Facebook | TikTok |
|---|---|---|---|---|
| Unit cost | $0.00025 | $0.0026 | $0.0025 | $0.0037 |
| Sample | 400 | 112 | 100 | 100 |
| Actionable | 39 (9.8%) | 38 (34%) | 37 (37%) | 9 (9%) |
| Cost per actionable | **$0.0026** | $0.0077 | $0.0070 | $0.041 |
| Structured geo | 1.5% (`place`) | 34% (`locationName`) | none | 19%, finest granularity |
| Demand axis | yes | almost none | **yes, the best** | yes, highest quality |
| Supply axis | yes | **yes, the best** | yes | yes |
| Rural long tail | no | no | **yes** | partially |
| Payment details | rare | sometimes | **frequent** | sometimes |
| Native date filter | yes | **no** | yes | yes |
| Free image/audio text | no | no | **alt-text, 100% of media** | **subtitles, 70%** |

**X** is the cheapest per actionable item and the only one with good time and volume
filtering: use it for broad sweeps and for the urban demand axis.
**Instagram** is the commercial collection-point directory, with structured geo.
**Facebook** is the one that finds who nobody is looking at. Most valuable for the mission
even though it is not the cheapest.
**TikTok** is a precision instrument for ground zero, not a sweeping tool.

Total pilot spend: **$1.32 of $5**.
