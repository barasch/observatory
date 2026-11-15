# Story events: Stage 3

**Implementation status:** private, unscheduled research only. Stage 3 can turn one validated Stage 2 collection batch into weekly observation manifests and a private cluster-audit file. It does not infer networks, compute attention scores, change navigation, publish data, or schedule a run.

## Purpose and evidentiary boundary

A story event is one time-bounded concrete development, independent of framing. Reactions to the same development may share an event. Similar topics, recurring programs, continuing controversies, and different acts involving the same person do not thereby share an event.

Stage 3 uses only metadata retained by Stage 2: normalized titles, URLs, public timestamps, observer and channel identities, and within-observer mirror groups. It does not fetch or store bodies, descriptions, transcripts, embeddings, X posts, Grok summaries, or private feedback. The rule is deliberately precision-first because a false merge alters the later observer-by-story matrix, while a missed merge ordinarily leaves two sparse columns that can still be audited.

Candidate retrieval and event judgment remain separate. A 96-hour interval supplies possible pairs. Retrieval is not a label. A pair may merge only under a declared evidence rule, and clusters use complete linkage: every cross-cluster pair must independently pass. This prevents a chain of individually plausible pairs from joining two observations that do not match each other.

## Relation to prior work

The implementation is a small, inspectable baseline rather than a claim to reproduce a modern proprietary topic system.

- Laban, Bandarkar, and Hearst's 2021 [Headline Grouping Dataset](https://aclanthology.org/2021.naacl-main.255/) supplies human group judgments for difficult same-event headline pairs. It is the external precision benchmark.
- Miranda et al.'s 2018 [Multilingual Clustering of Streaming News](https://aclanthology.org/D18-1483/) and its [open reimplementation](https://github.com/Priberam/news-clustering) establish the relevance of time, lexical evidence, entities, learned thresholds, and streaming evaluation. Their richer feature pipeline cannot be treated as available when Stage 2 deliberately retains titles rather than article text.
- Saravanakumar et al.'s 2021 [entity-aware stream-clustering study](https://aclanthology.org/2021.eacl-main.198/) supports testing entity-sensitive representations later, after a suitable in-domain benchmark exists.
- X's open recommendation work is useful for candidate retrieval and audience-affinity research, but SimClusters-style co-interest structure is not evidence that two items describe the same event. X News topics, entities, summaries, or story identifiers may later serve as an external comparison; none defines Observatory ground truth.

## Frozen baseline

Each Stage 2 mirror group becomes one comparison unit. Original items remain separate observations for audit, but all members of a mirror group inherit the same story assignment.

Titles are Unicode-normalized for comparison. Common function words and headline boilerplate are excluded from similarity features; the retained title is never rewritten. Candidate pairs must share at least three informative tokens before sequence comparison. The admitted merge routes are:

1. an informative normalized exact title within 96 hours;
2. the same canonical URL with substantial title support;
3. at least four shared informative tokens, 75 percent containment of the shorter title, 30 percent Jaccard overlap, and 50 percent sequence similarity within 24 hours; or
4. a more restrictive entity-supported paraphrase rule within 96 hours.

Conflicting numeric sets reject a merge. Fuzzy pairs from one observer face an additional near-duplicate gate: at least 75 percent Jaccard overlap and 70 percent sequence similarity. Exact titles from one observer must occur within 24 hours. This prevents recurring editions, compound podcast episodes, and multiple clips from one interview from manufacturing a common event merely by repeating program or guest language.

Clusters use complete linkage. The canonical title and URL are selected from an observed item, not synthesized. The selected title is the cluster medoid under the same audit score, with informative length and time used only as deterministic tie-breakers. A story identifier is anchored to its earliest constituent item so later additions do not ordinarily rename the event. Up to three evidence links are selected from distinct observers; later scoring may reorder or narrow them using network membership, but it cannot introduce an unobserved link.

`cluster_confidence` is a deterministic rule score and separation margin, not a statistical probability. Values such as `0.97` mean that a named rule passed; they do not mean that the assignment has an estimated 97 percent probability of being correct.

## External benchmark

Thresholds were selected on the Headline Grouping Dataset development split and then frozen before the test split was evaluated. The checked-in [benchmark report](story-event-benchmark.json) records release hashes and complete counts.

| Split | Proposed merges | Correct | False | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development | 65 | 62 | 3 | 95.38% | 8.36% |
| Untouched test | 52 | 51 | 1 | 98.08% | 8.64% |
| Combined audit | 117 | 113 | 4 | 96.58% | 8.48% |

The combined audit clears the provisional requirement of 95 percent precision over at least 100 proposed merges. It is not an untouched holdout result because it includes the development split. The low recall is material: this baseline recognizes only a narrow, high-confidence subset of same-event pairs.

## Observatory shadow result

The revised Stage 3 trial consumed the final 35-day Stage 2 batch dated October 27, 2026.

| Measure | Result |
| --- | ---: |
| Retained Stage 2 items | 2,550 |
| Within-observer mirror units | 1,925 |
| Pairs retained for audit | 196 |
| Proposed merge pairs | 19 |
| Multi-unit stories | 15 |
| Total stories | 1,909 |
| Weekly manifests | 6 |

The first in-domain pass proposed 34 merges. Inspection exposed false same-program matches among interview clips, recurring editions, and compound episode titles. The observer-aware and numeric gates reduced that set to 19 facially defensible pairs. That implementation review is not independent human adjudication and cannot establish local precision.

Before Stage 3 can feed inference or publication, additional shadow runs must accumulate at least 100 locally proposed merges. Those pairs must be labeled blind to the system decision as same event, different event, or uncertain; uncertain pairs are excluded from the point estimate and reported separately. The result must meet the 95 percent precision target, disclose its sample size and uncertainty, and include difficult same-entity negatives across website, podcast, and video titles. X News may be compared only after those judgments are fixed.

## Run and verify

Run a private clustering trial against a validated Stage 2 batch:

```sh
python scripts/media_cluster.py \
  --input .work/media-networks/RUN_ID/collection.json \
  --registry config/media-observers.json \
  --output-dir .work/media-networks-stage3
```

The command writes one ignored run directory containing `audit.json` and weekly files under `observations/`. It validates every manifest before the atomic directory rename and refuses to replace an existing run.

Reproduce the external audit after downloading the pinned HLGD 0.1 classification release:

```sh
python scripts/media_cluster.py --benchmark PATH/TO/dev.json
python scripts/media_cluster.py --benchmark PATH/TO/test.json
jq -s 'add' PATH/TO/dev.json PATH/TO/test.json \
  | python scripts/media_cluster.py --benchmark /dev/stdin --require-gate
```

The separate development and test results matter. Passing only the combined command does not convert the development rows into holdout evidence.
