#!/usr/bin/env python3
"""Turn data/raw/ + data/inventory.json into docs/data.js.

    python3 scripts/aggregate.py

Emits one `window.DATA = {...}` assignment, loaded by a plain <script src> so
docs/index.html opens straight from disk with no server. Every metric definition
lives here, so a new chart can be recomputed over stored history without
refetching anything.

Two rules run through the whole file:

  * Pageviews only. Per-visitor and per-session figures are not reliable on this
    analytics setup (see the README) and are never emitted.
  * A docs URL counts as a page only if it resolves to a source file in
    godot-docs. The rest — stale paths, Sphinx's search page, scanner noise — is
    counted and reported, not silently dropped.
"""

import argparse
import collections
import datetime
import json
import re
import statistics
import sys
import urllib.parse
from pathlib import Path

import plausible
import store

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "data" / "inventory.json"
OUT = ROOT / "docs" / "data.js"

URL_PREFIX = "/en/stable/"

# How many unmatched inbound URLs to list. They are useful to docs maintainers,
# but the tail is thousands of scanner hits, so it is cut and the cut reported.
STALE_LIMIT = 25

def canonical(url):
    """Normalise a trafficked URL to the form the inventory uses.

    Read the Docs serves a directory both as `.../` and `.../index.html`, and
    the docs root additionally as `/en/stable/`. Left alone, a page's traffic
    splits across two rows and both look half as read as they are.
    """
    url = url.split("#", 1)[0].split("?", 1)[0]
    if url == URL_PREFIX + "index.html":
        return URL_PREFIX
    if url.endswith("/") and url != URL_PREFIX:
        return url + "index.html"
    return url


def month_start_ts(month):
    """Unix timestamp of midnight on the first of a "YYYY-MM" month.

    Compared against git commit times, which are absolute; a few hours of
    timezone slack does not matter for "was this page still here?".
    """
    start = datetime.date.fromisoformat(plausible.month_range(month)[0])
    return int(datetime.datetime.combine(
        start, datetime.time(), datetime.timezone.utc).timestamp())


def redirect_map(inventory):
    """{source: destination} from the ordered rules in `_static/redirects.csv`.

    Later duplicate sources are unreachable — 404.rst stops at the first match —
    so the first one wins here too.
    """
    out = {}
    for source, destination in inventory.get("redirects", []):
        out.setdefault(source, destination)
    return out


def redirect_for(url, redirects):
    """The redirect 404.rst would apply to this URL, or None.

    Mirrors its matching exactly: `pathname.endsWith(source)`, first match in
    file order wins. Checking each "/"-anchored suffix against a dict is the
    same test as scanning every source, without the 1.3M string comparisons.
    """
    cut = url.find("/", 1)
    while cut != -1:
        hit = redirects.get(url[cut:])
        if hit is not None:
            return hit
        cut = url.find("/", cut + 1)
    return redirects.get(url)


def redirect_walk(url, redirects, pages):
    """Where a reader who asks for `url` ends up, following 404.rst as it runs.

    Returns (kind, url), kind being "page", "dead", "external" or "cycle".

    A redirect out of `redirects.csv` is applied in the browser, on the 404 page,
    so the destination is fetched as an ordinary request: if it is not a page
    either, it serves 404.rst again, which consults the same file again. Nothing
    in that loop notices it has been there before, and each pass fires the
    analytics script — which is why a cycle costs pageviews rather than being
    merely broken.

    Needs no hop limit: every URL after the first comes from a destination in the
    rule set, so the visited set closes a finite walk.
    """
    seen = set()
    while url not in seen:
        if url in pages:
            return "page", url
        destination = redirect_for(url, redirects)
        if destination is None:
            return "dead", url
        if not destination.startswith("/"):
            return "external", destination  # off-site; the reader has left
        seen.add(url)
        url = canonical(URL_PREFIX + destination.lstrip("/"))
    return "cycle", url


def stale_kind(removed_ts, window_start, landing):
    """Why a trafficked URL has no page, which decides what its count means.

    Forwarding comes first, and `landing` is where the reader ends up rather
    than whether a rule matched — the two are not the same, and assuming they
    were kept a redirect loop in the benign bucket. It is the kind
    `redirect_walk` returns, or None where no rule matched:

      * **forwarded** — the reader arrived, and the pageview here is an artifact
        of the redirect running in the browser. An off-site destination counts
        too: a target the docs chose, which nothing here can follow.
      * **looping** — never arrives, and every pass fires another pageview. Not
        readers; one reader counted thousands of times.
      * **forwardedNowhere** — forwarded to a URL that is not a page and is not
        forwarded on, so a "page not found" is answered with another one.

    Otherwise a page removed before the window was gone for all of it, so every
    view is a reader who hit a dead end — the only bucket anyone can act on. One
    removed during the window was readable for part of it. A URL that was never
    a page here is neither.
    """
    if landing in ("page", "external"):
        return "forwarded"
    if landing == "cycle":
        return "looping"
    if landing == "dead":
        return "forwardedNowhere"
    if not removed_ts:
        return "neverPage"
    return "removedDuring" if removed_ts >= window_start else "noRedirect"


def load_docs_month(month):
    """{canonical url: pageviews} for /en/stable/ in one month."""
    out = collections.Counter()
    for row in store.read("docs-en", month):
        if row["p"].startswith(URL_PREFIX):
            out[canonical(row["p"])] += row["pv"]
    return out


def load_en_other(month):
    """Pageviews on English docs outside `/en/stable/` in one month.

    The stable-only rule that keeps the English and translated sides comparable
    also drops the pinned releases and `/en/latest/`, and that is the larger of
    the two exclusions it makes. Emitted for the same reason as
    `i18nOtherVersions`: so the page can name what it leaves out rather than
    describe it.
    """
    return sum(row["pv"] for row in store.read("docs-en", month)
               if not row["p"].startswith(URL_PREFIX))


def load_editions_month(month):
    """{canonical url: pageviews} for every English edition in one month.

    The traffic checks read this rather than `load_docs_month`: an anomaly does
    not respect the report's window, and a fetch answering for the wrong
    hostname, or a scraper on a pinned release, would be invisible to a check
    that filtered first.
    """
    out = collections.Counter()
    for row in store.read("docs-en", month):
        out[canonical(row["p"])] += row["pv"]
    return out


def i18n_key(rest):
    """Fold a translated path onto the key `build_docs` derives from a canonical
    English URL, which is `index.html` for the manual's landing page.

    A reader at `/ja/stable/` leaves an *empty* remainder, not one ending in
    "/", so the directory rule alone misses it — and that one path is the
    largest single key in the file (4% of all translated traffic).
    """
    if not rest or rest.endswith("/"):
        rest += "index.html"
    return rest


def read_i18n(month, field="c"):
    """Yield (key, language, pageviews) for the translation roll-up.

    `field` picks the edition: "c" is the current docs in each language, the
    only figure comparable with the English side, which is `/en/stable/` alone.
    "pv" is every version, which is not a translation share but is a real number
    about how translated traffic is spread.

    `language` is None unless the file was fetched with `--by-language`, which
    is off by default because nothing on the page reads it.
    """
    for row in store.read("docs-i18n", month):
        if field not in row:
            sys.exit(f"data/raw/docs-i18n-{month}.ndjson predates the per-version "
                     f"split and cannot be corrected after the fact — every "
                     f"version is already summed into one number. Re-run "
                     f"`python3 scripts/fetch.py incremental`.")
        yield i18n_key(row["p"]), row.get("l"), row[field]


def load_i18n(month, field="c"):
    """{path: pageviews} summed over all non-English languages, so a page can
    show how much it is read in translation. Reads either shape of the file:
    summing an already-summed roll-up is the identity."""
    out = collections.Counter()
    for key, _lang, pv in read_i18n(month, field):
        out[key] += pv
    return out


def load_i18n_by_lang(month, field="c"):
    """{language: {path: pageviews}}. Needs `fetch.py --by-language`.

    Summing the languages is right for the English report — one number saying a
    page carries weight in translation — and wrong for a translator, whose
    priorities are their own language's and not the sum of thirteen. Nothing in
    this pipeline calls it; it is what `scripts/l10n.py` gets joined against
    when the translation question is asked again.
    """
    out = collections.defaultdict(collections.Counter)
    for key, lang, pv in read_i18n(month, field):
        if lang is None:
            sys.exit(f"data/raw/docs-i18n-{month}.ndjson summed the languages "
                     f"away, and a sum cannot be split back into what it summed. "
                     f"Re-run `python3 scripts/fetch.py incremental --by-language`.")
        out[lang][key] += pv
    return out


def build_funnel(inventory):
    """The onboarding tutorials, each with its landing page and its steps.

    Read from the toctrees rather than hardcoded, so a docs restructure moves
    the chart instead of silently breaking it. The grouping is the docs' own:
    every entry in the getting-started toctree is one tutorial's landing page,
    and that page's own toctree is its steps. Kept rather than flattened,
    because the four tutorials are four separate sequences that each restart at
    their landing page, and a reader given one run of pages has no way to see
    where one ends and the next begins.
    """
    toctrees = inventory["toctrees"]
    src_to_url = {p["src"]: url for url, p in inventory["pages"].items()}
    out = []
    for entry in toctrees.get("index.rst", []):
        if not entry.startswith("getting_started/"):
            continue
        landing = src_to_url.get(entry)
        steps = [src_to_url[src] for src in toctrees.get(entry, [])
                 if src in src_to_url]
        # A tutorial with no steps is a single page, not a sequence: there is
        # nothing for the chart to draw a shape between.
        if landing and steps:
            out.append({"landing": landing, "steps": steps})
    return out


def churn_months(inventory):
    """Every month of the churn window, including ones nobody touched.

    Built from the window rather than from the data: a section that was silent
    for a quarter is the thing this grid exists to show, and months derived from
    the commits themselves would close the gap up.
    """
    start = datetime.date.fromisoformat(inventory["churnSince"])
    end = datetime.datetime.fromtimestamp(
        inventory["churnHead"], datetime.timezone.utc).date()
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


PERIOD_MONTHS = 3


def churn_periods(months, head_month):
    """The churn window cut into calendar quarters, oldest first.

    Calendar periods rather than a window rolled back from the newest month.
    Both are redrawn every month; the difference is that rolling boundaries move
    under the reader, so a figure taken from one run covers a different span in
    the next and no two runs can be compared. A closed calendar half never
    changes again. The price is that the newest column is a part-period, which
    is marked rather than hidden.

    Quarters. Months were the first cut and were too fine to read — a third of
    the cells were empty and one piece of work spread across two or three of
    them, so an empty cell recorded the rhythm of when pull requests land rather
    than anything about the page. At a quarter an empty cell means nothing
    touched that part of the docs for three months, which is worth knowing.
    Sixteen of them only fit the page once the labels and the figures are
    abbreviated, which is why the grid says "Q3 22" and "24k".
    """
    groups = {}
    for month in months:
        year, number = int(month[:4]), int(month[5:])
        groups.setdefault((year * 12 + number - 1) // PERIOD_MONTHS, []).append(month)
    return [{"label": period_label(groups[key]), "months": groups[key],
             "partial": groups[key][-1] == head_month}
            for key in sorted(groups)]


def period_label(group):
    """"Q3 24". Short because ten columns share the grid's width.

    Groups are calendar-aligned, so every month in one falls in the same
    quarter and the first of them names the whole column — including the newest,
    which may hold only part of its quarter.
    """
    year, number = group[0].split("-")
    return f"Q{(int(number) - 1) // PERIOD_MONTHS + 1} '{year[2:]}"


def build_churn(inventory, index):
    """Lines changed per page per quarter, as {row index: {quarter: lines}}.

    Emitted per page rather than rolled up per section, because the grid follows
    the path bar like every other chart on the page: the rows are whatever level
    the reader has drilled to, which is not a grouping this script can know.
    Rolling up in the browser costs one pass over rows that are already in
    memory.

    Sparse — 928 pages have any churn at all and most of those in one or two
    quarters, so the zeros carry nothing. Added and deleted are not repeated
    here: the page recovers them from each row's total and net.
    """
    months = churn_months(inventory)
    periods = churn_periods(months, months[-1])
    position = {month: i for i, period in enumerate(periods)
                for month in period["months"]}
    churn = inventory.get("churn", {})
    series = {}
    for url, meta in inventory["pages"].items():
        entry = churn.get(meta["src"])
        if not entry:
            continue
        cells = collections.Counter()
        for month, lines in entry["m"].items():
            if lines and month in position:
                cells[position[month]] += lines
        if cells:
            series[index[url]] = dict(cells)
    return {"periods": [{"label": p["label"], "partial": p["partial"]}
                        for p in periods],
            "months": [p["months"] for p in periods],
            "since": inventory["churnSince"], "series": series}


def build_docs(months, inventory):
    newest = months[-1]
    current = load_docs_month(newest)
    previous = load_docs_month(months[-2]) if len(months) > 1 else {}
    i18n = load_i18n(newest)
    pages = inventory["pages"]
    churn = inventory.get("churn", {})

    matched, stale = {}, {}
    for url, pv in current.items():
        (matched if url in pages else stale)[url] = pv

    paths, rows = [], []
    for url in sorted(pages):
        meta = pages[url]
        rest = url[len(URL_PREFIX):] or "index.html"
        paths.append(url)
        # Churn is emitted as its total and its net, not as added and deleted:
        # the pair is recoverable from them, and total-and-net is the pair the
        # page reads out — how much of the file moved, and whether the page grew
        # by it or was rewritten in place.
        edits = churn.get(meta["src"]) or {"a": 0, "d": 0}
        rows.append([
            len(paths) - 1,
            matched.get(url, 0),
            previous.get(url, 0),
            meta["words"],
            meta["edited"] or 0,
            i18n.get(rest, 0),
            1 if meta["generated"] else 0,
            edits["a"] + edits["d"],
            edits["a"] - edits["d"],
            meta["lines"],
        ])

    # Monthly series per top-level section, for the trend chart.
    sections = collections.defaultdict(lambda: collections.defaultdict(int))
    for month in months:
        for url, pv in load_docs_month(month).items():
            if url not in pages:
                continue
            rest = url[len(URL_PREFIX):]
            top = rest.split("/")[0] if "/" in rest else "(root)"
            sections[top][month] += pv

    removed = inventory.get("removed", {})
    redirects = redirect_map(inventory)

    window_start = month_start_ts(newest)
    kinds, urls = collections.Counter(), collections.Counter()
    unforwarded, looping = [], []
    for url, pv in stale.items():
        landing = (redirect_walk(url, redirects, pages)[0]
                   if redirect_for(url, redirects) else None)
        kind = stale_kind(removed.get(url), window_start, landing)
        kinds[kind] += pv
        urls[kind] += 1
        if kind in ("noRedirect", "removedDuring"):
            unforwarded.append([url, pv, removed[url]])
        elif kind == "looping":
            looping.append([url, pv])
    unforwarded.sort(key=lambda r: -r[1])
    looping.sort(key=lambda r: -r[1])

    return {
        "branch": inventory["branch"],
        "commit": inventory["commit"][:9],
        "prefix": URL_PREFIX,
        "paths": paths,
        "rows": rows,
        "funnel": build_funnel(inventory),
        "churn": build_churn(inventory, {url: i for i, url in enumerate(paths)}),
        "sections": {k: [v.get(m, 0) for m in months] for k, v in sections.items()},
        "unforwarded": unforwarded[:STALE_LIMIT],
        "looping": looping[:STALE_LIMIT],
        "unforwardedTotal": sum(r[1] for r in unforwarded),
        "unforwardedUrls": len(unforwarded),
        "staleTotal": sum(stale.values()),
        "staleUrls": len(stale),
        "staleKinds": dict(kinds),
        "staleKindUrls": dict(urls),
        "redirectCount": len(redirects),
        "windowStart": window_start,
        "matchedViews": sum(matched.values()),
        # English traffic outside /en/stable/ — the pinned releases and
        # /en/latest/. Excluded from every figure on the page, and the larger
        # of the two editions exclusions, so the caveat naming it names a
        # number rather than gesturing at one nobody can see.
        "enOtherVersions": load_en_other(newest),
        "i18nViews": sum(i18n.values()),
        # Translated traffic outside /xx/stable/: excluded from every share on
        # the page, since the English side it is compared against is stable
        # only. Emitted for the same caveat, for the same reason.
        "i18nOtherVersions": sum(load_i18n(newest, "pv").values()) - sum(i18n.values()),
    }


# --------------------------------------------------------------------------
# Integrity checks
#
# Nothing below reaches docs/data.js. These exist because the report publishes
# unattended — update.yml commits a new docs/data.js on the 2nd of every month —
# and both failures they look for are silent: a month where one URL took 81% of
# the traffic reads as an ordinary month in every figure the page draws.
# --------------------------------------------------------------------------

# A page has to gain this share of a month's pageviews to be called anomalous —
# about 17,000 here. An excess rather than a ratio, because both incidents on
# record rose from a baseline of zero, where every ratio is infinite and a ratio
# rule fires on every newly added page instead.
ANOMALY_SHARE = 0.01

# A day is anomalous at this multiple of the trailing median day. The bursts on
# record ran 3.3x to 8.8x; no release in the window produced a day above 2x.
ANOMALY_DAY_RATIO = 3
ANOMALY_DAY_WINDOW = 90

# How many flagged days --check prints, newest first. The count and the oldest
# date are printed either way, so the cut is visible.
SPIKE_LIMIT = 20


def page_anomalies(history, months, share=ANOMALY_SHARE):
    """[(excess, url, baseline, views)] for the newest month, largest first.

    The baseline is each URL's *median* over the earlier months rather than its
    mean or its previous month, so a burst that has already happened cannot raise
    the bar for the months after it.

    A check on the data, not on the docs: one or two rows a month is the normal
    reading, and what it answers is whether a figure on the page is about people.
    """
    if len(months) < 2:
        return []
    newest, earlier = months[-1], months[:-1]
    current = history[newest]
    floor = share * sum(current.values())
    out = []
    for url, views in current.items():
        baseline = statistics.median([history[m].get(url, 0) for m in earlier])
        if views - baseline >= floor:
            out.append((views - baseline, url, baseline, views))
    out.sort(reverse=True)
    return out


def day_anomalies(days, ratio=ANOMALY_DAY_RATIO, window=ANOMALY_DAY_WINDOW):
    """[(day, views, median)] for days standing above their trailing median.

    The daily series covers the whole docs site, so this arm sees what the
    per-page arm cannot: `docs-en-*.ndjson` is English only, and the largest
    bursts on record — November and December 2025, about 3M pageviews a month —
    are not in it at all. It says when, and the per-page arm says what.

    Trailing rather than whole-history, so a growing site is not compared against
    its first year forever. **A day is not judged until the window behind it is
    full**, which handles the one case a trailing median reads backwards: docs
    pageviews rose 40x over the two months the tracker was being set up, and
    against a median drawn from that ramp every ordinary day after it clears the
    line. Ten days of March 2025 are the difference, and not one is an event.
    """
    out = []
    for i, (day, views) in enumerate(days):
        if i + 1 < window:
            continue
        median = statistics.median(pv for _, pv in days[i - window + 1:i + 1])
        if median and views >= ratio * median:
            out.append((day, views, median))
    return out


def docs_days():
    """[(date, pageviews)] for docs.godotengine.org, oldest first.

    From `site-daily.ndjson`, which `fetch.py` stores for the README's table
    about visitor identity. Its pageview column is otherwise unused.
    """
    per_day = collections.Counter()
    for row in store.read("site-daily"):
        if row["h"] == "docs":
            per_day[row["d"]] += row["pv"]
    return sorted(per_day.items())


def redirect_cycles(redirects, pages):
    """[(source, url)] for rules whose forwarding never lands anywhere.

    Needs no traffic data: a property of `redirects.csv` and the page list at
    the recorded commit. 404.rst matches by `pathname.endsWith(source)`, so a
    destination that is *itself* a suffix of its own source matches the same rule
    again —

        /development/file_formats/index.html
          -> /engine_details/development/file_formats/index.html   (not a page)
          -> /engine_details/development/file_formats/index.html   ...

    where the destination carries a `development/` segment the tree does not
    have. Checked against the pages that exist now, so this is about the current
    docs; older editions have their own tree.
    """
    out = []
    for source in redirects:
        kind, end = redirect_walk(
            canonical(URL_PREFIX + source.lstrip("/")), redirects, pages)
        if kind == "cycle":
            out.append((source, end))
    return out


def cycle_views(current, redirects, pages):
    """(pageviews, [(url, pageviews)]) for trafficked URLs that loop.

    Counted from the URLs readers reached rather than from the rules, because a
    rule nobody hits costs nothing.
    """
    hits = [(url, views) for url, views in current.items()
            if url not in pages
            and redirect_walk(url, redirects, pages)[0] == "cycle"]
    hits.sort(key=lambda pair: -pair[1])
    return sum(views for _, views in hits), hits


def run_checks(months, inventory, verbose):
    """Print what the two checks found. Never raises and never changes output.

    A warning rather than a failed run: the cycles are a bug in godot-docs, and a
    month with a burst in it is still the right month to publish. What must not
    happen is publishing it without anyone knowing.
    """
    newest = months[-1]
    redirects = redirect_map(inventory)
    pages = inventory["pages"]

    cycles = redirect_cycles(redirects, pages)
    looped, hits = cycle_views(load_docs_month(newest), redirects, pages)
    if cycles:
        print(f"warning: {len(cycles)} of {len(redirects):,} redirect rules never "
              f"land — 404.rst forwards them to themselves, and each pass fires "
              f"a pageview ({looped:,} in {newest})"
              + ("" if verbose else "; --check lists them"))

    history = {month: load_editions_month(month) for month in months}
    anomalies = page_anomalies(history, months)
    if anomalies:
        print(f"warning: {len(anomalies)} url(s) stand above their own history "
              f"in {newest}" + ("" if verbose else "; --check lists them"))

    days = docs_days()
    spikes = day_anomalies(days)
    recent = [row for row in spikes if row[0][:7] == newest]
    if recent:
        print(f"warning: {len(recent)} day(s) in {newest} above "
              f"{ANOMALY_DAY_RATIO}x the trailing median"
              + ("" if verbose else "; --check lists them"))

    if not verbose:
        return

    if cycles:
        print(f"\nredirect rules that never land ({len(cycles)}):")
        for source, end in cycles:
            print(f"  {source}\n      loops on {end}")
        if hits:
            print(f"  reached by readers in {newest}:")
            for url, views in hits:
                print(f"    {views:>9,}  {url}")
    if anomalies:
        print(f"\nurls above their own history in {newest} "
              f"(>= {ANOMALY_SHARE:.0%} of the month):")
        for excess, url, baseline, views in anomalies:
            print(f"  +{excess:>9,.0f}  median {baseline:>9,.0f} -> {views:>9,}  {url}")
    if spikes:
        print(f"\ndays above {ANOMALY_DAY_RATIO}x the trailing median "
              f"({len(spikes)} of {len(days):,}):")
        for day, views, median in spikes[-SPIKE_LIMIT:]:
            print(f"  {day}  {views:>9,}  {views / median:5.1f}x median {median:,.0f}")
        if len(spikes) > SPIKE_LIMIT:
            print(f"  ({len(spikes) - SPIKE_LIMIT} older not shown, "
                  f"back to {spikes[0][0]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="print join diagnostics and exit without writing")
    args = ap.parse_args()

    if not INVENTORY.exists():
        sys.exit("data/inventory.json missing — run scripts/inventory.py first")
    inventory = json.loads(INVENTORY.read_text())

    months = store.months_present("docs-en")
    if not months:
        sys.exit("data/raw is empty — run scripts/fetch.py backfill first")

    docs = build_docs(months, inventory)
    payload = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "months": months,
        "period": plausible.month_range(months[-1]),
        "docs": docs,
    }

    total = sum(r[1] for r in docs["rows"])
    untrafficked = sum(1 for r in docs["rows"] if r[1] == 0)
    print(f"docs pages: {len(docs['rows']):,} "
          f"({untrafficked} with no traffic in {months[-1]})")
    print(f"matched pageviews: {total:,}  "
          f"stale/unmatched: {docs['staleTotal']:,} across {docs['staleUrls']:,} urls "
          f"({docs['staleTotal'] / max(total + docs['staleTotal'], 1):.1%})")
    for kind, views in sorted(docs["staleKinds"].items(), key=lambda kv: -kv[1]):
        print(f"    {kind:14} {docs['staleKindUrls'][kind]:>6,} urls {views:>8,} pv")
    print(f"months: {len(months)} ({months[0]} .. {months[-1]})")
    run_checks(months, inventory, args.check)

    if args.check:
        print("\nremoved pages with no redirect:")
        for url, pv, _ in docs["unforwarded"][:10]:
            print(f"  {pv:>7,}  {url}")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    OUT.write_text("window.DATA = " + body + ";\n")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
