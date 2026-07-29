# Observatory method

Observatory collects public metadata from a fixed source registry and a private people-as-topics registry. It publishes links, source-supplied titles and compact descriptions, dates, publishers, and collection status. The public source registry is [`config/sources.json`](config/sources.json).

## Collection and publication

- Collection runs once daily at 5:17 a.m. Eastern Time.
- The home page shows the newest 30 collected items and remains unchanged until the next collection.
- The archive contains the latest 30 calendar days, grouped by publication date.
- A successful retrieval establishes that an item appeared at the configured endpoint. It does not independently verify every assertion in the linked material.
- Automated access can fail, feeds can change, timestamps can be incomplete, and public search indexes can omit relevant results. The page reports collector failures rather than silently substituting another source.

## Public category labels

The collector retains stable internal codes so existing records and adapters remain compatible.

| Public label | Internal code | Operational meaning |
| --- | --- | --- |
| Measured | `MEASURED` | An instrument, transaction system, administrative process, or accounting record directly produced the underlying observation. |
| Estimate | `ESTIMATED` | Sampling, modeling, seasonal adjustment, imputation, or projection materially contributes to the reported value. |
| Filing | `FILED` | A party formally submitted the record. This establishes the submission and attribution, not every assertion in it. |
| Adjudged | `ADJUDGED` | A court issued the disposition or opinion. This identifies legal effect, not independent proof of every factual recital. |
| Report | `REPORTED` | An identified institution or person made the statement or published the finding. |
| Analysis | `INTERPRETED` | The item primarily synthesizes, argues, explains, or forecasts from other facts. |

The label describes what kind of record the linked item is. It is not a universal quality score for the publisher.

## Ordering and text

Items are selected and ordered by publication time. Date-only records remain date-only and do not receive a fabricated clock time. Titles and descriptions are source supplied; Observatory does not insert generated summaries or use engagement, sentiment, or personalized importance scores.

## People matches

The identity registry is stored in the encrypted `PEOPLE_WATCHLIST_JSON` repository secret and is not committed. Instructions and the complete schema are in [`WATCHLIST.md`](WATCHLIST.md) and [`config/people.example.json`](config/people.example.json).

Direct feeds and stable author identifiers are accepted as direct matches. Broad-news results must contain a canonical name or configured alias and, unless the name is marked distinctive, a disambiguating term. Configured exclusions are then applied. There is no manual review queue; candidates below the fixed threshold are discarded.

Published matches necessarily reveal some interests, but not the complete registry, unused disambiguators, people who never match, or discarded candidates.
