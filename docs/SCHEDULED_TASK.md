# Observatory scheduled task

Run daily at **02:00 America/New_York**. This is the start of research; publication follows completion. Use the named timezone, not a fixed UTC offset. If the scheduler skips the nonexistent 02:00 at the spring daylight-saving transition, run that day's edition manually at 03:00. Do not add a second daily job that could produce duplicate editions.

The site is a static publication. The scheduled ChatGPT task performs the research, selection, and writing. GitHub Actions validates and publishes its committed edition. No OpenAI API key or custom skill is required for this design. The old 05:17 feed-collection schedule is retired. Select **GPT-5.6 Sol** with **low reasoning effort** if the Scheduled interface offers those controls.

Scheduled does not expose a hard token quota. A number such as “30,000 tokens” in the prompt would be an unverifiable request, not an enforcement mechanism. The task therefore uses the auditable ceiling in `docs/DAILY_EDITOR.md`: one agent, 10 search queries, 60 total retrieval actions, no more than 22 primary documents and 20 discovery pages, no more than 30,000 source words, at most 15 tiles, and one repair attempt. Reaching any ceiling ends research. A genuinely hard all-in token ceiling would require an API runner that meters usage, not a ChatGPT Scheduled task.

## Prerequisites

1. Create **barasch/observatory-private**, set visibility to **Private**, initialize with a README, and use the `main` branch.
2. Give ChatGPT's connected GitHub app access to both `barasch/observatory` and `barasch/observatory-private`.
3. At the Observatory, select **Unlock → Setup**. Supply a fine-grained GitHub token scoped to those two repositories with **Contents: Read and write**. Save the generated passphrase and finish setup. Never paste this token or passphrase into a task prompt.
4. Verify the connected GitHub tool can read `barasch/observatory-private/feedback.json` before activating the schedule. A public repository or an inaccessible private repository is not a valid substitute.

The task uses ChatGPT's GitHub connection, not the browser's encrypted credential. The passphrase unlocks voting in the browser and is not needed by the task. The public `feedback-auth.json` contains encrypted credential material plus the private repository's name. Votes, explanations, preferences, and inferred interests are never committed to the public repository.

## Text to enter in Scheduled

**Title:** Publish Observatory edition

**Schedule:** Every day at 2:00 a.m., timezone America/New_York.

**Instructions:**

```text
Research, compose, and publish the next daily edition of my Observatory at https://barasch.github.io/observatory/.

Use the connected GitHub app. First read docs/DAILY_EDITOR.md and docs/EDITION_FORMAT.md from the main branch of barasch/observatory, and follow their current instructions. Read the public feedback-auth.json only to resolve feedback_repository and feedback_branch; never try to decrypt it. Verify the referenced feedback repository is private. Read its feedback.json and, if present, preferences.json and profile.json. If private storage is not configured, is public, or cannot be read, stop and report the setup problem; never silently proceed without available feedback or copy private state into a public location.

The Hard work budget in docs/DAILY_EDITOR.md is mandatory. Use one agent. Maintain its action counts as you work, stop discovery when any ceiling is reached, and report the final counts. Do not replace those ceilings with a time estimate or an asserted token estimate.

Search newly published government/public data, reports, court opinions and filings, original academic research, corporate filings, and original reports from nongovernmental organizations. Use news to discover primary documents, and select only entries with an accessible underlying source you actually inspected. Target 15 worthwhile items without padding. Give equal editorial weight to consequential developments, usefulness to my research and writing, and surprising findings. Keep the four stable categories roughly balanced over 30 days. US federal and New York are the geographic emphasis, with selected international material.

Use my chat interests lightly for an initial seed when relevant personal context is available. Keep any seed or inferred preference profile exclusively in the private repository. Explicit votes and explanations increasingly outweigh the seed. Use current, reversible votes, not a cumulative count of superseded votes. Ignore dismissals, restores, clicks, and non-engagement when learning preferences. Always include some exploration when worthwhile material is available; repeated negative feedback never becomes an absolute ban. Interest is not agreement with a source's conclusion. Do not personalize toward a presumed ideology.

Read the compact public editorial index once to avoid repetition and assess thirty-day category balance; do not reread all full editions. Write descriptive titles and approximately 60–90 words per item explaining the substantive finding and its potential interest. Distinguish allegations, estimates, findings, and legal holdings. Include publication dates, institution, geography, primary-document and publisher-page URLs. Public summaries must stand alone and must not mention my chats, votes, personal history, or private preferences. Use genuine document previews only if available; otherwise omit previews.

Produce a public edition conforming to docs/EDITION_FORMAT.md, using the current date in America/New_York. Validate it with scripts/editions.py and the site build/tests where execution is available. Commit only the validated edition to data/editions/YYYY-MM-DD.json on barasch/observatory main, using an ordinary fast-forward update or the GitHub contents API. This recurring publication is authorized. Do not modify site code, workflows, authentication files, or existing feedback as part of an editorial run. Do not create a duplicate edition if one already exists for that local date.

Update profile.json only in the private repository if useful, keeping inferences narrow, tentative, and traceable to current explicit feedback; never overwrite preferences.json or feedback.json. Verify the publication workflow and the live edition where tools permit. Report the edition URL and any material retrieval or publishing failure accurately. If a source fails, continue with other sources; if publishing fails, leave the last successful public edition intact. Never claim publication from a commit alone.
```

## Operational checks

A normal run should (1) read private feedback, (2) inspect the compact public editorial index, (3) search and inspect primary documents within the hard budget, (4) compose and validate one edition, (5) commit only that edition, and (6) check deployment. Inspect the first few runs in Scheduled. Successful scheduling alone does not prove these later steps work.

If authoring a task through a tool, use an exact daily schedule with `DTSTART;TZID=America/New_York` and `RRULE:FREQ=DAILY;BYHOUR=2;BYMINUTE=0;BYSECOND=0`. Verify both repositories through the connector first. Keep the scheduled prompt durable; never depend on this checkout or a transient local directory surviving.
