# godot-visitors

Traffic report for the Godot documentation, built from
[Plausible](https://plausible.io) analytics and rendered as a static page.

Its main question: **which documentation pages deserve the most care, and which
are candidates for trimming or merging?** Traffic alone cannot answer that, so
the report joins per-page traffic against the `godotengine/godot-docs` source
tree — page length, last edit, and which URLs correspond to a real page at all.

Like [godot-area-throughput](https://github.com/Ivorforce/godot-area-throughput),
this is decision support for maintainers, not a ranking. A rarely-read page can
be the one that unblocks a contributor; the report proposes candidates for human
review and nothing more.

## Status

v1 works end to end: fetch, inventory, aggregate, and a page that opens straight
from disk. Not published — see *Publishing clearance* below.

The headline finding so far is about the data rather than the docs. The manual
has **no dead pages**: of 1,591 English `/en/stable/` pages, all but one were
read at least once in July 2026, and 512 hand-written pages carry a million
pageviews. So "what can be trimmed" has no easy answer here, and the report is
built to show proportion — 26 pages carry half the reading — rather than to
produce a deletion list.

### Why this report only uses pageviews

Plausible identifies a returning reader by hashing their IP address together
with the user agent. On Godot's instance (`plausible.godot.foundation`,
self-hosted) that identity stopped being distinguishing on 2025-10-01: daily
unique visitors fell from ~38,000 to ~2,100 overnight while pageviews did not
move, and the ratio has stayed there since.

| date | pageviews | visitors | pageviews / visitor |
|---|---|---|---|
| 2025-09-29 | 133,417 | 39,131 | 3.4 |
| 2025-09-30 | 138,333 | 26,086 | 5.3 |
| 2025-10-01 | 132,584 | 2,690 | 49.3 |
| 2025-10-02 | 130,549 | 2,470 | 52.9 |

The most likely explanation is an infrastructure change on that date — a CDN or
reverse proxy in front of the instance, after which Plausible hashes an upstream
address shared by many readers rather than each reader's own. That is consistent
with what else the data shows: country, browser, OS and device still resolve for
~100% of traffic, so the events are intact and only the per-reader identity is
degraded. Confirming it needs server-side access and is not visible through the
Stats API, so this repository treats it as a property of the data source, not as
something to diagnose.

Consequences, and they are not severe for this report's question:

- **Pageviews are unaffected** and are the only traffic metric used anywhere.
- Anything derived from identity or sessions — unique visitors, visits, bounce
  rate, session duration — is not used. Geolocation is intact but not used
  either; this report has no geographic question.
- This Plausible version offers no `scroll_depth` and no `time_on_page`, so there
  is no per-pageview engagement metric to fall back on. Page-quality signals come
  from the docs source tree (length, staleness) instead.

None of this appears on the page itself. A reader looking at documentation
traffic has no way to know these metrics were ever an option, and a prominent
notice about a metric that is absent would mislead more than it informs. The
page states plainly that everything on it is pageviews and that no engagement
signal exists; the reasoning lives here.

If you run the instance, `scripts/fetch.py` stores daily visitor counts in
`data/raw/site-daily.ndjson` — unused by the report, kept so the table above
stays checkable.

### Publishing clearance

This draws on Godot's own analytics rather than public data. Nothing is
published, and no analytics data is committed, before the people who run the
Plausible instance have agreed to it. That conversation is also the moment to
raise the 2025-10-01 regression, which affects every visitor number on the
dashboard, not just this report.

Until then, `data/raw/`, `data/inventory.json` and `docs/data.js` are
**gitignored**: the repository holds the code that produces the report, not the
report's data. Running the pipeline yourself needs an API key. Clearing this is
one `.gitignore` change plus a first data commit — no history rewrite, because
nothing sensitive ever entered history.

## How it works

- `scripts/plausible.py` — Stats API v2 client. Godot self-hosts at
  `plausible.godot.foundation`, and both `godotengine.org` and
  `docs.godotengine.org` report to one Plausible site with
  `data-domain="godotengine.org"`, so they share a path namespace and are split
  with an `event:hostname` filter. `Client.capabilities()` re-probes which
  metrics and dimensions exist, so a server upgrade that adds `scroll_depth`
  gets noticed rather than silently changing results.
- `scripts/fetch.py` — writes `data/raw/`, one byte-stable ndjson file per kind
  and month. The docs site serves ~87k distinct URLs a month across 15 languages
  and a dozen versions; keeping that per-page for every month would be ~85 MB of
  mostly noise. So per-page detail is English-only, and other languages are
  rolled up per path — enough to show that a page carries weight in translation
  without shipping fifteen parallel tables. Only the newest month pages the full
  hostname (nine requests); older months fetch the English subset in one.
- `scripts/inventory.py` — clones `godot-docs` at the `stable` branch (which is
  what Read the Docs serves at `/en/stable/`, not `master`) and records each
  page's word count, last-edit timestamp and toctree position. Blob-filtered and
  sparse, because the repository is ~450 MB and almost all of it is screenshots;
  history is kept in full, since last-edit dates are the entire point.
- `scripts/aggregate.py` — joins the two into `docs/data.js`, a single
  `window.DATA = {…}` assignment.
- `docs/` — the page. Hand-written `index.html`, vanilla `app.js`, and a
  vendored [Chart.js](https://github.com/chartjs/Chart.js) 4.4.9 (MIT).

`docs/data.js` is a `<script src>` rather than a fetched JSON file specifically
so the page opens from `file://` by double-clicking it. `fetch()` is blocked on
`file://`; a classic script tag is not.

## Metric definitions

- **pageviews** — the only traffic metric used anywhere. Visitors, visits, bounce
  rate and session duration are excluded because they broke on 2025-10-01.
- **page** — a URL that resolves to a `.rst` file in godot-docs at the recorded
  commit. Directory URLs and `index.html` are folded onto one canonical form, or
  a page's traffic would split across two rows and each would look half as read.
  URLs with no source file (~2% of pageviews: moved paths, Sphinx's search page,
  scanner noise) are excluded from every figure and listed separately, so the
  exclusion is visible rather than silent.
- **translation reads** — pageviews for the same path summed across all
  non-English languages and all doc versions, so a page's weight in translation
  shows up next to its English figure.
- **words** — approximate prose words in the source file. A token counts if it
  contains a letter (`str.isalpha()`, so non-Latin text counts too); table
  pipes, bullet markers, rule lines and bare numbers do not. Code is excluded
  both as directive bodies (`code-block`, `code-tab`) and as literal blocks
  opened by a trailing `::`; admonition bodies (`note`, `warning`, `tip`,
  `seealso`, …) *are* counted, being ordinary prose that merely renders as a
  callout — they are about a tenth of the manual. Roles and inline literals are
  unwrapped to their text.

  It is a size signal for comparing pages, not a typographic measurement. The
  earlier version counted raw whitespace tokens and skipped every directive
  body, which cut both ways: `tutorials/i18n/locales.rst` measured 2.3× its real
  length because a locale table's pipes counted as words, while
  `tutorials/xr/xr_action_map.rst` measured 39% short because most of its prose
  sits in notes. Neither error was visible in the total — the manual moved only
  +6% overall — but both distorted the pages they hit, which is what the scatter
  and the footprint bars actually read.
- **footprint** — how much of the manual a section occupies, measured in words by
  default. Words approximate what a section costs to keep correct, since review,
  updating and translation all scale with prose rather than with file count.
  Page count is offered as a toggle because it answers a different question —
  how many separate documents a reader moves between — and the two genuinely
  disagree. Inside `tutorials/`, `shaders/` is level with its readership by page
  count (6.4% of pages against 6.3% of reading) but well above it by words
  (9.1%), while `navigation/` is the reverse: 4.8% of pages, 2.7% of words, being
  many short documents rather than a lot of prose. Pages in the manual range from
  under 150 words to over 2,000, so the choice is not cosmetic.
- **last edit** — the most recent commit touching the source file, from a single
  `git log` walk. For the class reference these are bulk sync commits, not
  authorship, which is one reason it is excluded by default.

## Runbook

Python 3 stdlib and `git`; no other dependencies. Copy `.env.example` to `.env`
and add a Plausible API key with read access to the site — only `fetch.py` uses
it.

```sh
python3 scripts/fetch.py probe          # what this instance supports
python3 scripts/fetch.py backfill       # ~13 months, about a minute
python3 scripts/inventory.py            # clones godot-docs, ~2 s after first run
python3 scripts/aggregate.py            # writes docs/data.js
open docs/index.html                    # no server needed
```

Afterwards, `python3 scripts/fetch.py incremental` fetches only months not
already stored, always refreshing the newest.

Tests: `python3 -m unittest discover tests`. They cover URL canonicalisation,
download-link parsing, the rst parsers, and the invariants the page depends on —
including a check that no visitor-derived number leaks into page data, and one
that the 2025-10-01 collapse is still visible in the stored data, so the notice
on the page cannot quietly become a lie. Where the CSV export is present they
also cross-check page totals against it, since it was produced independently of
the API path.

Licensed [MIT](LICENSE).
