# Public edition contract, version 1

Commit a UTF-8 JSON file to `data/editions/YYYY-MM-DD.json`. The filename date, `date`, and preparation date in America/New_York must match. The daily research starts at 02:00; `prepared_at` records actual completion, not the intended start time. One edition per day. Existing editions are not automatically overwritten.

Top-level allowed fields: `schema_version`, `date`, `prepared_at`, `items`, optional `coverage_note`. `items` contains 1–15 entries, normally fifteen. The strict allowlist rejects additional fields. `coverage_note` is limited to 100 words.

Each entry requires:

| Field | Meaning |
| --- | --- |
| `id` | Stable lowercase letters/digits/hyphens, 3–101 characters. Identify the primary document, not its appearance date; preserve the id if it reappears. |
| `category` | `law`, `economy`, `government`, or `science`. |
| `title` | Descriptive plain-text headline. |
| `publisher` | Issuing institution or authors/institution where appropriate. |
| `published` | Verified publication date, YYYY-MM-DD. No invented dates. |
| `geography` | Short geographic label. |
| `summary` | Plain text, target 60–90 words; validator accepts 30–120. |
| `source_type` | E.g. Court opinion, Statistical release, Working paper, Corporate filing. |
| `source_url` | HTTPS publisher page that describes or links to the original. |
| `document_url` | HTTPS primary document or original HTML/data page. Can equal source_url. |
| `tags` | 1–8 short factual subject labels. Never preference labels. |
| `detail` | Optional plain text visible when expanded; no more than 120 words. |
| `preview_url` | Optional HTTPS image of an actual document page or source figure. |

`coverage_note` is optional, public, factual text about a material gap or limited edition. No private personalization rationale, ranks, feedback counts, inferred interests, profile data, or credentials belong anywhere in an edition.

Validate before committing:

```sh
python scripts/editions.py data/editions/YYYY-MM-DD.json
python scripts/build.py --check
python -m unittest discover -s tests -v
npm ci
npm test
```

For connected-tool publication, read the current file first to guard against duplicate creation; create it via GitHub Contents or make an ordinary fast-forward commit. Do not force-push. Do not modify any other public file during a scheduled editorial run. The build starts when the commit reaches main. Verify its result.
