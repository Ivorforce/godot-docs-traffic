#!/usr/bin/env python3
"""Build the docs page inventory from the godotengine/godot-docs source tree.

    python3 scripts/inventory.py

Analytics can only report URLs that were visited. That leaves two questions it
can never answer, and both matter here:

  * Which pages exist but nobody reads? Only the source tree knows the
    denominator.
  * Which trafficked URLs are real pages at all? Roughly half of the /en/stable/
    URLs Plausible reports are stale bookmarks, moved paths and scanner noise
    ("/en/stable/.45", "/en/stable/2.x/...", timestamps). Joining against the
    tree is what separates the long tail from the junk.

The tree also supplies the only usable page-quality signals on this instance:
length and staleness. Plausible here has no scroll_depth and no time_on_page,
and its session metrics are broken (see METHOD.md), so "is this page working?"
cannot come from traffic data at all.

Output: data/inventory.json. Requires git; clones into .cache/ (gitignored).
"""

import csv
import datetime
import io
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "godot-docs"
OUT = ROOT / "data" / "inventory.json"

REPO = "https://github.com/godotengine/godot-docs.git"
# docs.godotengine.org/en/stable/ is Read the Docs' `stable` version, which
# builds from this branch — not master, which is the next release's docs and has
# a visibly different tree (engine_details/ exists there and not in stable).
BRANCH = "stable"
URL_PREFIX = "/en/stable/"

# 404.rst is the "page not found" template, not a page of the manual. Read the
# Docs serves it *in place of* a missing URL, and the analytics event records the
# URL the reader asked for, so its own traffic is structurally zero — the reading
# it stands for is already counted under "removed pages with no redirect". Its
# body is a raw HTML block, so its length is meaningless too. Left in, it was a
# bar in the section chart with no reading and no words, and the one page behind
# "all but one page was read at least once".
NOT_A_PAGE = frozenset({"404.rst"})

# Class reference pages are generated from the engine's own source XML and
# synced in bulk; they are not editorially maintained here, and their git dates
# reflect sync commits rather than anyone writing prose.
GENERATED_DIRS = ("classes",)

# The edit-churn window: eight calendar quarters, ending with the one HEAD falls
# in. Two years is set by what anyone can act on rather than by what git can
# tell us. Four years was tried and reaches back roughly eight minor releases,
# by which point a quiet stretch says nothing a maintainer can do anything
# about, and it costs twice the columns to read. It also changes which pages the
# grid puts in front of you: where a level has more children than fit, ranking
# by four years of churn fills half the list with pages last worked on in 2023 —
# in the class reference, only 13 of the top 25 are the same list.
#
# Four years is where the data itself would stop, if the data were the binding
# constraint: resolved through the rename chain, 93-99% of the lines changed
# each year since 2022 land on a page that still exists, while 2021 drops to 83%
# and 2020 to 66%, the Godot 4 restructure having removed or rewritten most of
# what came before. Two years sits comfortably inside that.
#
# Calendar periods rather than a rolling window ending at HEAD, even though that
# leaves the newest column partial. Both update every month; the difference is
# that rolling boundaries move under the reader, so a figure noted from one run
# covers a different span in the next and no two runs of the report can be
# compared. A closed calendar quarter never changes again.
CHURN_PERIOD_MONTHS = 3
CHURN_PERIODS = 8

# Every redirect the manual has is meant to be in this file; it is the source of
# truth. Read the Docs applies only the first ~50 of them, and 404.rst applies
# the rest in the browser, which is why they behave differently in our traffic
# data. An RTD redirect answers with a 301 and the old URL never renders, so it
# records no pageview; a redirect applied by 404.rst runs *on* the 404 page,
# where the analytics script has already fired against the old URL by the time
# the reader is forwarded. Only that second share leaves traffic on the old URL,
# and without it we would read "successfully forwarded" as "dead link".
#
# The file is written by hand and nothing in CI checks it against the pages that
# actually moved, so a merged, removed or renamed page regularly ends up with no
# row at all. Treat a missing row as "nobody wrote one", never as "this page did
# not move": the join from old URL to new is as complete as the file is, which is
# to say incomplete by an unknown amount. Pages whose readers land nowhere are
# the visible part of that gap, and are reported as removed with no redirect.
REDIRECTS = "_static/redirects.csv"


def run(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True,
                          capture_output=True).stdout


def ensure_clone():
    """Clone or update the docs source at BRANCH.

    Sparse but *not* blob-filtered, at about 740 MB on disk. The filter was the
    obvious economy — the working tree only ever needs .rst files and the
    redirects table — but it cannot serve line churn: `git log --numstat`
    needs both sides of every diff, so on a filtered clone it fetches blobs one
    request at a time, taking minutes over a year and failing outright over
    more. Unfiltered, the same walk over four years takes five seconds. Nothing
    of this leaves the machine that runs the pipeline: .cache/ is gitignored.

    History is kept in full either way — last-edit dates are the whole point of
    the staleness signal, and a shallow clone would collapse every file onto the
    clone date.
    """
    if not (CACHE / ".git").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPO} -> {CACHE} (~740 MB, a minute or two)",
              file=sys.stderr)
        run(["git", "clone", "--no-checkout", "--branch", BRANCH, REPO,
             str(CACHE)])
    else:
        # A cache built by an earlier revision is blob-filtered and cannot serve
        # the churn walk. Refilling it costs one long fetch, once, rather than
        # leaving the report to fail in a way that reads like a git bug.
        if run(["git", "config", "--get", "remote.origin.partialclonefilter"],
               cwd=CACHE, check=False).strip():
            print(f"{CACHE} is blob-filtered and cannot serve line churn; "
                  f"refetching in full (~740 MB, once)", file=sys.stderr)
            for key in ("remote.origin.partialclonefilter", "remote.origin.promisor"):
                run(["git", "config", "--unset", key], cwd=CACHE, check=False)
            run(["git", "fetch", "--refetch", "origin", BRANCH], cwd=CACHE)
        else:
            print(f"updating {CACHE}", file=sys.stderr)
            run(["git", "fetch", "origin", BRANCH], cwd=CACHE)
    # Set on every run, not only at clone time, so a cache made by an earlier
    # version of this script picks up newly needed paths instead of silently
    # missing them.
    run(["git", "sparse-checkout", "set", "--no-cone",
         "/**/*.rst", "/*.rst", "/" + REDIRECTS], cwd=CACHE)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=CACHE)
    return run(["git", "rev-parse", "HEAD"], cwd=CACHE).strip()


def url_for(rel):
    """Source path (relative, .rst) -> the URL Read the Docs serves it at."""
    if rel == "index.rst":
        return URL_PREFIX
    return URL_PREFIX + rel[: -len(".rst")] + ".html"


# A directive line, capturing its name when it has one. Hyperlink targets and
# comments (".. _label:", ".. just a note") match with no name.
DIRECTIVE = re.compile(r"^\s*\.\.[ \t]+(?:([A-Za-z0-9_-]+)::)?")
ROLE = re.compile(r":[a-z:]+:`([^`]*)`")
LITERAL = re.compile(r"``([^`]*)``")

# Directive bodies are markup and are skipped — except these, which hold
# ordinary prose that merely renders as a callout. godot-docs leans on them
# heavily: skipping them discarded ~54k words, a tenth of the manual.
PROSE_DIRECTIVES = frozenset({
    "note", "warning", "tip", "important", "seealso", "caution", "danger",
    "attention", "admonition", "hint", "error",
})

# Lines made only of these are section underlines or table rules.
RULE_CHARS = frozenset("=-~^\"'`*+#_|:.<> \t")


def is_word(token):
    """Does this whitespace-separated token carry a word?

    Counting raw tokens made table pipes, bullet markers and bare numbers into
    "words": 28,623 of them across the manual, and up to 56% of an individual
    page — a locale table read as 2.3x its real length. Requiring a letter
    rejects essentially only markup (the rejected set is 97% `|`, `-`, `=`, `*`,
    `#` and rule lines) while keeping domain words like Node2D and res://.
    `str.isalpha()` rather than a Latin range, so quoted non-Latin text in the
    i18n pages counts too.
    """
    return any(c.isalpha() for c in token)


def word_count(text):
    """Approximate prose words in one .rst source file.

    A size signal for comparing pages against each other, not a typographic
    measurement. Skips directive bodies (code blocks, tables, figures), literal
    blocks opened by a trailing "::", section underlines and rule lines; counts
    admonition prose; unwraps roles and inline literals.
    """
    words = 0
    skip_indent = None    # inside a skipped directive body or literal block
    literal_after = None  # indent of a paragraph that just opened a literal block

    for raw in text.splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())

        if skip_indent is not None:
            # Bodies are indented past their introducing line; blank lines
            # inside them do not end them.
            if not stripped or indent > skip_indent:
                continue
            skip_indent = None

        if literal_after is not None:
            if not stripped:
                continue
            if indent > literal_after:
                skip_indent = literal_after   # the literal block proper
                literal_after = None
                continue
            literal_after = None

        if not stripped:
            continue

        match = DIRECTIVE.match(raw)
        if match:
            if (match.group(1) or "").lower() not in PROSE_DIRECTIVES:
                skip_indent = indent
                continue
            # An admonition: count the text on its own line, then let its body
            # fall through and be counted like any other paragraph.
            stripped = raw[match.end():].strip()

        stripped = LITERAL.sub(r"\1", ROLE.sub(r"\1", stripped))
        if not stripped or set(stripped) <= RULE_CHARS:
            continue

        words += sum(1 for token in stripped.split() if is_word(token))

        # "Text follows::" introduces a literal block of indented code.
        if stripped.endswith("::"):
            literal_after = indent

    return words


TOCTREE = re.compile(r"^(\s*)\.\.\s+toctree::\s*$")


def toctree_entries(text, rel):
    """Ordered child documents of a page's toctree, as source-relative paths.

    Used for the getting-started funnel: reading the order from the tree means
    the chart follows a docs restructure instead of silently charting a path
    that no longer exists.
    """
    base = Path(rel).parent
    out, lines = [], text.splitlines()
    i = 0
    while i < len(lines):
        m = TOCTREE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        i += 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            cur = len(line) - len(line.lstrip())
            if cur <= indent:
                break
            entry = line.strip()
            i += 1
            if entry.startswith(":"):
                continue  # :maxdepth:, :hidden:, ...
            if "<" in entry and entry.endswith(">"):
                entry = entry[entry.index("<") + 1: -1]
            if entry.startswith("/"):
                target = entry.lstrip("/")
            else:
                target = str((base / entry)) if str(base) != "." else entry
            out.append(target.replace("\\", "/") + ".rst")
    return out


def last_edits():
    """{source path: unix timestamp of its most recent commit}.

    One log walk over the whole history rather than a `git log` per file: 1,600
    subprocess round-trips takes minutes, this takes seconds.
    """
    log = run(["git", "log", "--format=%n%ct", "--name-only", "--no-renames"],
              cwd=CACHE)
    stamps, current = {}, None
    for line in log.splitlines():
        line = line.strip()
        if not line:
            # Blank lines are unreliable separators: git emits one between the
            # timestamp and the file list as well as between commits. Identify
            # timestamps by shape instead.
            continue
        if line.isdigit():
            current = int(line)
        elif current and line.endswith(".rst") and line not in stamps:
            # Commits arrive newest-first, so the first sighting wins.
            stamps[line] = current
    return stamps


def removals():
    """{source path: unix timestamp of the commit that removed it}.

    Traffic to a URL with no source file means one of two very different
    things, and only history can tell them apart: a page that left the manual
    long ago and is still bookmarked, or a URL that was never a page here at
    all (Sphinx's search page, scanner noise, invented paths). Without the
    date, a page removed *during* the traffic window looks like a stale
    bookmark while its counts are really ordinary reading of a live page.

    Same single-walk shape as last_edits(), filtered to deletions.
    """
    return parse_removals(run(
        ["git", "log", "--diff-filter=D", "--format=%n%ct", "--name-only",
         "--no-renames"], cwd=CACHE))


# With -M git writes a rename as "old => new", compressing a shared prefix or
# suffix into "dir/{old => new}/file.rst". Both forms have to resolve to the new
# path, or a moved page's churn lands under a path that no longer exists.
RENAME_BRACED = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


def numstat_path(field):
    """The post-change path from the path column of a --numstat line."""
    if "=>" not in field:
        return field
    if "{" in field:
        field = RENAME_BRACED.sub(lambda m: m.group(2), field)
    else:
        field = field.split(" => ")[-1]
    # A rename into or out of a directory leaves one brace side empty, and with
    # it a doubled or trailing separator.
    return re.sub("/{2,}", "/", field.strip()).strip("/")


def churn_start(head_ts):
    """First day of the earliest calendar period in the churn window.

    Anchored on HEAD rather than on today, so the window does not drift between
    runs against the same clone, and snapped to a period boundary so the oldest
    column covers its whole span instead of starting mid-period. Only the newest
    column is ever partial, and it ends at HEAD rather than at the end of its
    period.
    """
    end = datetime.datetime.fromtimestamp(head_ts, datetime.timezone.utc).date()
    period = (end.year * 12 + (end.month - 1)) // CHURN_PERIOD_MONTHS
    total = (period - (CHURN_PERIODS - 1)) * CHURN_PERIOD_MONTHS
    return datetime.date(total // 12, total % 12 + 1, 1).isoformat()


def rename_map():
    """{source path: the path it was renamed to}, over the whole history.

    Churn is recorded against the path a file had at the time, so a page that
    moved has its history split between a path that no longer exists and the one
    it ended up at — and only the second half reaches the report. Over one year
    that stranded 1,094 lines; over four it is the difference between a usable
    window and a misleading one, the manual having moved whole directories.

    `_static/redirects.csv` cannot do this job, though it looks as if it should.
    It maps URLs rather than source paths, it is hand-maintained with nothing
    checking it (see REDIRECTS above), and where a page was deleted rather than
    moved there is no live page for its history to join to. Git recorded the
    moves itself, and its record is what this reads.
    """
    log = run(["git", "log", "--diff-filter=R", "--name-status", "-M",
               "--format=", "--", "*.rst"], cwd=CACHE)
    pairs = []
    for line in log.splitlines():
        # "R096\told\tnew"; the score after R is the similarity git measured.
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) == 3 and parts[1].endswith(".rst") and parts[2].endswith(".rst"):
            pairs.append((parts[1], parts[2]))
    # git log runs newest-first. Assigning oldest-first leaves the newest rename
    # of any path as the one stored, which is where following it forward ends up
    # anyway; `follow_renames` walks the rest of the chain.
    out = {}
    for old, new in reversed(pairs):
        out[old] = new
    return out


def follow_renames(path, renames):
    """Where a source path ended up, following renames forward."""
    seen = set()
    while path in renames and path not in seen:
        seen.add(path)   # a path renamed back onto itself would not terminate
        path = renames[path]
    return path


def churn_since(since, renames):
    """Lines changed per source file since `since`, an ISO date.

    {path: {"a": added, "d": deleted, "e": edits, "m": {"YYYY-MM": lines}}}

    Lines rather than commits, because a commit count cannot separate a typo
    from a rewrite: over the last year the busiest hand-written page took 13
    commits, while the single largest change was 1,822 lines in 3 of them. Added
    and deleted are kept apart because their ratio is the useful second signal —
    a section adding 13,641 lines against 3,679 deleted is being extended, one
    at 226 against 223 is being reworked in place, and a total alone reads the
    same either way.

    Rename detection is on twice over. `-M` keeps a move from reading as an
    entire text deleted and an identical text written by nobody, and `renames`
    then carries each diff forward to the path its file ended up at, so a page
    that moved keeps one history instead of two halves under two names.

    This is the only walk that needs file contents, which is why the clone above
    is not blob-filtered.
    """
    return parse_churn(run(["git", "log", f"--since={since}", "--numstat", "-M",
                            "--format=%x00%cI", "--", "*.rst"], cwd=CACHE),
                       renames)


def parse_churn(log, renames=None):
    out, month = {}, None
    for line in log.splitlines():
        # A commit header, marked with a NUL no path can contain. Blank-line
        # separators are not reliable here for the same reason as in
        # last_edits(): git emits them inside a commit as well as between two.
        if line.startswith("\x00"):
            month = line[1:8]
            continue
        parts = line.split("\t")
        if len(parts) != 3 or month is None:
            continue
        added, deleted, path = parts
        if added == "-" or deleted == "-":
            continue  # a binary diff; no .rst is one, but the format allows it
        path = follow_renames(numstat_path(path), renames or {})
        if not path.endswith(".rst"):
            continue
        added, deleted = int(added), int(deleted)
        if not added and not deleted:
            continue  # a pure rename: the page moved, its text did not change
        entry = out.setdefault(path, {"a": 0, "d": 0, "e": 0, "m": {}})
        entry["a"] += added
        entry["d"] += deleted
        entry["e"] += 1
        entry["m"][month] = entry["m"].get(month, 0) + added + deleted
    return out


def parse_redirects(text):
    """Ordered [(source, destination)] from _static/redirects.csv.

    Order is kept because 404.rst forwards to the first source that matches,
    and some sources are suffixes of others.
    """
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        source, destination = row[0].strip(), row[1].strip()
        if source == "source" or not source.startswith("/"):
            continue  # header, or a shape 404.rst could not match anyway
        rows.append((source, destination))
    return rows


def parse_removals(log):
    stamps, current = {}, None
    for line in log.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            current = int(line)
        elif current and line.endswith(".rst") and line not in stamps:
            # Newest-first, so the most recent removal of a path wins.
            stamps[line] = current
    return stamps


def main():
    head = ensure_clone()
    head_ts = int(run(["git", "log", "-1", "--format=%ct"], cwd=CACHE).strip())
    stamps = last_edits()
    removed = removals()
    since = churn_start(head_ts)
    renames = rename_map()
    print(f"reading line churn since {since} "
          f"({len(renames):,} renames followed)", file=sys.stderr)
    churn = churn_since(since, renames)

    pages, toctrees = {}, {}
    for path in sorted(CACHE.rglob("*.rst")):
        rel = str(path.relative_to(CACHE))
        if rel.startswith((".", "_")) or rel in NOT_A_PAGE:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        top = rel.split("/")[0]
        pages[url_for(rel)] = {
            "src": rel,
            "words": word_count(text),
            # Raw source lines, which is what churn is counted in — the churn
            # grid shades a period against the length of the thing it changed,
            # and words would put a count of prose under a count of diff lines.
            "lines": len(text.splitlines()),
            "edited": stamps.get(rel),
            "generated": top in GENERATED_DIRS,
        }
        entries = toctree_entries(text, rel)
        if entries:
            toctrees[rel] = entries

    # A path deleted and later restored is a live page, not a removal.
    live = {p["src"] for p in pages.values()}
    gone = {url_for(rel): ts for rel, ts in removed.items() if rel not in live}

    redirect_file = CACHE / REDIRECTS
    redirects = (parse_redirects(redirect_file.read_text(encoding="utf-8"))
                 if redirect_file.exists() else [])
    if not redirects:
        print(f"warning: no redirects read from {REDIRECTS}; every removed page "
              f"will look unforwarded", file=sys.stderr)

    missing = sum(1 for p in pages.values() if p["edited"] is None)
    generated = sum(1 for p in pages.values() if p["generated"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "branch": BRANCH,
        "commit": head,
        "urlPrefix": URL_PREFIX,
        "pages": pages,
        "churn": churn,
        "churnSince": since,
        "churnHead": head_ts,
        "redirects": redirects,
        "removed": gone,
        "toctrees": toctrees,
    }, sort_keys=True, separators=(",", ":")) + "\n")

    live_churn = {src: c for src, c in churn.items() if src in live}
    lines = sum(c["a"] + c["d"] for c in live_churn.values())
    print(f"{len(pages):,} pages ({generated:,} generated, "
          f"{len(pages) - generated:,} hand-written), "
          f"{len(toctrees):,} toctrees, {missing} without a git date, "
          f"{len(gone):,} removed pages remembered, "
          f"{len(redirects):,} redirects -> {OUT}")
    print(f"churn since {since}: {lines:,} lines across "
          f"{len(live_churn):,} pages still in the tree "
          f"({len(churn) - len(live_churn):,} on paths since removed)")


if __name__ == "__main__":
    main()
