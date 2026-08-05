# What was tried and cut

Four things were built far enough to check and then removed. What they turned up
is the useful part, so it is kept here.

**A translator's view** — for each language, which pages are most read against
how much of them is still untranslated. Abandoned at the point of drawing it,
because **translators are already working in demand order**: across the
mid-coverage languages the correlation between a page's pageviews and its coverage
runs 0.44 to 0.60 (ja 0.60, es 0.50, fr 0.45, pt-br 0.44), and their top decile by
demand is 75–85% translated against 30–44% overall. The recommendation would also
have been obvious — 13 of Japanese's top 20 by pageviews per remaining word are
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

**Translation share** — translated pageviews as a multiple of the same page's
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
`about/introduction.html`, the third most-viewed page in the manual, carries a
2026-06 date it earned with a two-line change. Measuring the size of those
commits instead is what **lines changed** does — see
[METHOD.md](METHOD.md#metric-definitions).
