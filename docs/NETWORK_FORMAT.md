# Media-attention public data contract, version 1

This document specifies the JSON admitted by `scripts/media_validate.py`. The examples under `tests/fixtures/media/` are synthetic and do not describe real outlets or empirical networks.

Every document is a UTF-8 JSON object with `schema_version: 1` and a recognized `document_type`. The validator rejects unknown fields at every level. Timestamps include seconds and an explicit `Z` or numeric UTC offset. Public URLs must use HTTPS and cannot contain embedded credentials, whitespace, control characters, or backslashes. Files ending in `.json.gz` are accepted by the command-line validator.

## Files and roles

| Intended path | `document_type` | Role |
| --- | --- | --- |
| `config/media-observers.json` | `media-observer-registry` | Auditable entity, family, channel, and panel-state registry |
| `data/networks/observations/YYYY-Www.json.gz` | `media-observation-manifest` | Compact observation-to-story evidence for one New York ISO week |
| `data/networks/weeks/YYYY-Www.json` | `media-weekly-model` | Complete memberships, participant shares, diagnostics, and lineage events |
| `data/networks/latest.json` | `media-current-snapshot` | Zero to three trailing-24-hour stories for each current network |
| `data/networks/diagnostics/*.json` | `media-model-order-diagnostics` | Comparable model-order evaluations and the recorded decision |
| `tests/fixtures/media/lineage-scenarios.json` | `media-lineage-scenarios` | Synthetic expected outcomes for the later lineage algorithm |

Only the first path and synthetic fixtures exist in Stage 1. No production observation, model, snapshot, or diagnostic file is yet generated.

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
python scripts/media_validate.py tests/fixtures/media/*.json
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
