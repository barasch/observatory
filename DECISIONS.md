# Provisional decisions for review

These choices were made to put a coherent first version on its feet without waiting for further preferences.

1. **Static GitHub Pages site.** There is no public database, login, server, cookie, or account system. GitHub Actions performs collection and deployment.
2. **Cross-site masthead.** The SB/Observatory wordmark returns home. Primary navigation links to Archive, Notes, and The City; method, source configuration, and implementation notes remain in the public repository and footer.
3. **Thirty-item daily edition.** The newest 30 items are divided among collapsible evidence sections, with people matches in a separate section. The selection remains based on publication time.
4. **Rolling 30-day archive.** Archive pages are generated only for the latest 30 calendar days. There is no deliberate historical backfill, and a per-person public archive remains deferred.
5. **Stable internal codes, concise public labels.** Existing data retains the codes `MEASURED`, `ESTIMATED`, `FILED`, `ADJUDGED`, `REPORTED`, and `INTERPRETED`. The public interface displays Measured, Estimate, Filing, Adjudged, Report, and Analysis.
6. **Source text only.** Titles and compact descriptions come from sources. No language model summarizes, ranks, or characterizes items.
7. **One collection daily.** Collection runs at 5:17 a.m. Eastern. Code pushes rebuild from the existing data rather than changing the day’s edition.
8. **Private registry as an encrypted secret.** This is simple and sufficient for 20 people. A private collector repository is the planned scaling path.
9. **Conservative automatic identity matching.** Name-plus-context is the default. Weak name-only results are discarded rather than published for review.
10. **Free discovery first.** Google News RSS is the broad-web discovery layer; direct feeds and stable identifiers take precedence. Brave Search is not yet used.
11. **No embedded players or images in version one.** The interface is fast, text-forward, and less exposed to third-party tracking. Media can be added per source later.
12. **Official-source failures remain visible.** The collector does not silently replace a blocked official court feed with a secondary reporter. A secondary source can be added explicitly and labeled.
13. **Shared Tufte-derived identity.** Observatory and Notes use ET Book, the same restrained paper-and-ink palette, one roman treatment for mastheads and page titles, and the SB mark as favicon and identifier. Observatory retains original layout CSS for its record interface; its earlier circled-O mark remains available as a dormant asset.
14. **Public output is not disclaimed into non-responsibility.** The notice accurately states automatic inclusion and non-endorsement. It does not claim that automation eliminates the site owner's legal or reputational responsibility.
15. **Date precision is preserved.** Date-only records display without a fabricated clock time. The New York reporter feeds omit standard publication-date fields, so their decision dates are extracted only from the official summaries and only because those three source entries explicitly authorize that rule.
16. **One category per row.** Evidence categories and People occupy separate full-width rows at every viewport size. Text within each row retains a restrained reading measure.
17. **Current date is local to the reader.** The edition heading is rendered from the visitor’s browser date. The separate assembly timestamp remains fixed for the edition and uses Eastern Time.
