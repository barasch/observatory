# Weekly scheduled task

Keep the existing task paused until pilot review and explicit activation. Target schedule: Sundays at 02:00 America/New_York. The spring-transition 03:00 fallback requires verified scheduler support before activation. Model and reasoning selection remain user-interface settings; no exact total-token limit is asserted.

## Instructions

```text
Prepare and, only when activation is recorded in private editorial/state.json, publish the next weekly Observatory edition at https://barasch.github.io/observatory/.

Use the connected GitHub app. Read docs/WEEKLY_EDITOR.md, docs/WEEKLY_OPERATIONS.md and docs/EDITION_FORMAT.md on barasch/observatory main. They define the canonical procedure. Verify barasch/observatory-private is private before reading feedback.json, preferences.json, optional profile.json, discovery/registry.json and editorial/state.json. Stop if privacy or access fails. Check public feedback-auth.json matches the private repository and main branch without decrypting it. Never publish private state or personalized rationales.

Acquire the private conditional per-window lease, reconcile any interrupted run, and use the previous successfully published coverage end through the current Sunday midnight in New York. Preserve the window and cumulative counters across retries. Detect existing editions and commits before writing. Keep the first-release rule and substantive revisions of upvoted originals.

Use one editorial agent, no subagents or deep research. Collect compact metadata through the registry and independent searches, at most 1,000 records and 50 screening words each. Screen without opening documents; assess at most 50 works through bounded passages; verify at most 25 finalists or reserves and select 15–20 tiles. Do not penalize unsurprising results. Give innovation a slight advantage and evaluate evidence and consequence concretely, without publisher quotas or authority proxies.

Mandatory retrieval ceilings: 40 registry; 32 search queries; 16 extra metadata; 100 assessment; 60 final verification; 20 reserved checks, total 268. Charge failed actions, each query in a batch, helper subrequests and visible redirects. Reserve actions before execution using scripts/weekly.py. Assessment text at most 600 words per work and 30,000 total; final reading at most 1,500 additional words per work and 30,000 total. Never shift budgets between stages. Actual source text exposed counts. Save private checkpoints after each stage. No time-to-token estimates and no asserted hard total-token guarantee.

Use current reversible feedback only, ignoring dismissals, restores, clicks and silence. Add narrow originating streams after upvotes with private provenance and validated routes; independent discovery may add provisional streams. Keep exploration possible. Preserve feedback.json and preferences.json unchanged.

Produce schema-2 edition data, validate once with at most one repair, semantically check privacy, and commit only the new validated data/editions/YYYY-MM-DD.json. Pilot or activation=false means save privately and never publish. Technical shortfalls hold the draft. For live publication, verify exact-commit deployment and the live window and item identifiers, reporting committed, deployed and live-verified states accurately. Retain the previous edition on failure. Finish with counts and material failures, without private selection explanations.
```
