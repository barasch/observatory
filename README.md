# Observatory

Observatory is a public reading surface for recurring records and a private people-as-topics watchlist.

The deployed site is intended to live at <https://barasch.github.io/observatory/>.

## What the first version does

- Collects a deliberately narrow set of official statistical, regulatory, health, fiscal, and judicial sources.
- Labels each item by the kind of record it is instead of assigning a universal “reliability score.”
- Shows the newest 30 items in collapsible category sections, with people matches kept separate.
- Retains a rolling 30-day date archive.
- Republishes only source-supplied metadata: title, abbreviated description, publisher, date, and link.
- Provides local browser filters, visited-link differentiation, and browser-local “save for later” storage.
- Reads a private people registry from the encrypted `PEOPLE_WATCHLIST_JSON` repository secret.
- Searches configured direct feeds, OpenAlex author identifiers, and exact-name Google News RSS queries.
- Collects once daily at 5:17 a.m. in `America/New_York`; ordinary code pushes redeploy the existing daily edition without recollecting it.

## Repository map

```text
config/sources.json          public source catalog and adapters
config/people.example.json   non-secret watchlist schema example
data/                        retained public records and adapter status
scripts/collect.py           collection and deterministic match rules
scripts/build.py             static-site and archive generator
site_src/                    source CSS, JavaScript, typefaces, and marks
site/                        generated, git-ignored GitHub Pages artifact
METHOD.md                    public collection and classification method
WATCHLIST.md                 exact instructions for the private registry
DECISIONS.md                 provisional design decisions
```

The operational method is in [METHOD.md](METHOD.md), and the complete public source registry is in [config/sources.json](config/sources.json).

## Run locally

The pipeline uses only the Python standard library.

```bash
python scripts/collect.py
python scripts/build.py --check
python -m unittest discover -s tests -v
python -m http.server 8000 --directory site
```

To test a private registry locally, copy `config/people.example.json` to `.private/people.json`, replace the example, and run `python scripts/validate_people.py .private/people.json`. The `.private` directory is ignored by Git.

## Publishing

The workflow in `.github/workflows/update.yml` builds and deploys through GitHub Pages. If GitHub does not permit the workflow to enable Pages automatically, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**, then rerun the workflow.

## License and design

Code is available under the MIT License. The visual system uses the ET Book typeface and is strongly informed by the article typography, proportions, marginal notes, and restrained palette of [Tufte CSS](https://github.com/edwardtufte/tufte-css). The SB mark is the active site identifier; the earlier Observatory mark remains in `site_src/observatory-mark.svg` for possible later use.
