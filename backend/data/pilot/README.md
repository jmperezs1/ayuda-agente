# Pilot dataset

939 raw items harvested on 15 August 2026 against the Chocó earthquake (M7.4, 10 August 2026)
for $1.32 of Apify credit. Load them with:

```bash
make seed
```

These are the **unmodified Actor payloads**, pulled straight from the Apify datasets with no
field projection, so what the loader sees is exactly what a live harvest produces. Provenance
per file — dataset id, Actor, query intent, cost — is in `manifest.json`.

## Why this is committed

It makes the whole pipeline downstream of Apify developable and testable against real content
without spending credit or waiting on a scraper: extraction, classification, geocoding,
identity resolution and matching all run on this.

It is also the regression corpus. When a prompt changes, the question "did this get better or
worse" needs a fixed set of real posts to answer, and re-scraping would give a different one
every time.

## What it cannot test

**Images and video.** Platform media URLs are signed and expire within hours, so every
`source_url` in here is dead by now. We never downloaded the bytes.

What survives is the text the platforms hand over for free, and it is a lot: Facebook's
`accessibilityCaption` (its own OCR of the flyer, present on every post carrying media) is in
the payloads and is genuinely useful. TikTok's subtitle links are present but, being signed
URLs, no longer resolve.

So the vision half of extraction needs a fresh, small harvest to develop against. Everything
else does not.

## Refreshing

The Apify datasets were still retrievable when this was written. To re-pull, read the
`dataset_id` values from `manifest.json`:

```bash
curl -s "https://api.apify.com/v2/datasets/<dataset_id>/items?format=json&clean=true" \
  -H "Authorization: Bearer $APIFY_TOKEN" | gzip -9 > data/pilot/<file>
```

Apify's free tier retains datasets for a limited window, so treat the committed copies as the
source of truth rather than the remote ones.
