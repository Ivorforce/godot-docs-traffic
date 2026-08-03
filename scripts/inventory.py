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

# Class reference pages are generated from the engine's own source XML and
# synced in bulk; they are not editorially maintained here, and their git dates
# reflect sync commits rather than anyone writing prose.
GENERATED_DIRS = ("classes",)


def run(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True,
                          capture_output=True).stdout


def ensure_clone():
    """Clone or update the docs source at BRANCH.

    Blob-filtered and sparse: the repository is ~450 MB, almost all of it
    screenshots, and only .rst files are ever read. History is kept in full —
    last-edit dates are the whole point of the staleness signal, and a shallow
    clone would collapse every file onto the clone date.
    """
    if not (CACHE / ".git").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPO} -> {CACHE} (blob-filtered)", file=sys.stderr)
        run(["git", "clone", "--filter=blob:none", "--no-checkout",
             "--branch", BRANCH, REPO, str(CACHE)])
        run(["git", "sparse-checkout", "set", "--no-cone", "/**/*.rst", "/*.rst"],
            cwd=CACHE)
        run(["git", "checkout", BRANCH], cwd=CACHE)
    else:
        print(f"updating {CACHE}", file=sys.stderr)
        run(["git", "fetch", "--filter=blob:none", "origin", BRANCH], cwd=CACHE)
        run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=CACHE)
    return run(["git", "rev-parse", "HEAD"], cwd=CACHE).strip()


def url_for(rel):
    """Source path (relative, .rst) -> the URL Read the Docs serves it at."""
    if rel == "index.rst":
        return URL_PREFIX
    return URL_PREFIX + rel[: -len(".rst")] + ".html"


# Directive and role markup that would otherwise be counted as prose. Crude by
# design: the word count is a size signal for comparing pages, not a
# typographic measurement, and it only has to be consistent across pages.
DIRECTIVE = re.compile(r"^\s*\.\.\s")
ROLE = re.compile(r":[a-z:]+:`([^`]*)`")
LITERAL = re.compile(r"``([^`]*)``")


def word_count(text):
    words = 0
    in_block = False
    for line in text.splitlines():
        if DIRECTIVE.match(line):
            in_block = True
            continue
        if in_block:
            # Directive bodies stay indented; the first flush line ends them.
            if line.strip() and not line.startswith((" ", "\t")):
                in_block = False
            else:
                continue
        line = ROLE.sub(r"\1", line)
        line = LITERAL.sub(r"\1", line)
        stripped = line.strip()
        if not stripped or set(stripped) <= set("=-~^\"'`*+#_"):
            continue  # section underlines and rules
        words += len(stripped.split())
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


def main():
    head = ensure_clone()
    stamps = last_edits()

    pages, toctrees = {}, {}
    for path in sorted(CACHE.rglob("*.rst")):
        rel = str(path.relative_to(CACHE))
        if rel.startswith((".", "_")):
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

    missing = sum(1 for p in pages.values() if p["edited"] is None)
    generated = sum(1 for p in pages.values() if p["generated"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "branch": BRANCH,
        "commit": head,
        "urlPrefix": URL_PREFIX,
        "pages": pages,
        "toctrees": toctrees,
    }, sort_keys=True, separators=(",", ":")) + "\n")

    print(f"{len(pages):,} pages ({generated:,} generated, "
          f"{len(pages) - generated:,} hand-written), "
          f"{len(toctrees):,} toctrees, {missing} without a git date -> {OUT}")


if __name__ == "__main__":
    main()
