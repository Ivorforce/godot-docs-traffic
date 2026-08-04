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
once in July 2026, and 511 hand-written pages carry a million pageviews. So "what can be trimmed" has no easy answer here, and the report is
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
  without shipping fifteen parallel tables. Two counts per path, not one: the
  current docs and every version, because a share needs the first and the
  breadth of translated reading needs the second. Only the newest month pages the full
  hostname (nine requests); older months fetch the English subset in one.

  Three kinds go unread by the report: `www` (pages on `godotengine.org`),
  `outbound` (clicks leaving the site, which is where download links show up)
  and `os`. They belong to a *visitor* report rather than a docs one — the
  question they answer is about the engine's audience, not about which page
  needs care — and every claim on this page comes from joining traffic to the
  source tree, which none of them can do. Fetching them costs three queries a
  month and keeps the history accumulating, so a separate analysis one day
  starts with the months already stored rather than with a backfill.
- `scripts/inventory.py` — clones `godot-docs` at the `stable` branch (which is
  what Read the Docs serves at `/en/stable/`, not `master`) and records each
  page's word count, last-edit timestamp, two-year line churn and toctree
  position, plus the date every *removed* page left the tree and the redirects
  that forward the URLs they left behind. Sparse (`*.rst` plus
  `_static/redirects.csv`) so the working tree stays small, but **not**
  blob-filtered: `git log --numstat` needs both sides of every diff, and on a
  filtered clone it fetches blobs one request at a time — minutes over a year,
  and it fails outright over more — a two-year walk fails too, so shortening the
  window does not buy the filter back. Unfiltered the same walk takes seconds, at the cost of a ~740 MB clone that takes a minute or two
  the first time. A cache left over from the filtered era is detected and
  refetched once, rather than failing in a way that reads like a git bug.
  History is kept in full either way, since last-edit dates are the entire
  point. The sparse spec is reapplied on every run, so a cache built by an older
  revision picks up newly needed files rather than silently missing them.
  Nothing of this reaches GitHub: `.cache/` is gitignored.
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

  `404.rst` is dropped as well, being the "page not found" template rather than
  a page of the manual. Read the Docs serves it *in place of* a missing URL and
  the pageview records the URL the reader asked for, so the reading it stands
  for is already counted under **forwarded** and in the removed-pages table,
  while `/en/stable/404.html` itself draws two or three views a month from people
  who navigate to it directly. Counted as a page it was a bar in the section
  chart with no reading and no words — its body is a raw HTML block, so its
  length means nothing either — and the one page behind what used to read "all
  but one page was read at least once".
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
  descriptive. Pageviews to URLs with no source file split three ways: about
  half are forwarded by the CSV and the reader arrived where they meant to, most
  of the rest went to URLs that were never pages here (Sphinx's search page,
  downloadable examples, `null` paths, scanner noise), and a small remainder are
  **removed pages with nothing forwarding them**. Only that last group is worth
  anyone's time, and it is a fraction of what the unjoined list implied;
  `python3 scripts/aggregate.py --check` prints the current split. The
  inference holds in one direction only, which is enough: since a server-side
  redirect records no pageview, a URL that reaches this report is not covered by
  one, so absence from the CSV means genuinely unforwarded rather than possibly
  handled elsewhere.
- **translation reads** — pageviews for the same path summed across all
  non-English languages, so a page's weight in translation shows up next to its
  English figure. 34% of reading on the current docs is non-English.

  The table shows the count; the scatter colours by the **share** — translated
  reading as a multiple of the same page's English figure — because the raw
  count correlates with English reading at r = 0.92 on log-log and as a colour
  would only redraw the horizontal axis. The share varies structurally:
  `engine_details/development` runs at 0.78× and `getting_started/introduction`
  at 0.72×, against 0.35× for `tutorials/networking` and 0.26× for
  `tutorials/inputs`. People start in their own language and move to English as
  they go deeper. Two readings are actionable: a share near zero flags a page
  the translations have not reached (the whole of `community/asset_store/` sits
  at 0.00–0.13×, being new), and a high share flags a page where an English
  rewrite invalidates work in fifteen languages.

  Two joins have to be right for any of that to mean anything.

  *Which version.* The English side is `/en/stable/` alone, so the translated
  side must be the current docs alone too, or the share is inflated by whatever
  reading sits on pinned releases. The languages make that awkward: English has
  `stable` and `latest` and the translations have neither — their current
  edition is `4.x`, carrying 87% of translated reading against 1,552 views for
  `stable`, and `/xx/latest/` is scanner noise outside one language. So
  `fetch.py` decides by exclusion — a pinned release is spelled as a number —
  and stores two counts per path: `c` for the current docs, which every share
  divides by, and `pv` for every version. It matters by a sixth: 130,039 of
  851,950 translated pageviews are on pinned releases.

  *Which path.* Translation paths are keyed on the remainder after
  `/<lang>/<version>/`, which is *empty* for the manual's landing page — the
  largest single key in the file. Folding it onto `index.html` is what
  `load_i18n` does beyond the obvious directory rule, and `TestI18nKeys` pins
  it. 97% of translated pageviews join to a page; the rest is the same residue
  the English side has, `search.html` and paths that only ever existed in 3.x.
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
  colour — and measuring the size of those commits instead is what **lines
  changed** below does.
- **lines changed** — source lines added plus deleted over the two years ending
  at the recorded docs commit, from `git log --numstat -M`. Added and
  deleted are kept apart, and the table shows the total and the **net**: a page
  whose net is close to its total was written, one whose net is near zero was
  rewritten at its existing length. Per section the same split separates growth
  from rework — `tutorials/` added 13,473 lines against 3,623 deleted while
  `getting_started/` added 225 against 222, the same activity by volume and a
  different one in kind.

  Lines rather than commits, because a commit count cannot tell a typo from a
  rewrite. Over this window the most-committed hand-written page took 13
  commits, while the largest single change was 1,822 lines in three of them; the
  edit-count distribution runs 0–13 with 161 pages tied at one, and the
  line-churn distribution spans three orders of magnitude with its top decile
  holding 68% of all changed lines. That is the difference between a ranking
  that is mostly ties and one that can be read.

  `-M` is not optional, and it is applied twice. Within a commit it stops a move
  reading as a whole text deleted and an identical text written by nobody. Across
  the history, a second walk (`--diff-filter=R --name-status`) builds a map of
  every rename — 846 of them — and each diff is carried forward along that chain
  to the path its file ended up at, so a page that moved keeps one history rather
  than two halves filed under two names. Without it a year of churn strands 1,094
  lines on dead paths, and any longer window strands whole restructures.

  **`_static/redirects.csv` cannot do this job**, though it looks as if it
  should. It maps URLs rather than source paths, it is hand-maintained with
  nothing checking it, and where a page was deleted rather than moved there is no
  live page for its history to join to. Git recorded the moves itself.

  **Two years is the window**, and it is set by what anyone can act on, not by
  what git can reach. Four years was built and rejected: it spans roughly eight
  minor releases, by which point a quiet stretch says nothing a maintainer can do
  anything about, it doubles the columns, and — where a level has more children
  than fit — ranking by four years of churn fills half the list with pages last
  worked on in 2023. In the class reference only 13 of the top 25 rows are the
  same list under the two windows.

  Four years is where the *data* would stop, if the data were binding. Resolved
  through the rename chain, the share of each year's changed lines landing on a
  page still in the tree runs 95% (2022), 96%, 99%, 93%, 95% (2026), then falls
  to 83% in 2021 and 66% in 2020 — the Godot 4 restructure having removed or
  rewritten most of what came before. Two years sits well inside that, so the
  join is not what limits the choice.

  The grid follows the path bar like every other chart: its rows are the
  children of the level you are on, so drilling into `tutorials/` replaces the
  six sections with the twenty-six things inside it. That is the whole point of
  it — `tutorials/xr/` took 1,823 lines in one quarter and
  `tutorials/navigation/` has been silent since July with 15 of its 19 pages
  untouched, and neither is visible from the top level. Rows are capped at the
  25 most-edited and what the cap left out is printed underneath, because a
  level can have a thousand children: inside the class reference, 612 pages
  changed and 467 of 1,079 did not.

  Columns are **calendar quarters**, eight of them, ending with the one the
  docs commit falls in. Calendar-pinned rather than rolled back from the newest
  month: both are redrawn every month, but rolling boundaries move under the
  reader, so a figure taken from one run covers a different span in the next and
  no two runs of the report can be compared. A closed quarter never changes
  again. The price is that the newest column stops at the docs commit rather than
  at its own end; it is marked `*` in the header and called out in the caption,
  because read as a full column it looks like work falling off.

  Months were the first cut and were too fine even over one year: a third of the
  cells were empty, a single piece of work spread across two or three of them,
  and an empty cell recorded the rhythm of when pull requests land rather than
  anything about the page. Eleven columns still need the grid to abbreviate —
  `Q3 24` for the headers, `24k` and `1.3k` for the figures, tighter padding and
  a row label that ellipsises, with the exact number and full name on hover —
  which brings it to 928 px at every level of the manual. A row read half at a
  time answers nothing.

  **Shading is churn against length, on a square root, scaled to what is on
  screen.** Three separate decisions, each forced by a way the previous version
  failed.

  *Against length, not raw.* Shading the line counts made a table of size:
  sections differ by three orders of magnitude and a row's own cells usually by
  less than one, so the scale spent its range restating what the figures, the
  ordering and the added column already said — 24,229 sat one shade off 7,851.
  Dividing by the row's current length makes the colour comparable down a column
  as well as across a row, and carries what the figures cannot: in 2023 H1
  `getting_started/` changed 3,531 lines to `tutorials/`'s 24,229 and was the
  harder worked of the two, 45% of its own length against 23%.

  *Scaled to the darkest cell shown, not to a fixed 1x.* A section almost never
  changes by its whole length in half a year — `tutorials/`'s busiest half turned
  over 23% of it — so anchoring the top of the scale there spent three quarters
  of the palette on nothing and pushed every section back into the palest end,
  the same washout reached from the other side. The cost is that the colours
  redraw when you change section; the figures do not.

  *Square root, not linear or log.* The cells are heavy-tailed at every level of
  the manual: measured across all 63 navigable levels, the darkest cell is a
  median of 12.9x the median cell and up to 55x. Linear leaves the bulk at a
  tenth of the range; a log ramp overcorrects and darkens the quiet cells until
  absence stops reading, which is what the grid is for. Root sits between them —
  `tutorials/` spans 23–53 across its eight columns where a fixed 1x anchor left
  it inside 7–23.

  Shading against each row's own busiest period was the other candidate and is
  worse than any of these: it throws away every comparison between rows, so one
  20-line edit to a dead section renders exactly as hot as `tutorials/` at its
  peak. The divisor is the length **today**, not at the time — one divisor per
  row leaves the row's profile untouched, where a per-period divisor would bend
  the very shape the row exists to show.

  Version cycles were the other candidate and they lose. Boundaries are
  derivable from the repo (each `upgrading_to_godot_4.x.rst` is created near the
  start of its cycle: 4.5 on 2025-09-13, 4.6 on 2026-01-20, 4.7 on 2026-06-18),
  but the durations run 2.4, 4.2, 4.9 and 0.2 months, and a six-day newest column
  reads as either nothing happening (raw) or a tenfold spike (per month) with
  neither being true — the whole `community/` burst lands inside it. The column
  set would also change shape whenever a new upgrade guide lands, and the
  boundary itself is an inference about what that file's creation date means.

  The class reference follows the same toggle as everywhere else rather than
  being special-cased out, but its rows mean something different: those pages
  sync in bulk from the engine's source, so their diffs are the size of whatever
  the engine changed — 77% of all edited lines, with nobody having written a
  sentence.

  It measures activity, not quality and not need. A page nobody has touched
  reads identically whether it is finished or forgotten, and nothing in this
  data separates those two; the grid is a map of where work happened, not a list
  of what to do next.

  Its window ends at the docs commit, not at the end of the traffic window, so
  the newest column is a part-period and can lag the newest traffic month by
  weeks. The two spans are not aligned and are not comparable period for period —
  and churn reaches back two years where traffic reaches back thirteen months,
  so the older half of the grid has no traffic figure beside it at all.

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

Tests: `python3 -m unittest discover tests`. They cover URL canonicalisation,
the rst parsers, and the invariants the page depends on —
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
positives. And the path bar's menus are built when opened and removed when
closed, so a dump never contains one — assert on the sections a menu would list,
which is `menuItems(section)`, rather than on the markup.

Not yet automated. A `scripts/check_page.py` wrapping the above and asserting a
few invariants (section shares summing to 100% across several hash states) would
close the gap; it would need Chrome present, so it would sit outside the unit
suite.

Licensed [MIT](LICENSE).
