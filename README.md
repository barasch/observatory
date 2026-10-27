# Observatory

A public weekly selection of primary sources, with private feedback for its owner.

- Site: https://barasch.github.io/observatory/
- [Method](METHOD.md)
- [Scheduled-task instructions](docs/SCHEDULED_TASK.md)
- [Weekly editorial procedure](docs/WEEKLY_EDITOR.md)
- [Weekly operations and helper contracts](docs/WEEKLY_OPERATIONS.md)
- [Public edition schema](docs/EDITION_FORMAT.md)
- [Media-attention method contract](docs/MEDIA_NETWORKS.md)
- [Media-attention data contract](docs/NETWORK_FORMAT.md)
- [Media-attention Stage 2 collection](docs/MEDIA_COLLECTION.md)

## Build and verify

Building needs Python 3.11+. Feedback and interface tests use Node 22+ and the locked development dependencies.

```sh
python scripts/build.py --check
python scripts/media_validate.py --registry config/media-observers.json config/media-panel-candidates.json
python -m unittest discover -s tests -v
npm ci
npm test
```

`site/` is generated and ignored. GitHub Actions builds and deploys commits to main. A scheduled ChatGPT task supplies validated `data/editions/YYYY-MM-DD.json` files; GitHub Actions no longer runs a competing feed collector. `scripts/collect.py` and the historical data remain available for reference, but do not author current editions.

The weekly task uses four separately metered stages. It can collect up to 1,000 compact records, screen metadata for all of them, assess at most 50 works through bounded passages, verify at most 25, and publish 15–20 tiles. Retrieval allowances total 268 actions across registry, search, metadata, assessment, verification, and publication checks; failures and redirects count. Source text is capped separately for assessment and verification. The final report states the actual counts and coverage failures. These controls do not claim to meter hidden model tokens, which ChatGPT Scheduled does not expose.

Private feedback requires a separate private repository. Never commit `feedback.json`, `preferences.json`, or `profile.json` here. `feedback-auth.json`, created by the site setup form, contains only an encrypted credential and the private repository reference. A build allowlist validates this envelope before it can be served.

The site never stores the plaintext token, passphrase, votes, or explanations in browser storage. Reload or Lock clears the unlocked session. Saves verify private visibility and use optimistic concurrency to preserve changes from other devices; failed saves remain visibly unsaved.
