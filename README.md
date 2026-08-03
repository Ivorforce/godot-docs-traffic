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
  page's word count, last-edit timestamp and toctree position, plus the date
  every *removed* page left the tree and the redirects that forward the URLs
  they left behind. Blob-filtered and
  sparse (`*.rst` plus `_static/redirects.csv`), because the repository is
  ~450 MB and almost all of it is screenshots; history is kept in full, since
  last-edit dates are the entire point. The sparse spec is reapplied on every
  run, so a cache built by an older revision picks up newly needed files rather
  than silently missing them.
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
  URLs with no source file (~2% of pageviews: removed pages, Sphinx's search
  page, scanner noise) are excluded from every figure and listed separately, so
  the exclusion is visible rather than silent.
- **removed** — the commit that deleted a page's source file, from a second
  `git log --diff-filter=D` walk. A page removed *before* the traffic window
  was gone for all of it, so every view is a reader hitting a dead end; one
  removed *during* the window was readable for part of it, and its views are
  partly ordinary reading.
- **forwarded** — whether `_static/redirects.csv` sends the URL somewhere.
  Read the Docs caps its own redirects at ~50 and godot-docs needs hundreds, so
  the overflow lives in that file and is applied by `404.rst` **in the reader's
  browser**. That is what makes it measurable here: an RTD redirect answers with
  a 301 and the old URL never renders, recording nothing, while a redirect from
  the CSV runs *on* the 404 page, after the analytics script has already fired
  against the old path. Every forwarded URL people still reach therefore appears
  in the traffic data — and read naively, looks exactly like a dead link.

  Joining the file is what makes the table actionable rather than merely
  descriptive. For July 2026, of 26,854 pageviews to URLs with no source file:
  13,793 (51%) were forwarded and the reader arrived where they meant to, 9,200
  went to URLs that were never pages here (Sphinx's search page, downloadable
  examples, `null` paths, scanner noise), and **3,861 across 307 URLs** were
  removed pages with nothing forwarding them. Only that last group is worth
  anyone's time, and it is a third of what the unjoined list implied. The
  inference holds in one direction only, which is enough: since a server-side
  redirect records no pageview, a URL that reaches this report is not covered by
  one, so absence from the CSV means genuinely unforwarded rather than possibly
  handled elsewhere.
- **translation reads** — pageviews for the same path summed across all
  non-English languages and all doc versions, so a page's weight in translation
  shows up next to its English figure. 39% of all docs reading is non-English.

  The table shows the count; the scatter colours by the **share** — translated
  reading as a multiple of the same page's English figure — because the raw
  count correlates with English reading at r = 0.92 on log-log and as a colour
  would only redraw the horizontal axis. The share does vary, and structurally:
  `getting_started/introduction` runs at 0.75× and `tutorials/editor` at 0.83×,
  against 0.45× for `tutorials/scripting` and 0.30× for `tutorials/inputs`.
  People start in their own language and move to English as they go deeper. Two
  readings are actionable: a share near zero flags a page the translations have
  not reached (the whole of `community/asset_store/` sits at 0.00–0.03×, being
  new), and a high share flags a page where an English rewrite invalidates work
  in fifteen languages.

  Translation paths are keyed on the remainder after `/<lang>/<version>/`, which
  is *empty* for the manual's landing page — the largest single key in the file
  at 34,842 views. Folding it onto `index.html` is what `load_i18n` does beyond
  the obvious directory rule, and `TestI18nKeys` pins it.
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
- **change on the month** — a page's pageviews against the month before. The
  table gives it raw; the scatter colours by it *with the whole docs' change
  divided out*, since in a month when the manual rose 5.7% overall a page at
  +5% held its place rather than grew. The reference is every page in scope, so
  drilling into a section does not quietly change what a colour means. Two
  months is a short baseline: pages under a few hundred views a month move 15%
  on noise, which the page says next to the chart.
- **last edit** — the most recent commit touching the source file, from a single
  `git log` walk. For the class reference these are bulk sync commits, not
  authorship, which is one reason it is excluded by default.

  It is a table column only. It was a scatter colouring until the commits behind
  it were checked: of the 130 manual pages last edited in the three months to
  `bca391ae6`, the median was last touched by a commit changing under five lines
  and 30% by one changing two or fewer — `fix typos in gettext i18n tutorial`,
  `fixed Visual Studio being typo'd as Visaul Studio`, a six-file sweep replacing
  asset-library links two lines at a time. "Recently edited" therefore means
  someone corrected a typo, not that the page was reviewed, and an old date is
  equally mute, since a page can go untouched because it is still correct. A
  scale that cannot be read in either direction was worth more as space than as
  colour.

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
including a check that no identity-derived number reaches the page, and one that
per-visitor figures are still implausible, so if the instance is ever fixed the
suite says so instead of the constraint quietly outliving its reason. Where the
CSV export is present they also cross-check page totals against it, since it was
produced independently of the API path.

### Verifying the page

**The tests do not cover `docs/app.js`.** Everything above runs against the
Python payload, and the payload has been right every time the page was wrong.
The chart bug that made the section chart's reading bars sum to 89.6% instead of
100% lived entirely in JavaScript grouping logic and no test could see it.

Headless Chrome renders `file://` directly, which is enough to check both that
the page runs and what it computed:

```sh
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --virtual-time-budget=8000 \
  --dump-dom "file://$PWD/docs/index.html#s=tutorials&f=pages" > /tmp/dom.html
```

The dumped DOM carries everything `app.js` wrote into the document — the stat
tiles, both tables, the generated captions — so those can be asserted directly.
Chart contents live in canvas and need one extra step: copy `index.html`, append
a script that reads `Chart.getChart("chart-sections").data` into a `<pre>`, dump
that instead, and delete the copy. That is how the 89.6% was found, and it is
worth repeating after any change to the charts.

Two cautions. Chrome's stderr carries `ERROR:gpu/...` lines in headless mode that
have nothing to do with the page, so grepping stderr for "error" gives false
positives. And a checkbox set from JavaScript does not serialise its `checked`
attribute into the dumped DOM — assert on the data the toggle changes, not on
the control.

Not yet automated. A `scripts/check_page.py` wrapping the above and asserting a
few invariants (section shares summing to 100% across several hash states) would
close the gap; it would need Chrome present, so it would sit outside the unit
suite.

Licensed [MIT](LICENSE).
