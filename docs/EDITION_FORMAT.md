# Edition data format

Public edition files are UTF-8 JSON at `data/editions/YYYY-MM-DD.json`. Historical schema-1 daily files remain valid. New weekly files use schema 2 and the preparation date in New York as filename and `date`.

```json
{
  "schema_version": 2,
  "date": "2026-10-18",
  "prepared_at": "2026-10-18T06:12:00Z",
  "window": {"start": "2026-10-05", "end": "2026-10-18"},
  "coverage_note": "Optional public note under 100 words.",
  "items": []
}
```

`window.start` is inclusive and `window.end` exclusive. Both are complete New York calendar dates; end cannot follow the edition date. Weekly windows cannot overlap. An edition contains 1–20 items; the editorial target is 15–20. Fewer than 15 requires a completed search and an editorial shortage under the private publication gate.

Each item contains exactly:

- required: `id`, `category`, `title`, `publisher`, `published`, `geography`, `summary`, `source_url`, `document_url`, `source_type`, `tags`;
- optional: `detail`, `preview_url`, `revision_of`.

`category` is `law`, `economy`, `government`, or `science`. `published` must be the first public release date within the window, except for a substantive revision. Summaries are 30–120 words with a 60–90 target; detail is at most 120 words; tags contain 1–8 factual labels. URLs must be credential-free HTTPS. Document URLs cannot duplicate another item. Unknown fields are rejected because public data must never carry private selection material.

A substantive revision of a currently upvoted original adds:

```json
"revision_of": {
  "item_id": "stable-original-item-id",
  "edition_date": "2026-09-30",
  "changes": "A concise public account of what materially changed."
}
```

The original item must exist in the named earlier edition. Changes contain 1–60 words. The new item's `published` is the revision release date. Cosmetic corrections are ineligible. The private publication gate verifies the current original upvote, the first-release exception, source inspection, and the exact weekly window.

Run `python scripts/editions.py data/editions/YYYY-MM-DD.json` for structural validation. Editorial runs must additionally use `scripts/weekly.py` and its private verification records before any write. A structurally valid file is not proof that the primary material was inspected or that publication succeeded.
