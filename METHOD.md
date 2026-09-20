# Observatory method

Observatory publishes approximately fifteen primary sources each day, selected and summarized by a scheduled ChatGPT task. Research starts at 2:00 a.m. America/New_York; publication follows completion. The public page groups tiles under Courts & Law, Economy & Finance, Government & Public Services, and Science & Research. Category balance is a soft target over thirty days.

Each editorial run is bounded by countable retrieval limits: one agent, no more than ten search queries, sixty total retrieval actions, twenty-two primary documents, twenty discovery pages, and 30,000 retrieved source words. It publishes no more than fifteen tiles and permits one validation repair. Reaching any ceiling ends research; the edition may therefore contain fewer than fifteen items. These are operational limits, not a claim to meter hidden model tokens, which ChatGPT Scheduled does not expose.

Government releases, court opinions and filings, original academic research, corporate filings, and original institutional reports are eligible. News may lead to a primary document but cannot replace access to it. Selection gives equal weight to consequence, research usefulness, and discovery. The issuing institution, publication date, geographic scope, and original source accompany each summary. Primary status does not establish the truth of every claim; summaries should distinguish estimates, allegations, findings, and legal holdings.

Private, explicit thumbs-up and thumbs-down feedback guides future selection. Votes are reversible and may include an explanation. Dismissal only moves an item into a collapsed section; restoring it is neutral. Non-engagement and clicks are not preference signals. Exploration remains possible in every subject, including those repeatedly rated negatively.

The site is publicly readable. A passphrase unlocks private feedback and preferences. These are stored in a separate private GitHub repository, never in the public site data. The publicly downloadable credential record is encrypted; a long randomly generated passphrase protects it. Browser credentials and decrypted feedback remain only in memory for that tab. The task uses the owner's authorized GitHub connection to read private inputs independently.

Each public edition is schema-validated before rendering. The curated archive shows the last thirty calendar days, while the last successful edition stays on the homepage during failures. Historical source records from the previous collector remain separately accessible. Public repository history retains earlier editions.

See [scheduled setup](docs/SCHEDULED_TASK.md), [editorial instructions](docs/DAILY_EDITOR.md), and [edition format](docs/EDITION_FORMAT.md). A configured schedule is not evidence that a particular run completed; check the edition date and actual deployment result.
