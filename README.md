# godot-docs-traffic

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
has **no dead pages**: all 1,590 English `/en/stable/` pages were read at least
once in July 2026, and 511 hand-written pages carry a million pageviews. So "what
can be trimmed" has no easy answer here, and the report is built to show
proportion — 26 pages carry half the reading — rather than to produce a deletion
list.

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

The likely cause is an infrastructure change on that date — a CDN or reverse
proxy in front of the instance, after which Plausible hashes an upstream address
shared by many readers. Country, browser, OS and device still resolve for ~100%
of traffic, so the events are intact and only the per-reader identity is
degraded. Confirming it needs server-side access, so this repository treats it as
a property of the data source.

Consequently: **pageviews are the only traffic metric used anywhere.** Anything
derived from identity or sessions — unique visitors, visits, bounce rate, session
duration — is unused. This Plausible version offers no `scroll_depth` and no
`time_on_page` either, so page-quality signals come from the source tree (length,
staleness) instead of from engagement. `scripts/fetch.py` still stores daily
visitor counts in `data/raw/site-daily.ndjson`, unused, so the table above stays
checkable.

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

- `scripts/plausible.py` — Stats API v2 client. Both `godotengine.org` and
  `docs.godotengine.org` report to one Plausible site, so they share a path
  namespace and are split with an `event:hostname` filter.
  `Client.capabilities()` re-probes which metrics and dimensions exist, so a
  server upgrade that adds `scroll_depth` gets noticed rather than silently
  changing results.
- `scripts/fetch.py` — writes `data/raw/`, one byte-stable ndjson file per kind
  and month. The docs site serves ~87k distinct URLs a month across 46 language
  codes and a dozen versions, so per-page detail for all of them would be ~85 MB
  of mostly noise: it is kept for English only, and other languages are rolled up
  per path for the newest month alone, at 519 KB. Two counts per path — the
  current docs, which shares divide by, and every version. `--by-language` keeps
  the language rather than summing it away, at 2.2 MB and no extra requests; it
  is off because nothing reads it. Three kinds are fetched but unread — `www`,
  `outbound` and `os` — because they belong to a *visitor* report rather than a
  docs one and cost three queries a month, so that analysis one day starts with
  history already stored.
- `scripts/inventory.py` — clones `godot-docs` at the `stable` branch (which is
  what Read the Docs serves at `/en/stable/`, not `master`) and records each
  page's word count, last-edit timestamp, two-year line churn and toctree
  position, plus the date every *removed* page left the tree and the redirects
  forwarding the URLs they left behind. Sparse (`*.rst` plus
  `_static/redirects.csv`) but **not** blob-filtered: `git log --numstat` needs
  both sides of every diff, and on a filtered clone it fetches blobs one request
  at a time, taking minutes over a year and failing outright over two. Unfiltered
  the same walk takes seconds, at the cost of a ~740 MB clone. `.cache/` is
  gitignored.
- `scripts/l10n.py` — **not part of the pipeline**; nothing runs it and nothing
  reads its output. It clones `godot-docs-l10n` and records, per page and
  language, how many of the page's source words are translated, into
  `data/l10n.json` (94 KB, ~120 MB clone). Coverage is word-weighted, and the
  denominator must be `sphinx/templates/<page>.pot`: a language's own `.po` holds
  only what someone has already translated, so reading that alone reports every
  page as complete.
- `scripts/aggregate.py` — joins raw traffic and the inventory into
  `docs/data.js`, a single `window.DATA = {…}` assignment.
- `docs/` — the page. Hand-written `index.html`, vanilla `app.js`, and a
  vendored [Chart.js](https://github.com/chartjs/Chart.js) 4.4.9 (MIT).

`docs/data.js` is a `<script src>` rather than a fetched JSON file specifically
so the page opens from `file://` by double-clicking it. `fetch()` is blocked on
`file://`; a classic script tag is not.

## Metric definitions

- **pageviews** — the only traffic metric used anywhere.
- **page** — a URL that resolves to a `.rst` file in godot-docs at the recorded
  commit. Directory URLs and `index.html` are folded onto one canonical form, or
  a page's traffic would split across two rows and each would look half as read.
  URLs with no source file (~2% of pageviews: removed pages, Sphinx's search
  page, scanner noise) are excluded from every figure and listed separately, so
  the exclusion is visible rather than silent. `404.rst` is dropped too, being the
  "page not found" template: Read the Docs serves it *in place of* a missing URL
  while the pageview records the URL the reader asked for, so its reading is
  already counted under **forwarded**.
- **corpus** — which body of pages every figure down to the page-by-page table
  is about, chosen by the path bar's first entries: the hand-written manual by
  default, the manual with the class reference counted in, or the class
  reference on its own.

  The class reference is 1,079 generated pages against 511 hand-written ones, so
  whether it counts decides the shape of every page-count comparison. Alone it is
  a corpus rather than a section: those pages sync in bulk from the engine's
  source, and asked as a group they answer a different question — which classes
  people read, and which the engine keeps moving. `inventory.py` names the
  generated directories, so the root the report jumps to comes from the pages
  rather than being written into the page. It is also why the section chart and
  the churn grid cap their rows and say underneath what that left out: a flat
  level of 1,079 is a smear a pixel high apiece. Both bars stay shares of the
  whole level, so what is drawn no longer sums to 100%.
- **removed** — the commit that deleted a page's source file, from a second
  `git log --diff-filter=D` walk. A page removed *before* the traffic window was
  gone for all of it, so every view is a reader hitting a dead end; one removed
  *during* the window was readable for part of it.
- **forwarded** — whether `_static/redirects.csv` sends the URL somewhere. Read
  the Docs caps its own redirects at ~50 and godot-docs needs hundreds, so the
  overflow lives in that file and is applied by `404.rst` **in the reader's
  browser**. That is what makes it measurable: an RTD redirect answers with a 301
  and the old URL never renders, recording nothing, while a redirect from the CSV
  runs *on* the 404 page, after the analytics script has fired against the old
  path. Every forwarded URL people still reach therefore appears in the traffic
  data — and read naively, looks exactly like a dead link.

  Joining the file is what makes the removed-pages table actionable. Pageviews to
  URLs with no source file split three ways: about half are forwarded by the CSV,
  most of the rest went to URLs that were never pages here, and a small remainder
  are **removed pages with nothing forwarding them**. Only that last group is
  worth anyone's time; `python3 scripts/aggregate.py --check` prints the current
  split. The inference holds in one direction only, which is enough: since a
  server-side redirect records no pageview, a URL that reaches this report is not
  covered by one.
- **translation reads** — pageviews for the same path summed across all
  non-English languages. 35% of reading on the current docs is non-English. The
  scatter names the count on hover; it is nowhere else (see *What was tried and
  cut*).

  Both sides must count the current docs only, and the translations have neither
  `stable` nor `latest` — their current edition is `4.x` — so `fetch.py` decides
  by exclusion, a pinned release being spelled as a number. It matters by a
  sixth: 130,121 of 851,931 translated pageviews are on pinned releases. Paths
  are keyed on the remainder after `/<lang>/<version>/`, which is *empty* for the
  manual's landing page, the largest single key in the file; `load_i18n` folds it
  onto `index.html` and `TestI18nKeys` pins that. 97% of translated pageviews
  join to a page.
- **words** — approximate prose words in the source file. A token counts if it
  contains a letter (`str.isalpha()`, so non-Latin text counts too); table pipes,
  bullet markers, rule lines and bare numbers do not. Code is excluded both as
  directive bodies (`code-block`, `code-tab`) and as literal blocks opened by a
  trailing `::`; admonition bodies (`note`, `warning`, `tip`, `seealso`, …) *are*
  counted, being ordinary prose that merely renders as a callout — they are about
  a tenth of the manual. Roles and inline literals are unwrapped to their text. It
  is a size signal for comparing pages, not a typographic measurement.
- **footprint** — how much of the manual a section occupies, in words by default,
  words being roughly what a section costs to keep correct. Page count is offered
  as a toggle because it answers a different question, and the two genuinely
  disagree: inside `tutorials/`, `shaders/` is level with its readership by page
  count (6.4% against 6.3% of reading) but well above it by words (9.1%), while
  `navigation/` is the reverse at 4.8% of pages and 2.7% of words.
- **last edit** — the most recent commit touching the source file. Shown on the
  scatter's hover only, as context for a page already chosen (see *What was tried
  and cut*).
- **lines changed** — source lines added plus deleted over the two years ending
  at the recorded docs commit, from `git log --numstat -M`. The table shows the
  total with the **net** as a bar drawn from the point where added and deleted
  balance: a page whose net is close to its total was written and runs the bar
  out to one end, one whose net is near zero was rewritten at its existing
  length. Per section the same split separates growth from rework —
  `tutorials/` added 13,473 lines against 3,623 deleted while `getting_started/`
  added 225 against 222.

  Lines rather than commits, because a commit count cannot tell a typo from a
  rewrite: over this window the most-committed hand-written page took 13 commits
  while the largest single change was 1,822 lines in three of them, and the
  edit-count distribution has 161 pages tied at one.

  Renames are followed twice: `-M` within a commit, and a second
  `--diff-filter=R --name-status` walk that maps all 846 of them across the
  history so a page that moved keeps one history rather than two halves. Without
  it a year of churn strands 1,094 lines on dead paths.
  `_static/redirects.csv` cannot do this job — it maps URLs rather than source
  paths, and where a page was deleted rather than moved there is no live page to
  join to.

  **Two years is the window**, set by what anyone can act on. Four years spans
  roughly eight minor releases, doubles the columns, and — where a level has more
  children than fit — fills half the list with pages last worked on in 2023.
- **turnover** — lines changed against the page's current length. It shades the
  churn grid and colours the scatter, and it earns the colour by not repeating
  the axes: across the manual turnover measures −0.09 against log pageviews and
  0.01 against log words, so position says what a page costs and returns while
  colour says whether anyone is already working on it. In the
  long-and-seldom-read corner that splits 103 pages into 17 barely touched and 41
  lightly worked against 24 reworked and 21 rewritten outright, where position
  alone would have handed over all 103.

  The bins are 0.05, 0.3 and 1×: the first is the lower quartile rather than zero
  exactly, because whether a quiet page caught one passing typo fix is an
  accident of who walked past it, and the last is nameable rather than fitted, a
  page changed by its whole length again having been rewritten. The ratio has no
  ceiling and short pages swing hardest — a 22-line stub took 175 lines of work
  and reads as 7.95×.

### The churn grid

Rows are the children of the level the path bar is on, capped at the 25
most-edited with the remainder printed underneath — a level can have a thousand
children, and inside the class reference 612 pages changed while 467 of 1,079 did
not. Drilling in is the whole point: `tutorials/xr/` took 1,823 lines in one
quarter and `tutorials/navigation/` has been silent since July with 15 of its 19
pages untouched, and neither is visible from the top level.

Columns are **calendar quarters**, eight of them, ending with the one the docs
commit falls in. Calendar-pinned rather than rolled back from the newest month,
because rolling boundaries move under the reader and no two runs of the report
could then be compared. A closed quarter never changes again. The price is that
the newest column stops at the docs commit rather than at its own end; it is
marked `*`, because read as a full column it looks like work falling off. Months
were the first cut and were too fine even over one year: a third of the cells
were empty and a single piece of work spread across two or three of them.

**Shading is churn against the row's current length, on a square root, scaled to
the darkest cell shown.** Against length, because shading raw counts made a table
of size: in 2023 H1 `getting_started/` changed 3,531 lines to `tutorials/`'s
24,229 and was the harder worked of the two, 45% of its own length against 23%.
Divided by the length *today*, so one divisor per row leaves the row's profile
untouched. Scaled to what is on screen, because a section almost never changes by
its whole length in half a year and a fixed 1× anchor spent three quarters of the
palette on nothing; the cost is that colours redraw when you change section while
the figures do not. Square root, because the darkest cell is a median of 12.9×
the median cell across all 63 navigable levels — linear leaves the bulk at a
tenth of the range, and log darkens the quiet cells until absence stops reading,
which is what the grid is for.

The grid measures activity, not quality and not need. A page nobody has touched
reads identically whether it is finished or forgotten. Its window also ends at
the docs commit rather than at the end of the traffic window, and reaches back
two years where traffic reaches back thirteen months, so the older half of the
grid has no traffic figure beside it at all.

## What was tried and cut

Four things were built far enough to check and then removed. The checks are the
useful part, so they are kept here.

**A translator's view** — for each language, which pages are most read against
how much of them is still untranslated. Abandoned at the point of drawing it,
because **translators are already working in demand order**: across the
mid-coverage languages the correlation between a page's reading and its coverage
runs 0.44 to 0.60 (ja 0.60, es 0.50, fr 0.45, pt-br 0.44), and their top decile by
demand is 75–85% translated against 30–44% overall. The recommendation would also
have been obvious — 13 of Japanese's top 20 by reading per remaining word are
`getting_started/` — and the ranking degenerates exactly where coverage is lowest:
for Polish, at 5% translated, the top entries are two-word index stubs with five
readers, because a small denominator wins a ratio. `scripts/l10n.py` and
`fetch.py --by-language` are kept dormant; reproduce by joining the two.

Two findings from it survive:

- **Counting strings overstates progress, worst where the work is.** A string
  count is what `msgfmt --statistics` reports; a translator spends words. Short
  strings get translated first, so the two diverge systematically and in the same
  direction — `tutorials/2d/index` reads 67% done by string and 9% by words,
  because its four translated strings are `2D`, `Rendering`, `Physics and
  movement` and `Tools` while the two left carry 62 of the page's 68 words. It
  held on every page and language checked and converges only once a page is
  finished. Weblate tracks word statistics too, so this is a caution about how a
  percentage is read rather than a defect in anyone's tooling.
- **A sixth of the remaining work is on pages nobody reads in that language.**
  Between 6% and 27% of each language's untranslated words sit on pages with zero
  readers in it over the month — 27% for Italian, 15% Polish, 13% German. A stop
  signal rather than a start signal, and invisible in Weblate.

**Translation share** — translated reading as a multiple of the same page's
English figure. It was a table column until what it ranks pages *by* was checked
against the coverage in `data/l10n.json`: **the share measures 0.067 against
it.** So a low share does not flag a page the translations have not reached — the
ten lowest sit at 59–79% translated, at or above the manual's 62% median — and a
high share does not flag a page where an English rewrite would invalidate work in
fifteen languages. What its extremes measure is small denominators: the twenty
highest-share pages have a median of 73 pageviews against 609 across the table.

Coverage is the measure those readings wanted, and it is real: it spreads 39% to
93% and correlates 0.46 with traffic. It is not in `docs/data.js`, since adding
it would make the ~120 MB l10n clone a required input for every build.

**Change on the month** — a page's pageviews against the month before, computed
by `aggregate.py` and shown nowhere. Two months is a short baseline: pages under
a few hundred views a month move 15% on noise, and 84% of pages sit within ±20%
of the docs' own move. Sorting by it returned the smallest pages — of the 30
largest risers, 23 drew under 250 pageviews the month before, against a median of
566 across the table. It was uncorrelated with every column it sat beside at |ρ|
≤ 0.14, so it was cut for saying too little rather than for repeating a
neighbour. `PREV` stays in the row layout, unread; a trend is the version worth
having, and it wants the other eleven months in `data/raw`.

**Last edit as a scale** — a date sorted or coloured by. Of the 130 manual pages
last edited in the three months to `bca391ae6`, the median was last touched by a
commit changing under five lines and 30% by one changing two or fewer. "Recently
edited" therefore means someone corrected a typo, and an old date is equally
mute, since a page can go untouched because it is still correct.
`about/introduction.html`, the third most-read page in the manual, carries a
2026-06 date it earned with a two-line change. Measuring the size of those
commits instead is what **lines changed** does.

## Runbook

Python 3 stdlib and `git`; no other dependencies. Copy `.env.example` to `.env`
and add a Plausible API key with read access to the site — only `fetch.py` uses
it.

```sh
python3 scripts/fetch.py probe          # what this instance supports
python3 scripts/fetch.py backfill       # ~13 months, about a minute
python3 scripts/inventory.py            # clones godot-docs (~740 MB), ~7 s after
python3 scripts/aggregate.py            # writes docs/data.js
open docs/index.html                    # no server needed
```

Afterwards, `python3 scripts/fetch.py incremental` fetches only months not
already stored, always refreshing the newest.

Tests: `python3 -m unittest discover tests`. They cover URL canonicalisation, the
rst parsers, and the invariants the page depends on — including a check that no
identity-derived number reaches the page, and one that per-visitor figures are
still implausible, so if the instance is ever fixed the suite says so instead of
the constraint quietly outliving its reason. Where the CSV export is present they
also cross-check page totals against it, since it was produced independently of
the API path.

### Verifying the page

One part of `docs/app.js` is covered: `deno test --allow-read tests/` runs the
churn grid's renderer against the data the page ships. It exists because shading
is set through a style string, and an alpha of `NaN` is not an error in a
browser — the declaration is dropped, every cell goes white, and the page carries
on. That is what happened when a refactor took a `top` declaration out and the
name resolved to `window.top`: no console output, nothing for `deno check` to
see.

**The rest of the page is not covered.** Everything else runs against the Python
payload, and the payload has been right every time the page was wrong. The bug
that made the section chart's reading bars sum to 89.6% instead of 100% lived
entirely in JavaScript grouping logic.

Headless Chrome renders `file://` directly, which is enough to check both that
the page runs and what it computed:

```sh
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --virtual-time-budget=8000 \
  --dump-dom "file://$PWD/docs/index.html#s=tutorials&f=pages" > /tmp/dom.html
```

The dumped DOM carries everything `app.js` wrote into the document — the stat
tiles, both tables, the generated captions. Chart contents live in canvas and
need one extra step: copy `index.html`, append a script that reads
`Chart.getChart("chart-sections").data` into a `<pre>`, dump that instead, and
delete the copy. That is how the 89.6% was found, and it is worth repeating after
any change to the charts.

Two cautions. Chrome's stderr carries `ERROR:gpu/...` lines in headless mode that
have nothing to do with the page. And the path bar's menus are built when opened
and removed when closed, so a dump never contains one — assert on
`menuItems(section)` rather than on the markup.

Not yet automated: a `scripts/check_page.py` wrapping the above would close the
gap, and would sit outside the unit suite since it needs Chrome present.

Licensed [MIT](LICENSE).
