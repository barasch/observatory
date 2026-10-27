# Media-attention data contract, version 1

This document specifies the JSON admitted by `scripts/media_validate.py`. The examples under `tests/fixtures/media/` are synthetic and do not describe real outlets or empirical networks.

Every document is a UTF-8 JSON object with `schema_version: 1` and a recognized `document_type`. The validator rejects unknown fields at every level. Timestamps include seconds and an explicit `Z` or numeric UTC offset. Public URLs must use HTTPS and cannot contain embedded credentials, whitespace, control characters, or backslashes. Files ending in `.json.gz` are accepted by the command-line validator.

The registry and candidate reservoir are checked-in research inputs. Stage 2 collection batches and health reports are private intermediate records under `.work/`; they are contracts so later stages cannot reinterpret failures or silently acquire extra fields. The observation manifest, weekly model, diagnostics, and current snapshot remain planned public outputs.

## Files and roles

| Intended path | `document_type` | Role |
| --- | --- | --- |
| `config/media-observers.json` | `media-observer-registry` | Auditable entity, family, channel, and panel-state registry |
| `config/media-panel-candidates.json` | `media-panel-candidate-reservoir` | Auditable 150–200-row source-research reservoir and route state |
| `.work/media-networks/RUN_ID/collection.json` | `media-collection-batch` | Private normalized pre-clustering metadata and mirror groups |
| `.work/media-networks/RUN_ID/health.json` | `media-collection-health` | Private per-channel attempt result and recomputed run coverage |
| `data/networks/observations/YYYY-Www.json.gz` | `media-observation-manifest` | Compact observation-to-story evidence for one New York ISO week |
| `data/networks/weeks/YYYY-Www.json` | `media-weekly-model` | Complete memberships, participant shares, diagnostics, and lineage events |
| `data/networks/latest.json` | `media-current-snapshot` | Zero to three trailing-24-hour stories for each current network |
| `data/networks/diagnostics/*.json` | `media-model-order-diagnostics` | Comparable model-order evaluations and the recorded decision |
| `tests/fixtures/media/lineage-scenarios.json` | `media-lineage-scenarios` | Synthetic expected outcomes for the later lineage algorithm |

The first two paths and synthetic fixtures are checked in at Stage 2. The collector writes the next two paths only to the ignored private work directory. No production observation, model, snapshot, diagnostic, public page, or schedule exists.

## Candidate reservoir

Top-level fields: `document_type`, `schema_version`, `updated_at`, `target_range`, `selection_basis`, `inventory_sources`, `candidates`.

`target_range` states the allowed reservoir size and is currently 150–200. The validator enforces the bounds. Each inventory source has a stable `id`, factual `display_name`, HTTPS `url`, and `retrieved_at` date.

Each candidate has:

| Field | Contract |
| --- | --- |
| `id` | Stable lowercase candidate slug. |
| `display_name` | Factual public name. |
| `primary_medium` | One recognized medium used for coverage accounting. |
| `route_status` | `enabled-shadow`, `disabled-candidate`, or `unverified`. |
| `inventory_source_ids` | One or more declared provenance references. |
| `registry_observer_id` | Required for the two registered states and forbidden for `unverified`. |

When a registry is supplied, every registered observer must appear exactly once. `enabled-shadow` requires a `shadow` observer with at least one enabled channel; other registered rows must be `disabled-candidate`. The reservoir has no field for ideology, party, audience, quality, or inferred network.

## Observer registry

Top-level fields: `document_type`, `schema_version`, `updated_at`, `brand_families`, `observers`.

Each brand family has:

| Field | Contract |
| --- | --- |
| `id` | Stable lowercase slug. |
| `display_name` | Public factual name. |

Each observer has:

| Field | Contract |
| --- | --- |
| `id` | Stable lowercase slug for one editorial product. |
| `display_name` | Public display name. |
| `brand_family_id` | Required reference to a declared family. |
| `panel_status` | `candidate`, `shadow`, `active`, or `retired`. |
| `status_since` | ISO date on which the current state began. |
| `retired_at` | Required only when retired; all of that observer's channels must then be disabled. |
| `independence_basis` | Short factual reason this is one editorial product distinct from any siblings. |
| `channels` | One or more collection endpoints that all collapse to this observer. |

Each channel has a globally unique `id`, a `medium`, a lowercase-slug `collection_method`, an `endpoint_url`, a reader-facing `public_url`, and Boolean `enabled`. The recognized media are `website`, `newsletter`, `podcast`, `youtube`, `bluesky`, `x`, `tiktok`, and `instagram`. Collection methods identify adapters but are not political categories.

Endpoint URLs are globally unique. The same feed therefore cannot be assigned to two observers. A second channel for the same observer is allowed and expected for cross-platform mirrors.

## Stage 2 collection batch

Top-level fields: `document_type`, `schema_version`, `run_id`, `registry_updated_at`, `started_at`, `completed_at`, `timezone`, `lookback_started_at`, `items`, `mirror_groups`.

An item contains exactly:

| Field | Meaning |
| --- | --- |
| `id` | Stable hash-derived item identifier. |
| `observer_id`, `channel_id`, `medium` | Registry identity; all three must agree. |
| `normalized_title` | Unicode-normalized, markup-free title, at most 300 characters. |
| `url` | Canonical public HTTPS item URL with common tracking parameters and fragments removed. |
| `external_id` | Endpoint-provided identifier, or canonical URL fallback. |
| `attention_at` | Publication time or stable first-seen time. |
| `timestamp_basis` | `published` or `first-seen`. |
| `collected_at` | Time this item was normalized. |
| `mirror_group_id` | Exactly one declared within-observer mirror group. |

Items cannot contain a body, description, transcript, embedding, story identifier, cluster score, private field, or inferred classification. Times must fall within the declared lookback interval and run boundary. Channel/external-identifier pairs are unique.

Every mirror group contains one observer's items and records a deterministic earliest canonical item. Its `match_basis` is `singleton`, `canonical-url`, or `exact-title`. An exact-title group uses case-folded equality; it is not a fuzzy story match. Mirror groups partition the complete item set.

## Stage 2 collection health

Top-level fields: `document_type`, `schema_version`, `run_id`, `registry_updated_at`, `started_at`, `completed_at`, `summary`, `channels`.

Every configured channel appears exactly once. A channel row records observer and medium, status, attempt/completion times, HTTP status, final URL, raw and qualifying item counts, newest attention time, and a bounded error code. Nullable fields must be null when they do not apply.

The admitted statuses are `ok`, `empty`, `disabled`, `http-error`, `network-error`, `parse-error`, `unsafe-redirect`, and `unsupported-method`. Only `ok` and `empty` count as a successful check. The validator recomputes configured, enabled, attempted, succeeded, failed, and disabled channel counts; successful and failed observer sets; attempted media; retained item count; and mirror-group count. When a collection batch is supplied, run identity, times, registry version, item counts, and per-channel yields must match it.

## Weekly observation manifest

Top-level fields: `document_type`, `schema_version`, `week`, `generated_at`, `observations`, `stories`.

An observation contains exactly:

| Field | Meaning |
| --- | --- |
| `id` | Stable lowercase observation identifier. |
| `observer_id` | Registry observer that performed the act of attention. |
| `channel_id` | Exact collection channel; it must belong to `observer_id`. |
| `medium` | Must match the registered channel. |
| `normalized_title` | Compact normalized headline used for audit and clustering; at most 300 characters. |
| `url` | Public HTTPS item URL. |
| `attention_at` | Timestamp used by inference and scoring. It must fall in `week` in New York. |
| `timestamp_basis` | `published` when a reliable publication timestamp exists; otherwise `first-seen`. |
| `story_id` | Exactly one declared story event. |
| `cluster_confidence` | Number from zero through one. |

A story contains `id`, `canonical_title`, optional `canonical_url`, `first_attention_at`, `last_attention_at`, and all of its `observation_ids`. Every observation appears in exactly one story list; the declared bounds must equal the constituent timestamps. Descriptions, full text, transcripts, private annotations, and credentials are unknown fields and fail validation.

## Weekly model

Top-level fields record the `week`, optional `previous_model_week`, `generated_at`, Monday 4:00 a.m. Eastern `effective_at`, timezone, inference window, model metadata, counts, coverage, preceding-week usable observers, complete memberships, networks, lineage events, and diagnostics.

### Time and model metadata

`inference_window` contains Monday-midnight `start_at` and `end_at`, `completed_weeks`, and `half_life_weeks`. Its local calendar span must equal `7 * completed_weeks`; it is deliberately not validated as a fixed number of UTC hours.

`model` records:

- `algorithm`, fixed to `non-negative-matrix-factorization` for v1;
- a lowercase-slug `implementation_version`;
- the exact 7–40 character lowercase Git `code_version`;
- arbitrary positive `factor_count`;
- nonnegative integer `random_seed`; and
- `continuity_threshold`.

The schema never assumes ten factors.

### Coverage and memberships

`coverage` records included and excluded media, attempted and successful observer counts, and factual material limitations. `successful_observer_count` equals the number of membership rows.

Each membership row has `observer_id` and a `weights` object containing every and only current `lineage_id`. Weights are finite numbers from zero through one and sum to one within `1e-6`.

### Network record

Each network contains:

| Field | Contract |
| --- | --- |
| `lineage_id` | Immutable identifier such as `lineage-0001`. |
| `version_id` | Current lineage plus week, e.g. `lineage-0001@2026-W42`. |
| `label` | Neutral `Network A` form. More than 26 can continue as `Network AA`. |
| `factor_index` | A permutation of `0..factor_count-1`; it carries no identity. |
| `effective_membership` | Must equal the value recomputed from the full membership matrix. |
| `continuity` | Relationship, primary predecessor version or null, and exact cosine similarity or null. |
| `participants` | Every preceding-week observer with positive mass, ordered by derived network share. |

Each participant contains `observer_id`, derived `network_share`, and matching membership weight as `observer_affinity`. The validator recomputes the complete table; a plausible but wrong percentage fails.

`continuity.relationship` is `birth`, `continuation`, `split`, `merger`, or `uncertain`. Births have no predecessor. Claimed continuity meets the stored threshold; uncertain matches fall below it and use a new lineage. For a split, exactly one child retains the parent lineage. For a merger, the successor retains its primary parent’s lineage.

### Lineage events

Every current network is the target of exactly one event. An event has an `id`, `type`, predecessor and successor version arrays, and links containing each predecessor-successor pair’s cosine similarity and shared membership mass. A predecessor may occur in only one event for the week. Shapes are strict:

| Event | Predecessors | Successors |
| --- | ---: | ---: |
| birth | 0 | 1 |
| continuation | 1 | 1 |
| retirement | 1 | 0 |
| uncertain | 1 | 1 |
| split | 1 | 2 or more |
| merger | 2 or more | 1 |

### Diagnostics

The weekly record includes held-out reconstruction error and fraction, bootstrap stability and run count, observer and brand-family sensitivity, and one story-profile similarity for every unordered pair of current networks.

## Current snapshot

The snapshot records `scheduled_for`, actual `generated_at`, timezone, the half-open 24-hour window, `weekly_model_week`, coverage, and an arbitrary number of networks. `scheduled_for` must be a three-hour Eastern boundary. `window.end_at` equals it, and the interval is exactly 24 elapsed hours.

Coverage lists every attempted and successfully checked observer and at least one measured medium. The nonempty successful list is the denominator set; a run with no successful observers fails rather than publishing. Failures are not zeros.

Each network repeats its weekly lineage, version, label, continuity similarity, effective membership, and first three participants, followed by zero to three stories in descending attention-score order. When the weekly file is supplied, these repeated values must match it exactly.

Each story contains:

| Field | Meaning |
| --- | --- |
| `story_id`, `title`, `canonical_url` | Event identity and principal evidence link. |
| `weighted_attention` | Membership-weighted, recency-decayed network score. |
| `panel_attention` | Equal-observer recency score across checked observers. |
| `over_index` | Exact `weighted_attention / panel_attention`. |
| `independent_observer_count` | Deduplicated observer breadth, minimum three. |
| `independent_brand_family_count` | Deduplicated family breadth, minimum three. |
| `latest_observed_at` | Latest timestamp among disclosed evidence. |
| `evidence_observation_ids` | Complete compact evidence set used for breadth and scoring. |
| `representative_links` | One to three links drawn from that evidence set. |

When the registry, weekly model, and applicable observation manifests are supplied together, the validator recomputes observer breadth, family breadth, the latest timestamp, the network score, and the panel score. An observer’s multiple posts reduce to its latest observation. A story may appear in more than one network.

## Model-order diagnostics

The document records a `prelaunch-exhaustive`, `routine-local`, or `triggered-exhaustive` comparison. Every listed cutoff must evaluate the same candidate set. Cutoffs are adjacent weeks so a proposed change is tested on two adjacent endpoints.

Prelaunch evaluation must contain every integer from 5 through 15. Exhaustive searches are contiguous. Routine review contains the current value and its two nearest values on each side. Each candidate records held-out error and standard error, bootstrap stability, minimum and median effective membership, agenda distinctiveness, observer sensitivity, brand-family sensitivity, and whether absolute gates passed.

The decision records the raw best value, the constrained selected value, one winner for each cutoff, whether a change is authorized, whether the range must expand, and a public rationale. The selected value must pass every absolute quality gate. A changed value cannot be authorized unless the same alternative wins at two adjacent cutoffs. A boundary best result requires expansion and cannot authorize a change yet.

## Validation

Validate one self-contained document:

```sh
python scripts/media_validate.py config/media-observers.json
python scripts/media_validate.py config/media-panel-candidates.json
python scripts/media_validate.py tests/fixtures/media/*.json
```

Validate the Stage 2 research inputs together:

```sh
python scripts/media_validate.py \
  --registry config/media-observers.json \
  config/media-panel-candidates.json
```

Validate a private collection run against its frozen registry and batch:

```sh
python scripts/media_validate.py \
  --registry config/media-observers.json \
  .work/media-networks/RUN_ID/collection.json

python scripts/media_validate.py \
  --registry config/media-observers.json \
  --collection-batch .work/media-networks/RUN_ID/collection.json \
  .work/media-networks/RUN_ID/health.json
```

Validate a snapshot against its frozen dependencies:

```sh
python scripts/media_validate.py \
  --registry tests/fixtures/media/observer-registry.json \
  --weekly-model tests/fixtures/media/weekly-model.json \
  --observations tests/fixtures/media/observations.json \
  tests/fixtures/media/current-snapshot.json
```

`--observations` may be repeated when a trailing window crosses an ISO-week boundary. Production publication must use the cross-document form before writing a snapshot.
