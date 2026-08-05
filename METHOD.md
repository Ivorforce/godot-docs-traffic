# Method

What every figure in the report counts, how it was chosen, and what the build
checks before publishing. Read this to audit a number or to change the code that
produces one. The report's own captions carry the short form; this is the long
one.

## Why this report only uses pageviews

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

## Metric definitions

- **pageviews** — the only traffic metric used anywhere.
- **page** — a URL that resolves to a `.rst` file in godot-docs at the recorded
  commit. Directory URLs and `index.html` are folded onto one canonical form, or
  a page's traffic would split across two rows and each would look half as read.
  Only `/en/stable/` counts, so the pinned releases and `/en/latest/` are
  excluded — 336,181 of 1,717,257 English pageviews, a fifth, and the larger of
  the two editions exclusions this report makes. Both are named in the tile whose
  figure they are missing from, this one in **pageviews** and the translated one
  in **read in translation**, rather than described in a caveat nobody can check.
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
  URLs with no source file split three ways: about half match a rule in the CSV,
  most of the rest went to URLs that were never pages here, and a small remainder
  are **removed pages with nothing forwarding them**. That last group is what the
  table lists; `python3 scripts/aggregate.py --check` prints the current split. The inference holds in one direction only, which is enough: since a
  server-side redirect records no pageview, a URL that reaches this report is not
  covered by one.

  **Forwarded is not the same as arrived**, so the join follows the hops and the
  bucket splits by where the reader ends up: **forwarded** reached a page (or an
  off-site target the docs chose, which nothing here can follow), **looping**
  never arrives, and **forwardedNowhere** gets a second "page not found". In July
  that is 1,746, 11,996 and 32 pageviews — **87% of what this report used to call
  benign was a loop**. See [Checks](#checks).
- **translation reads** — pageviews for the same path summed across all
  non-English languages. 35% of pageviews on the current docs is non-English. The
  scatter names the count on hover; it is nowhere else (see [NOTES.md](NOTES.md)).

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
  count (6.4% against 6.3% of pageviews) but well above it by words (9.1%), while
  `navigation/` is the reverse at 4.8% of pages and 2.7% of words.
- **last edit** — the most recent commit touching the source file. Shown on the
  scatter's hover only, as context for a page already chosen (see
  [NOTES.md](NOTES.md)).
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

## The churn grid

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
two years where every traffic figure in the report is one month, so no column of
the grid has a traffic figure beside it at all.

## Which edition people read

The one chart drawn from more than one month, and the one place the fifth of
English reading outside `/en/stable/` is a shape rather than a number.

**The total barely moves**: 1.63M–1.90M pageviews across the thirteen stored
months, ±8%, no trend, no seasonality, and the fifty most-read pages of any
month overlap July's fifty by 44 to 50. That is what makes a one-month snapshot
honest, and why the chart is counts rather than a 100% stack — normalising the
flat sum away would leave only the composition.

**The shape to read is the handover.** `/en/4.4/` held 28% of English reading in
August 2025, while it was current, and 3% by November: a superseded release's
tail runs about two months, which is the figure behind backporting a fix to it.
The reverse holds before a release — `/en/4.7/` reached 144k pageviews a month
while still in development. Looping pageviews are excluded here and nowhere
else: one looping URL fired 7.2M of them in April 2026, four times the site's
ordinary month, which drawn to scale leaves the other twelve columns a smear.

**Months, not quarters.** `/en/4.4/` ran 18%, 28%, 29%, 13%, 5% across five
months, which in calendar quarters is 25%, 8%, 5% — the peak averaged away, the
collapse reduced to one edge. Two runs in three would also end on a part-period,
and where the churn grid marks one with a `*` because it shades a rate, a stacked
count drawn from one month of three is a third as tall and reads as traffic
collapsing. The chart draws the newest 24 months and says so.

**Bars that touch, rather than a stacked area.** The bands read as layers across
time either way, but a bar keeps each month a closed count, where an area puts
its vertices at month centres and interpolates a third of a year between two of
them. [godot-area-throughput](https://github.com/Ivorforce/godot-area-throughput)
draws its sediment over three years of months, and past 36 points or so the
interpolation stops mattering — at which point this should become one too.

## Checks

`aggregate.py` runs two integrity checks on every build. Both print warnings and
change nothing; `--check` lists what they found. They exist because `update.yml`
publishes on the 2nd of every month with nobody watching, and both failures are
invisible in the report itself: a month in which one URL took 81% of the traffic
draws exactly like an ordinary month.

**Redirect cycles** need no traffic data — they are a property of
`_static/redirects.csv` and the page list. 404.rst matches by
`pathname.endsWith(source)` and applies the redirect in the browser, so a
destination that is not a page serves 404.rst again, which consults the same
file again:

```
/development/file_formats/index.html
  -> /engine_details/development/file_formats/index.html   (not a page)
  -> /engine_details/development/file_formats/index.html   ...
```

The tree has no `development/` segment there; the page is
`engine_details/file_formats/index.html`. **Eight rules currently loop**, into
seven URLs, and readers reaching them account for 7,830,862 pageviews across the
stored history — 7,248,306 of them on that one URL in April 2026, 84% of the
month's `/en/stable/` traffic, at ~600,000 pageviews a day against a site
baseline of ~80,000.

**Traffic anomalies** are checked on two arms, because neither alone would have
named both incidents. Per page, a URL is flagged when it gains at least 1% of the
newest month — about 17,000 pageviews — against the *median* of its own earlier
months. An excess rather than a ratio, since both incidents rose from zero, where
every ratio is infinite; a median rather than the previous month, so a burst
already in the history cannot raise the bar after it.

Per day, from `site-daily.ndjson`, a day is flagged above 3× its trailing 90-day
median. This arm covers the whole docs site rather than English alone: the
largest bursts on record, about 3M pageviews a month across November and December
2025, are not in `docs-en-*.ndjson` at all. No day is judged until the window
behind it is full — pageviews rose 40× over the two months the tracker was being
set up, and against a median drawn from that ramp every ordinary day after it
clears the line.

Neither check fails the build. The cycles are a bug in godot-docs rather than in
this report, and a month with a burst in it is still the right month to publish:
in the one case on record only the stale bucket read wrong. What must not happen
is publishing it with nobody knowing.
