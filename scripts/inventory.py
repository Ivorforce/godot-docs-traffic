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
and its session metrics are broken (see README), so "is this page working?"
cannot come from traffic data at all.

Output: data/inventory.json. Requires git; clones into .cache/ (gitignored).
"""

import csv
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

    Blob-filtered and sparse: the repository is ~450 MB, almost all of it
    screenshots, and only .rst files and the redirects table are ever read.
    History is kept in full — last-edit dates are the whole point of the
    staleness signal, and a shallow clone would collapse every file onto the
    clone date.
    """
    if not (CACHE / ".git").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPO} -> {CACHE} (blob-filtered)", file=sys.stderr)
        run(["git", "clone", "--filter=blob:none", "--no-checkout",
             "--branch", BRANCH, REPO, str(CACHE)])
    else:
        print(f"updating {CACHE}", file=sys.stderr)
        run(["git", "fetch", "--filter=blob:none", "origin", BRANCH], cwd=CACHE)
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
    stamps = last_edits()
    removed = removals()

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
        "redirects": redirects,
        "removed": gone,
        "toctrees": toctrees,
    }, sort_keys=True, separators=(",", ":")) + "\n")

    print(f"{len(pages):,} pages ({generated:,} generated, "
          f"{len(pages) - generated:,} hand-written), "
          f"{len(toctrees):,} toctrees, {missing} without a git date, "
          f"{len(gone):,} removed pages remembered, "
          f"{len(redirects):,} redirects -> {OUT}")


if __name__ == "__main__":
    main()
