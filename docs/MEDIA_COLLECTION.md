# Media-attention collection: Stage 2

**Implementation status:** private shadow collection only. This stage does not cluster stories, infer networks, compute attention scores, schedule runs, publish data, or add a public interface.

## Purpose and boundary

Stage 2 turns public machine-readable endpoints into compact, validated metadata for later research. It preserves the Stage 1 identity boundary:

1. a channel is one endpoint;
2. mirrored channels collapse to one observer;
3. related observers share a brand family; and
4. network membership is absent because it has not been inferred.

The collector retains only a normalized title, public item URL, endpoint-local external identifier, observer/channel/medium identifiers, attention timestamp and basis, collection timestamp, and mirror-group identifier. It does not retain article bodies, descriptions, transcripts, embeddings, private feedback, credentials, story identifiers, political labels, or network assignments.

All run artifacts and first-seen state are written atomically under `.work/media-networks/`, which is ignored by Git. A failed run cannot alter any public file.

## Candidate reservoir and registered shadow panel

`config/media-panel-candidates.json` is the auditable research reservoir. Its 191 current rows fall within the Stage 1 target of 150–200. The file distinguishes an inventory lead from a verified route; appearing in it is not an endorsement or a claim that the product is active, independent, or suitable for promotion.

| Primary medium | Reservoir candidates | Registered observers | Enabled channels |
| --- | ---: | ---: | ---: |
| Website | 128 | 61 | 50 |
| Podcast | 48 | 41 | 40 |
| YouTube | 4 | 4 | 21 |
| Newsletter | 6 | 0 | 0 |
| Bluesky | 5 | 0 | 0 |
| **Total** | **191** | **106** | **111** |

The mismatch between observer and channel counts is intentional. A podcast or show can have podcast and YouTube channels without becoming two observers. The registered panel has 106 observers in 88 brand families: 93 are enabled in `shadow`, 13 remain disabled `candidate` entries, and none are `active`.

The current reservoir status is:

| Route status | Count | Meaning |
| --- | ---: | --- |
| `enabled-shadow` | 93 | At least one route is configured for private health and yield measurement. |
| `disabled-candidate` | 13 | A prior route failed or returned no usable feed; it is preserved for re-research. |
| `unverified` | 85 | Inventory lead only; identity, family, endpoint, and reliability still require review. |

### Inventory sources

The reservoir combines several source-discovery frames rather than treating any one list as a sampling frame:

- [Pew Research Center News Media Tracker](https://www.pewresearch.org/journalism/2025/06/10/news-media-tracker-frequently-asked-questions/) for prominent cross-platform national products;
- [Pew's America's News Influencers](https://www.pewresearch.org/journalism/2024/11/18/americas-news-influencers/) for creator-led products and cross-platform identity problems;
- [Media Cloud Directory](https://www.mediacloud.org/media-cloud-directory) for a much larger international and research-oriented source inventory;
- [The Election Newspaper source directory](https://theelectionnewspaper.com/sources) for national, specialist, state, and local written outlets;
- [The Election Newspaper podcast directory](https://theelectionnewspaper.com/podcasts) for current political-program leads; and
- [Standing Record monitoring sources](https://standingrecord.com/sources/) for specialist and public-interest feeds.
- [Bluesky public profiles](https://bsky.app/) for direct verification of the five Bluesky-first research leads.

These sources are used only as factual inventory leads. Their ratings, political categories, or editorial judgments, where present, are not copied. Candidate selection records medium and provenance, then requires route verification, distinct-product review, family assignment, reliability, and marginal coverage value before shadow registration.

The next panel review should prioritize the current structural gaps: state and local reporting, newsletters, Bluesky, Spanish-language products, and accessible replacements for blocked national feeds. Closed or inconsistently accessible platforms remain excluded from collection even when their products appear in the research reservoir.

## Adapters and URL safety

The Stage 2 collector supports two adapter names:

| Adapter | Input | Output |
| --- | --- | --- |
| `feed` | HTTPS RSS or Atom | Title, link, external identifier, and publication timestamp when present |
| `bluesky-author` | `public.api.bsky.app` author-feed JSON with an explicit `actor` | Authored post text as title, public post URL, AT URI, and creation time |

The production registry currently enables only `feed`; Bluesky candidates have not yet passed handle and identity review. The adapter verifies each returned author against the requested handle or decentralized identifier and excludes repost wrappers, whose attention time and semantics require a separate rule.

Requests have a 25-second timeout, a 32 MB response ceiling, an explicit user agent, and an HTTPS-only final URL. Non-HTTPS item links, credential-bearing URLs, unsafe redirects, malformed XML/JSON, oversized responses, and unsupported adapters fail closed. Common tracking parameters and fragments are removed from retained item URLs.

When a reliable publication time is absent, the collector uses a stable `first-seen` timestamp persisted by channel, external identifier, and canonical URL. Re-runs therefore do not make an old undated item appear newly published.

## Mirror collapse

Mirror collapse happens only within one observer. Every retained item belongs to exactly one mirror group.

- Identical canonical URLs collapse.
- Otherwise, case-folded normalized titles must be exactly equal, come from different channels, and be observed within 36 hours.
- Fuzzy similarity is not used.
- Same-channel repeated titles do not collapse merely because the title repeats.
- A URL group containing different titles cannot bridge transitively into a title group.
- The earliest deterministic item is recorded as canonical; all members remain available for audit.

This is duplicate-channel normalization, not story clustering. Two independent observers publishing the same headline remain independent items for the later story-event stage.

## Health semantics

Each configured channel receives exactly one status:

| Status | Interpretation |
| --- | --- |
| `ok` | Fetch and parse succeeded with one or more qualifying items. |
| `empty` | Fetch and parse succeeded but no item fell in the lookback window. |
| `disabled` | The route was deliberately not attempted. |
| `http-error` | The endpoint returned an HTTP failure. |
| `network-error` | DNS, timeout, TLS, or transport failure. |
| `parse-error` | The bounded response could not be parsed safely. |
| `unsafe-redirect` | The final location violated URL policy. |
| `unsupported-method` | No adapter exists for the configured method. |

Only `ok` and `empty` make an observer successfully checked. A failure is never converted into evidence of inattention. Counts, observer sets, media coverage, item totals, and run times are recomputed by the validator rather than trusted from the report.

## Unscheduled shadow result, 2026-10-27

The final Stage 2 trial used a 35-day lookback and produced only ignored local artifacts:

| Measure | Result |
| --- | ---: |
| Configured / enabled / disabled channels | 125 / 111 / 14 |
| Successful / failed channel attempts | 105 / 6 |
| `ok` / `empty` | 101 / 4 |
| Successful / fully failed shadow observers | 88 / 5 |
| Retained compact items | 2,550 |
| Mirror groups after collapse | 1,925 |

The six final channel failures comprised four HTTP failures and two endpoints that returned parseable HTML rather than RSS or Atom; those HTML responses are `parse-error`, not `empty`. All 21 enabled YouTube feeds succeeded in this run, after a preceding trial in which they failed together. That reversal is direct evidence that one successful or failed attempt is insufficient for panel promotion or route retirement. No observer can move to `active` until repeated health runs and the unresolved medium gaps are reviewed.

## Run and validate

Run a private shadow collection:

```sh
python scripts/media_collect.py \
  --registry config/media-observers.json \
  --output-dir .work/media-networks \
  --lookback-days 35
```

Validate the registry and reservoir together:

```sh
python scripts/media_validate.py config/media-observers.json
python scripts/media_validate.py \
  --registry config/media-observers.json \
  config/media-panel-candidates.json
```

Validate one completed private run:

```sh
python scripts/media_validate.py \
  --registry config/media-observers.json \
  .work/media-networks/RUN_ID/collection.json

python scripts/media_validate.py \
  --registry config/media-observers.json \
  --collection-batch .work/media-networks/RUN_ID/collection.json \
  .work/media-networks/RUN_ID/health.json
```

Stage 3 may consume a validated collection batch, but it must add story identifiers through a separate clustering contract. Stage 2 does not fabricate singleton stories to satisfy the later observation-manifest schema.
