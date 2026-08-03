#!/usr/bin/env python3
"""Fetch Godot's Plausible stats into data/raw/.

    python3 scripts/fetch.py probe            # what this instance supports
    python3 scripts/fetch.py backfill         # every complete month with data
    python3 scripts/fetch.py incremental      # only months not yet stored

Only this script needs PLAUSIBLE_API_KEY. Everything downstream reads data/raw/,
so aggregation and the site work for anyone who clones the repo.

What is stored, and why not simply everything: the docs site serves ~87k distinct
URLs a month across 15 languages and a dozen versions. Keeping that per-page for
every month would put ~85 MB of mostly-noise in git. So per-page detail is stored
for English only (the source of truth for editorial decisions), and the other
languages are rolled up per path — enough to show that a page carries weight in
translation without shipping fifteen parallel tables.
"""

import argparse
import collections
import re
import sys

import plausible
import store

# History starts 2025-02; 2025-01 holds only a few days of tracking (22k
# pageviews against 1.9M the month after) and would render as a false cliff.
FIRST_MONTH = "2025-02"

# Per-page detail is English-only; see the module docstring.
EN_PREFIX = "/en/"

# A docs URL is /<lang>/<version>/<rest>. Anything else under the docs hostname
# (search pages, asset URLs, stray redirects) has no source file behind it and
# is dropped at aggregation time by the inventory join.
DOCS_PATH = re.compile(r"^/([a-z]{2}(?:-[a-z]{2})?)/([^/]+)/(.*)$")

# Which version slug means "the current docs" — asked by exclusion, because the
# languages do not agree on the answer. English has `stable` (the released
# branch) and `latest` (master), and both are English-only: `/xx/latest/` drew
# 769 views in one language and single digits of scanner noise in the rest
# across 2026-07. The translations have neither. The edition their own language
# switcher points at is `4.x`, which carried 87% of all translated reading that
# month against 1,552 views for `stable`. What every language does share is that
# a *pinned release* is spelled as a number — 4.5, 3.6 — so that is what gets
# excluded, and the rule survives the rename to 5.x that a hardcoded slug would
# not.
PINNED_VERSION = re.compile(r"^\d[\d.]*$")


def fetch_site_daily(client, first, last):
    """Daily site totals per hostname.

    Visitor counts are stored alongside pageviews here and nowhere else. Nothing
    on the page uses them; they are kept so the README's claim that per-visitor
    figures are unusable stays checkable against stored data rather than being
    something a reader has to take on trust.
    """
    rows = []
    for tag, host in (("docs", plausible.DOCS_HOST), ("www", plausible.WWW_HOST)):
        result = client.query(
            metrics=["pageviews", "visitors", "visits"],
            date_range=[f"{first}-01", last],
            dimensions=["time:day"],
            filters=plausible.host_filter(host),
            pagination={"limit": plausible.MAX_LIMIT},
        )
        for row in result.get("results", []):
            pv, visitors, visits = row["metrics"]
            rows.append({"d": row["dimensions"][0][:10], "h": tag,
                         "pv": pv, "v": visitors, "vis": visits})
    store.write("site-daily", rows, key="d")
    print(f"  site-daily: {len(rows)} rows")


def fetch_docs_month(client, month, full):
    """English per-page detail, plus (on `full` months) a translation roll-up.

    `full` pages the entire docs hostname — ~87k rows, nine requests — and is
    only worth it for the month the report actually displays. Older months fetch
    the English subset directly, which is one request in ten.
    """
    date_range = plausible.month_range(month)
    base = plausible.host_filter(plausible.DOCS_HOST)
    filters = base if full else base + [["contains", "event:page", [EN_PREFIX]]]

    seen = 0

    def tick(n):
        nonlocal seen
        seen = n
        print(f"\r    {month}: {n:,} rows", end="", file=sys.stderr)

    rows = list(client.paginate(metrics=["pageviews"], date_range=date_range,
                                dimensions=["event:page"], filters=filters,
                                progress=tick))
    if seen:
        print("", file=sys.stderr)

    english = []
    # Two counters per path, not one. Collapsing every version onto the page it
    # mirrors is right for the path and wrong for the total: the English figure
    # a translation count gets compared against is /en/stable/ alone, so a
    # numerator that also carried /ja/4.5/ and /ja/3.6/ overstated every
    # translation share. `c` is the like-for-like number the report divides by;
    # `pv` keeps every version, which is worth knowing on its own.
    i18n_all, i18n_current = collections.Counter(), collections.Counter()
    for row in rows:
        path, pv = row["dimensions"][0], row["metrics"][0]
        if path.startswith(EN_PREFIX):
            english.append({"p": path, "pv": pv})
            continue
        if not full:
            continue
        m = DOCS_PATH.match(path)
        if m and m.group(1) != "en":
            i18n_all[m.group(3)] += pv
            if not PINNED_VERSION.match(m.group(2)):
                i18n_current[m.group(3)] += pv

    store.write("docs-en", english, month=month)
    print(f"  docs-en {month}: {len(english):,} pages, "
          f"{sum(r['pv'] for r in english):,} pageviews")
    if full:
        store.write("docs-i18n",
                    [{"p": p, "pv": pv, "c": i18n_current[p]}
                     for p, pv in i18n_all.items()], month=month)
        current = sum(i18n_current.values())
        print(f"  docs-i18n {month}: {len(i18n_all):,} paths, "
              f"{current:,} pageviews on the current docs, "
              f"{sum(i18n_all.values()) - current:,} on pinned releases")


def fetch_www_month(client, month):
    date_range = plausible.month_range(month)
    rows = list(client.paginate(
        metrics=["pageviews"], date_range=date_range, dimensions=["event:page"],
        filters=plausible.host_filter(plausible.WWW_HOST)))
    store.write("www", [{"p": r["dimensions"][0], "pv": r["metrics"][0]}
                        for r in rows], month=month)
    print(f"  www {month}: {len(rows):,} pages, "
          f"{sum(r['metrics'][0] for r in rows):,} pageviews")


def fetch_outbound_month(client, month):
    """Outbound-link and file-download clicks, keyed by target URL.

    The download buttons on godotengine.org point at downloads.godotengine.org
    with version and platform in the query string, so this breakdown is the only
    view of what people actually download — the download itself happens on a
    host that Plausible never sees.
    """
    date_range = plausible.month_range(month)
    rows = list(client.paginate(
        metrics=["events"], date_range=date_range, dimensions=["event:props:url"],
        filters=plausible.host_filter(plausible.WWW_HOST)))
    kept = [{"p": r["dimensions"][0], "n": r["metrics"][0]}
            for r in rows if r["dimensions"][0] not in ("(none)", "")]
    store.write("outbound", kept, month=month)
    print(f"  outbound {month}: {len(kept):,} urls, "
          f"{sum(r['n'] for r in kept):,} clicks")


def fetch_os_month(client, month):
    """OS mix by pageviews, not visitors — visitor counts are broken after
    2025-10-01 (see README), pageviews are not."""
    date_range = plausible.month_range(month)
    rows = list(client.paginate(
        metrics=["pageviews"], date_range=date_range, dimensions=["visit:os"],
        filters=plausible.host_filter(plausible.WWW_HOST)))
    store.write("os", [{"p": r["dimensions"][0], "pv": r["metrics"][0]}
                       for r in rows], month=month)
    print(f"  os {month}: {len(rows)} systems")


def available_months(client):
    """Complete months that actually carry data, oldest first."""
    last = plausible.last_complete_month()
    result = client.query(metrics=["pageviews"],
                          date_range=[f"{FIRST_MONTH}-01",
                                      plausible.month_range(last)[1]],
                          dimensions=["time:month"],
                          pagination={"limit": 200})
    return [r["dimensions"][0][:7] for r in result.get("results", [])
            if r["metrics"][0] > 0]


def run(months, newest, client):
    for month in months:
        full = month == newest
        fetch_docs_month(client, month, full=full)
        fetch_www_month(client, month)
        fetch_outbound_month(client, month)
        fetch_os_month(client, month)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["probe", "backfill", "incremental"])
    ap.add_argument("--months", type=int, default=13,
                    help="how many trailing complete months to keep (default 13)")
    args = ap.parse_args()

    client = plausible.Client()

    if args.command == "probe":
        caps = client.capabilities()
        print("metrics:")
        for name, ok in caps["metrics"].items():
            print(f"  {'yes' if ok else ' no'}  {name}")
        print("dimensions:")
        for name, ok in caps["dimensions"].items():
            print(f"  {'yes' if ok else ' no'}  {name}")
        months = available_months(client)
        print(f"\nmonths with data: {len(months)} ({months[0]} .. {months[-1]})")
        return

    months = available_months(client)
    if not months:
        sys.exit("no months with data — check the site id")
    wanted = months[-args.months:]
    newest = wanted[-1]

    if args.command == "incremental":
        have = set(store.months_present("docs-en"))
        # The newest month is always refetched: it is the only one carrying the
        # translation roll-up, and it may have been stored while still partial.
        todo = [m for m in wanted if m not in have or m == newest]
        if not todo:
            print("nothing to fetch")
            return
    else:
        todo = wanted

    print(f"fetching {len(todo)} month(s): {todo[0]} .. {todo[-1]}")
    fetch_site_daily(client, FIRST_MONTH, plausible.month_range(newest)[1])
    run(todo, newest, client)


if __name__ == "__main__":
    main()
