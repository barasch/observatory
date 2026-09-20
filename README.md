# Observatory

A public daily selection of primary sources, with private feedback for its owner.

- Site: https://barasch.github.io/observatory/
- [Method](METHOD.md)
- [One-time setup and exact scheduled-task instructions](docs/SCHEDULED_TASK.md)
- [Daily editorial procedure](docs/DAILY_EDITOR.md)
- [Public edition schema](docs/EDITION_FORMAT.md)

## Build and verify

Building needs Python 3.11+. Feedback and interface tests use Node 22+ and the locked development dependencies.

```sh
python scripts/build.py --check
python -m unittest discover -s tests -v
npm ci
npm test
```

`site/` is generated and ignored. GitHub Actions builds and deploys commits to main. A scheduled ChatGPT task supplies validated `data/editions/YYYY-MM-DD.json` files; GitHub Actions no longer runs a competing feed collector. `scripts/collect.py` and the historical data remain available for reference, but do not author current editions.

The scheduled task has a countable daily work ceiling rather than a fictional time-to-token conversion: one agent, at most 10 search queries and 60 total retrieval actions, no more than 22 primary documents or 30,000 retrieved source words, at most 15 published tiles, and one validation repair. The final report states the counts. ChatGPT Scheduled does not offer a mechanically enforced all-in token quota; that would require an API runner with usage metering.

Private feedback requires a separate private repository. Never commit `feedback.json`, `preferences.json`, or `profile.json` here. `feedback-auth.json`, created by the site setup form, contains only an encrypted credential and the private repository reference. A build allowlist validates this envelope before it can be served.

The site never stores the plaintext token, passphrase, votes, or explanations in browser storage. Reload or Lock clears the unlocked session. Saves verify private visibility and use optimistic concurrency to preserve changes from other devices; failed saves remain visibly unsaved.
