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


def stale_kind(removed_ts, window_start, redirected):
    """Why a trafficked URL has no page, which decides what its count means.

    Redirection comes first: if 404.rst forwards the URL, the reader arrived
    where they meant to and the pageview here is an artifact of the redirect
    running in the browser. Otherwise a page removed before the window was gone
    for all of it, so every view is a reader who hit a dead end — the only
    bucket anyone can act on. One removed during the window was readable for
    part of it. A URL that was never a page here is neither.
    """
    if redirected:
        return "redirected"
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


def i18n_key(rest):
    """Fold a translated path onto the key `build_docs` derives from a canonical
    English URL, which is `index.html` for the manual's landing page.

    A reader at `/ja/stable/` leaves an *empty* remainder, not one ending in
    "/", so the directory rule alone misses it — and that one path is the
    largest single key in the file (4% of all translated reading).
    """
    if not rest or rest.endswith("/"):
        rest += "index.html"
    return rest


def read_i18n(month, field="c"):
    """Yield (key, language, pageviews) for the translation roll-up.

    `field` picks the edition: "c" is the current docs in each language, the
    only figure comparable with the English side, which is `/en/stable/` alone.
    "pv" is every version, which is not a translation share but is a real number
    about how translated reading is spread.

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
    """"Q3 24". Short because eleven columns share the width of the page.

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
    # Later duplicate sources are unreachable — 404.rst stops at the first
    # match — so the first one wins here too.
    redirects = {}
    for source, destination in inventory.get("redirects", []):
        redirects.setdefault(source, destination)

    window_start = month_start_ts(newest)
    kinds, urls = collections.Counter(), collections.Counter()
    unforwarded = []
    for url, pv in stale.items():
        kind = stale_kind(removed.get(url), window_start,
                          redirect_for(url, redirects))
        kinds[kind] += pv
        urls[kind] += 1
        if kind in ("noRedirect", "removedDuring"):
            unforwarded.append([url, pv, removed[url]])
    unforwarded.sort(key=lambda r: -r[1])

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
        "unforwardedTotal": sum(r[1] for r in unforwarded),
        "unforwardedUrls": len(unforwarded),
        "staleTotal": sum(stale.values()),
        "staleUrls": len(stale),
        "staleKinds": dict(kinds),
        "staleKindUrls": dict(urls),
        "redirectCount": len(redirects),
        "windowStart": window_start,
        "matchedViews": sum(matched.values()),
        "i18nViews": sum(i18n.values()),
        # Translated reading outside /xx/stable/: excluded from every share on
        # the page, since the English side it is compared against is stable
        # only. Emitted so the exclusion is a number on the page rather than a
        # sentence about a number nobody can see.
        "i18nOtherVersions": sum(load_i18n(newest, "pv").values()) - sum(i18n.values()),
    }


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
