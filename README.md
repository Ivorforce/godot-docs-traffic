# godot-docs-traffic

Traffic report for the Godot documentation, built from Godot's own
[Plausible](https://plausible.io) instance and rendered as a static page. The
numbers come from the project's tracker directly, and are published here with
the clearance of the Godot Foundation.

**[Read the report →](https://ivorforce.github.io/godot-docs-traffic/)**

Its main question: **which documentation pages deserve the most care, and which
are candidates for trimming or merging?** Traffic alone cannot answer that, so
the report joins per-page traffic against the `godotengine/godot-docs` source
tree — page length, last edit, and which URLs correspond to a real page at all.

Like [godot-area-throughput](https://github.com/Ivorforce/godot-area-throughput),
this is decision support for maintainers, not a ranking. A rarely-read page can
be the one that unblocks a contributor; the report proposes candidates for human
review and nothing more.

## What it found

The headline finding so far is about the data rather than the docs. The manual
has **no dead pages**: all 1,590 English `/en/stable/` pages were read at least
once in July 2026, and 511 hand-written pages carry a million pageviews. So "what
can be trimmed" has no easy answer here, and the report is built to show
proportion — 26 pages carry half the pageviews — rather than to produce a deletion
list.

**Everything here is pageviews.** Plausible's per-reader identity stopped being
distinguishing on this instance on 2025-10-01, and the version running offers no
scroll depth or time on page, so unique visitors, bounce rate and every other
identity- or session-derived figure are used nowhere — and nothing in the report
measures whether a page was *read*, only how often it was opened. The evidence is
in [METHOD.md](METHOD.md#why-this-report-only-uses-pageviews).

## The repository

| | |
|---|---|
| `scripts/plausible.py` | Stats API v2 client; re-probes which metrics and dimensions the instance supports |
| `scripts/fetch.py` | Writes `data/raw/`, one byte-stable ndjson file per kind and month. The only script that needs an API key |
| `scripts/inventory.py` | Clones `godot-docs` at `stable` and records per page: word count, last edit, two-year line churn, toctree position, removals and redirects |
| `scripts/l10n.py` | **Not part of the pipeline.** Records word-weighted translation coverage per page and language; see [NOTES.md](NOTES.md) |
| `scripts/aggregate.py` | Joins traffic and inventory into `docs/data.js`, and runs two integrity checks |
| `docs/` | The page: hand-written `index.html`, vanilla `app.js`, vendored [Chart.js](https://github.com/chartjs/Chart.js) 4.4.9 (MIT) |

Each script's module docstring carries its own design rationale — what is stored
and what is not, and why.

`docs/data.js` is a `<script src>` rather than a fetched JSON file specifically
so the page opens from `file://` by double-clicking it. `fetch()` is blocked on
`file://`; a classic script tag is not.

## Runbook

Python 3 stdlib and `git`; no other dependencies. Copy `.env.example` to `.env`
and add a Plausible API key with read access to the site — only `fetch.py` uses
it.

```sh
python3 scripts/fetch.py probe          # what this instance supports
python3 scripts/fetch.py backfill       # ~13 months, about a minute
python3 scripts/inventory.py            # clones godot-docs (~740 MB), ~7 s after
python3 scripts/aggregate.py            # writes docs/data.js
python3 scripts/aggregate.py --check    # what the checks found; writes nothing
open docs/index.html                    # no server needed
```

Afterwards, `python3 scripts/fetch.py incremental` fetches only months not
already stored, always refreshing the newest. That is what
`.github/workflows/update.yml` runs on the 2nd of each month, when last month is
the newest complete one, committing `data/` and `docs/data.js` back to `main`.
It needs `PLAUSIBLE_API_KEY` as a repository secret.

## Further reading

- **[METHOD.md](METHOD.md)** — what each figure counts and why it was chosen that
  way: the metric definitions, the churn grid, the editions chart, and the two
  checks that run on every build.
- **[NOTES.md](NOTES.md)** — four views built far enough to check and then cut,
  and what they turned up. Including two findings about translation progress that
  outlived the view they came from.
- **[DEVELOPING.md](DEVELOPING.md)** — tests, and how to verify the page itself.

Licensed [MIT](LICENSE).
