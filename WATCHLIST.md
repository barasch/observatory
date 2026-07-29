# Private people-watch registry

The repository contains a public schema example, `config/people.example.json`, but not the real registry. The first version stores the real JSON in a GitHub Actions repository secret.

## Add the registry

1. Prepare a UTF-8 JSON file using `config/people.example.json` as the shape.
2. In this repository, open **Settings → Secrets and variables → Actions**.
3. Create a repository secret named exactly `PEOPLE_WATCHLIST_JSON`.
4. Paste the complete JSON object as the secret value.
5. Run **Actions → Update and publish Observatory → Run workflow**.

The workflow reads the secret directly into memory. It does not write the registry, queries, rejected candidates, or identities without matches into the public repository. Published matches necessarily reveal the subject of the match.

## Identity fields

Each object in `people` may contain:

| Field | Required | Function |
|---|---:|---|
| `id` | Yes | Private lowercase slug, unique within the registry. |
| `display_name` | Yes | Canonical public name shown when a match is published. |
| `category` | Recommended | Free-text class such as `academic`, `writer`, `musician`, `artist`, or `commentator`. |
| `aliases` | Recommended | Other exact public names, initials, transliterations, or professional names. |
| `distinctive_name` | Yes | If `true`, an exact name can pass without a context term. Use sparingly. |
| `require_any` | Usually | At least one occupation, institution, location, title, or other disambiguator that must appear with the name. |
| `exclude_any` | Optional | Terms identifying common false positives or namesakes. |
| `search_query` | Optional | Complete Google News query. If absent, the collector builds one from the name and `require_any`. |
| `official_feeds` | Optional | Direct RSS or Atom feeds attributable to this person. These pass as `DIRECT SOURCE` matches without name filtering. |
| `openalex_id` | Optional | Stable OpenAlex author ID for scholarly works. Prefer this to an academic name search. |
| `google_news` | Optional | Defaults to `true`; set `false` to use only direct feeds and stable identifiers. |
| `enabled` | Optional | Defaults to `true`; set `false` to retain an entry without querying it. |

## Identification rule

Do not solve ambiguity by adding many broad context words. Identify the person as if distinguishing records in a database:

- Record the full canonical professional name.
- Add real publication names, institutional affiliations, professions, stage names, or geographic identifiers.
- Add known namesakes to `exclude_any`.
- Use an official feed or stable identifier whenever one exists.
- Mark `distinctive_name` true only when an ordinary exact-name web search has negligible namesake risk.

An automatically discovered news item is published only when its title or supplied description contains:

1. the canonical name or an alias;
2. at least one `require_any` term, unless `distinctive_name` is true; and
3. none of the `exclude_any` terms.

Items below that fixed threshold are discarded. There is no review queue and no machine-generated adjudication of identity.

## Validation

Before installing the secret, validate a local file:

```bash
python scripts/validate_people.py /path/to/people.json
```

The command checks the schema and duplicate identifiers but cannot prove that a search query uniquely identifies a human being. Test difficult identities by examining ordinary search results and adding narrow disambiguators.

## Scale limit

GitHub Actions secrets are appropriate for the initial 20-person registry, but a single secret is limited in size. Before the registry approaches hundreds of detailed entries, move collection into a private companion repository and publish only sanitized output to this public repository. The public item schema already supports that migration.

