"""Minimal client for the Plausible Stats API v2.

Godot self-hosts Plausible at plausible.godot.foundation; both godotengine.org
and docs.godotengine.org report to one site (`data-domain="godotengine.org"`),
so they share a path namespace and are separated with an `event:hostname`
filter rather than by site.

This instance is an older build than Plausible Cloud. Notably absent:
`scroll_depth`, `time_on_page`, `conversion_rate` as metrics, and
`visit:entry_page` / `visit:exit_page` as dimensions (they 500). `capabilities()`
re-checks that at runtime so a server upgrade is noticed rather than silently
changing results.

Stdlib only: urllib posts JSON fine and the pipeline stays dependency-free.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_URL = "https://plausible.godot.foundation/api/v2/query"
DEFAULT_SITE = "godotengine.org"

# Hostname values that split the one Plausible site into the two properties
# this report treats separately. Anything else (readthedocs mirrors, translation
# proxies, scrapers echoing the tracker) is deliberately excluded — see
# `OTHER_HOSTS` in the README's caveats.
DOCS_HOST = "docs.godotengine.org"
WWW_HOST = "godotengine.org"

# The instance stopped counting unique visitors correctly on this date; see the
# README. Everything session-derived is unusable from here on, so the aggregator
# refuses to chart those metrics past it rather than relying on a caller to
# remember.
VISITOR_BREAK = "2025-10-01"

MAX_LIMIT = 10000  # server-side cap on pagination.limit


def load_env(path=None):
    """Read KEY=value lines from .env. Real environment variables win, so CI
    can inject the key without the file existing."""
    path = Path(path) if path else ROOT / ".env"
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class Client:
    def __init__(self, key=None, url=None, site=None):
        load_env()
        self.key = key or os.environ.get("PLAUSIBLE_API_KEY")
        if not self.key:
            sys.exit(
                "PLAUSIBLE_API_KEY is not set. Copy .env.example to .env and add a\n"
                "Plausible API key with read access to the godotengine.org site."
            )
        self.url = url or os.environ.get("PLAUSIBLE_URL", DEFAULT_URL)
        self.site = site or os.environ.get("PLAUSIBLE_SITE", DEFAULT_SITE)

    def query(self, *, metrics, date_range, dimensions=None, filters=None,
              pagination=None, order_by=None, retries=4):
        body = {"site_id": self.site, "metrics": metrics, "date_range": date_range}
        if dimensions:
            body["dimensions"] = dimensions
        if filters:
            body["filters"] = filters
        if pagination:
            body["pagination"] = pagination
        if order_by:
            body["order_by"] = order_by
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.key}",
                   "Content-Type": "application/json"}

        for attempt in range(retries):
            req = urllib.request.Request(self.url, data=payload, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:300]
                # 4xx other than rate-limiting is a bug in the query, not a
                # transient failure — retrying just burns time and quota.
                if e.code < 500 and e.code != 429:
                    raise RuntimeError(
                        f"Plausible {e.code} for {body!r}: {detail}") from None
                if attempt == retries - 1:
                    raise RuntimeError(
                        f"Plausible {e.code} after {retries} tries: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"Plausible unreachable: {e}") from None
            time.sleep(2 ** attempt)

    def paginate(self, *, metrics, date_range, dimensions, filters=None,
                 limit=MAX_LIMIT, progress=None):
        """Yield every result row, walking pagination.offset.

        The API returns no total, so the only end signal is a short page. A full
        final page is followed by one more request that comes back empty.
        """
        offset = 0
        while True:
            result = self.query(metrics=metrics, date_range=date_range,
                                dimensions=dimensions, filters=filters,
                                pagination={"limit": limit, "offset": offset})
            rows = result.get("results", [])
            yield from rows
            if progress and rows:
                progress(offset + len(rows))
            if len(rows) < limit:
                return
            offset += limit

    def capabilities(self):
        """Probe which metrics and dimensions this instance actually supports.

        Recorded in the output so a later server upgrade that adds scroll_depth
        (the engagement metric this report would prefer) is noticed.
        """
        caps = {"metrics": {}, "dimensions": {}}
        for metric in ["pageviews", "visitors", "visits", "events", "bounce_rate",
                       "visit_duration", "views_per_visit", "time_on_page",
                       "scroll_depth"]:
            try:
                self.query(metrics=[metric], date_range="7d")
                caps["metrics"][metric] = True
            except RuntimeError:
                caps["metrics"][metric] = False
        for dim in ["event:page", "event:hostname", "event:props:url",
                    "visit:source", "visit:os", "visit:country",
                    "visit:entry_page", "visit:exit_page", "time:day", "time:month"]:
            try:
                self.query(metrics=["pageviews"], date_range="7d",
                           dimensions=[dim], pagination={"limit": 1})
                caps["dimensions"][dim] = True
            except RuntimeError:
                caps["dimensions"][dim] = False
        return caps


def host_filter(hostname):
    return [["is", "event:hostname", [hostname]]]


def month_range(month):
    """('2026-07') -> ['2026-07-01', '2026-07-31'] — the API wants explicit dates."""
    year, mon = (int(x) for x in month.split("-"))
    if mon == 12:
        nxt = f"{year + 1}-01-01"
    else:
        nxt = f"{year}-{mon + 1:02d}-01"
    import datetime
    last = datetime.date.fromisoformat(nxt) - datetime.timedelta(days=1)
    return [f"{month}-01", last.isoformat()]


def months_between(first, last):
    """Inclusive list of 'YYYY-MM' strings."""
    out = []
    y, m = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    while (y, m) <= (ly, lm):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def last_complete_month(today=None):
    import datetime
    today = today or datetime.date.today()
    y, m = today.year, today.month
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}-{m:02d}"
