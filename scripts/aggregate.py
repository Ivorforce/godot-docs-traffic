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

# How many stale inbound paths to list. They are useful to docs maintainers —
# each one is a URL people still reach for that no longer exists — but the tail
# is thousands of scanner hits, so it is cut and the cut is reported.
STALE_LIMIT = 25

DOWNLOAD_HOST = "downloads.godotengine.org"


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


def load_docs_month(month):
    """{canonical url: pageviews} for /en/stable/ in one month."""
    out = collections.Counter()
    for row in store.read("docs-en", month):
        if row["p"].startswith(URL_PREFIX):
            out[canonical(row["p"])] += row["pv"]
    return out


def load_i18n(month):
    """{path below /<lang>/<version>/: pageviews} summed over all non-English
    languages, so a page can show how much it is read in translation."""
    out = collections.Counter()
    for row in store.read("docs-i18n", month):
        rest = row["p"]
        if rest.endswith("/"):
            rest += "index.html"
        out[rest] += row["pv"]
    return out


def build_funnel(inventory):
    """The onboarding sequence, in the order the docs themselves present it.

    Read from the toctrees rather than hardcoded, so a docs restructure moves
    the chart instead of silently breaking it.
    """
    toctrees = inventory["toctrees"]
    order = []
    for entry in toctrees.get("index.rst", []):
        if not entry.startswith("getting_started/"):
            continue
        order.append(entry)
        order.extend(toctrees.get(entry, []))
    src_to_url = {p["src"]: url for url, p in inventory["pages"].items()}
    return [src_to_url[src] for src in order if src in src_to_url]


def parse_download(url):
    """('windows', '4.7.1') from a downloads.godotengine.org link, else None.

    The download itself happens on a host Plausible never sees, so these
    outbound clicks are the only record of what people actually take away.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.netloc != DOWNLOAD_HOST:
        return None
    q = urllib.parse.parse_qs(parts.query)
    platform = (q.get("platform") or [""])[0]
    version = (q.get("version") or [""])[0]
    flavor = (q.get("flavor") or [""])[0]
    slug = (q.get("slug") or [""])[0]
    if not platform or not version:
        return None
    # platform arrives as "windows.64", "macos.universal", "android.apk"
    family = platform.split(".")[0]
    mono = "mono" in slug
    return {"family": family, "platform": platform, "version": version,
            "flavor": flavor or "stable", "mono": mono}


def build_downloads(month):
    rows = store.read("outbound", month)
    by_family = collections.Counter()
    by_version = collections.Counter()
    by_mono = collections.Counter()
    total_outbound = 0
    download_clicks = 0
    for row in rows:
        total_outbound += row["n"]
        parsed = parse_download(row["p"])
        if not parsed:
            continue
        download_clicks += row["n"]
        by_family[parsed["family"]] += row["n"]
        by_version[parsed["version"]] += row["n"]
        by_mono["C# / .NET" if parsed["mono"] else "standard"] += row["n"]

    os_rows = store.read("os", month)
    os_mix = {r["p"]: r["pv"] for r in os_rows}
    # The OS a visitor browses with is not the OS they build for, but the gap
    # between the two mixes is exactly what makes the comparison interesting.
    os_family = collections.Counter()
    for name, pv in os_mix.items():
        key = {"GNU/Linux": "linux", "Ubuntu": "linux", "Windows": "windows",
               "Mac": "macos", "iOS": "ios", "iPadOS": "ios",
               "Android": "android", "Chrome OS": "linux",
               "HarmonyOS": "android", "Fire OS": "android"}.get(name)
        if key:
            os_family[key] += pv

    return {
        "byFamily": by_family.most_common(),
        "byVersion": by_version.most_common(12),
        "byFlavor": by_mono.most_common(),
        "osMix": os_family.most_common(),
        "downloadClicks": download_clicks,
        "outboundClicks": total_outbound,
    }


def build_docs(months, inventory):
    newest = months[-1]
    current = load_docs_month(newest)
    previous = load_docs_month(months[-2]) if len(months) > 1 else {}
    i18n = load_i18n(newest)
    pages = inventory["pages"]

    matched, stale = {}, {}
    for url, pv in current.items():
        (matched if url in pages else stale)[url] = pv

    paths, rows = [], []
    for url in sorted(pages):
        meta = pages[url]
        rest = url[len(URL_PREFIX):] or "index.html"
        paths.append(url)
        rows.append([
            len(paths) - 1,
            matched.get(url, 0),
            previous.get(url, 0),
            meta["words"],
            meta["edited"] or 0,
            i18n.get(rest, 0),
            1 if meta["generated"] else 0,
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

    stale_sorted = sorted(stale.items(), key=lambda kv: -kv[1])
    return {
        "branch": inventory["branch"],
        "commit": inventory["commit"][:9],
        "prefix": URL_PREFIX,
        "paths": paths,
        "rows": rows,
        "funnel": build_funnel(inventory),
        "sections": {k: [v.get(m, 0) for m in months] for k, v in sections.items()},
        "stale": [[u, pv] for u, pv in stale_sorted[:STALE_LIMIT]],
        "staleTotal": sum(stale.values()),
        "staleUrls": len(stale),
        "matchedViews": sum(matched.values()),
        "i18nViews": sum(i18n.values()),
    }


def build_www(months):
    newest = months[-1]
    rows = store.read("www", newest)
    total = sum(r["pv"] for r in rows)
    top = sorted(rows, key=lambda r: -r["pv"])[:60]
    monthly = []
    for month in months:
        monthly.append(sum(r["pv"] for r in store.read("www", month)))
    return {
        "total": total,
        "monthly": monthly,
        "top": [[r["p"], r["pv"]] for r in top],
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
        "www": build_www(months),
        "downloads": build_downloads(months[-1]),
    }

    total = sum(r[1] for r in docs["rows"])
    untrafficked = sum(1 for r in docs["rows"] if r[1] == 0)
    print(f"docs pages: {len(docs['rows']):,} "
          f"({untrafficked} with no traffic in {months[-1]})")
    print(f"matched pageviews: {total:,}  "
          f"stale/unmatched: {docs['staleTotal']:,} across {docs['staleUrls']:,} urls "
          f"({docs['staleTotal'] / max(total + docs['staleTotal'], 1):.1%})")
    print(f"months: {len(months)} ({months[0]} .. {months[-1]})")

    if args.check:
        print("\ntop stale inbound paths:")
        for url, pv in docs["stale"][:10]:
            print(f"  {pv:>7,}  {url}")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    OUT.write_text("window.DATA = " + body + ";\n")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
