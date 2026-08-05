#!/usr/bin/env python3
"""Record how much of each page is translated, into data/l10n.json.

    python3 scripts/l10n.py

The mirror of inventory.py, one repository over. inventory.py records what each
page costs from the docs source tree; this records what is already done from the
translation tree, so a translator can be told what to work on next rather than
only that a page carries weight in translation.

Coverage is *word-weighted*, not string-counted, which is why this parses the
files rather than taking a ready-made percentage. Short strings get translated
first, so a string count overstates progress everywhere and worst on the pages
where the remaining work is: tutorials/2d/index has four of its six strings
translated — 67% by string — and they are `2D`, `Rendering`, `Physics and
movement` and `Tools`, while the two that are left carry 62 of the page's 68
words. By words that page is 9% done.

The denominator has to be sphinx/templates/<page>.pot, which holds every source
string. It cannot be the language's own .po, which holds only what somebody has
already translated: ja/tutorials/3d/volumetric_fog.po carries one message where
the template carries 85, so a coverage figure taken from that file alone reads
100% for every page in every language.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from inventory import is_word

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "godot-docs-l10n"
OUT = ROOT / "data" / "l10n.json"

REPO = "https://github.com/godotengine/godot-docs-l10n.git"
# The repository's own default. Its `docs` submodule pins a godot-docs commit,
# which is why a handful of pages in the report have no template yet — see the
# `untemplated` count in the output.
BRANCH = "4.x"

PO_DIR = "sphinx/po"
POT_DIR = "sphinx/templates"


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          capture_output=True).stdout


def ensure_clone():
    """Clone or update the translation repository.

    Shallow and blob-filtered, unlike the docs clone. That clone cannot use
    either, because `git log --numstat` needs both sides of every diff and the
    last-edit dates need full history; here nothing is asked of the history at
    all — only the current state of the translations — so the cheap clone is
    the right one, at ~120 MB for thirteen languages.
    """
    if not (CACHE / ".git").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPO} -> {CACHE}", file=sys.stderr)
        run(["git", "clone", "--depth", "1", "--filter=blob:none",
             "--no-checkout", "--branch", BRANCH, REPO, str(CACHE)])
    else:
        print(f"updating {CACHE}", file=sys.stderr)
        run(["git", "fetch", "--depth", "1", "origin", BRANCH], cwd=CACHE)
    # Reapplied every run, so a cache built by an earlier revision picks up
    # newly needed paths instead of silently missing them.
    run(["git", "sparse-checkout", "set", "--no-cone",
         f"/{PO_DIR}/**", f"/{POT_DIR}/**"], cwd=CACHE)
    run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"], cwd=CACHE)
    return run(["git", "rev-parse", "HEAD"], cwd=CACHE).strip()


ENTRY = re.compile(r'(msgid|msgstr|msgctxt|msgid_plural)\s+"(.*)"$')
CONTINUATION = re.compile(r'^"(.*)"$')


def po_entries(path):
    """Yield (msgid, msgstr, fuzzy) for each live entry in a .po or .pot.

    Deliberately not polib: this is the only gettext the pipeline reads, the
    format it has to cope with is narrow, and a dependency that must be
    installed before the report builds is a worse trade than thirty lines.

    Strings are left escaped exactly as the file spells them. Both sides of the
    comparison come from the same generator and escape identically, so equality
    still holds, and word counting does not care.

    Obsolete entries (#~) and the header (empty msgid) are skipped; the header
    is conventionally flagged fuzzy and would otherwise poison the first real
    entry's flag. Sphinx's extraction emits no plurals and no msgctxt across all
    6,565 files here, so both fail loudly rather than being half-handled.
    """
    entry, fuzzy, field = {}, False, None
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("#~"):
            continue
        if not line.strip():
            if entry.get("msgid"):
                yield entry["msgid"], entry.get("msgstr", ""), fuzzy
            entry, fuzzy, field = {}, False, None
            continue
        if line.startswith("#,"):
            fuzzy = "fuzzy" in line
            continue
        if line.startswith("#"):
            continue
        m = ENTRY.match(line)
        if m:
            if m.group(1) in ("msgid_plural", "msgctxt"):
                raise ValueError(f"{m.group(1)} is not handled: {path}")
            field = m.group(1)
            entry[field] = m.group(2)
            continue
        m = CONTINUATION.match(line)
        if m and field:
            entry[field] += m.group(1)


def words(msgid):
    """Source words in one message, by the same rule inventory.py counts pages
    with, so the two sides of any ratio mean the same thing."""
    return sum(1 for token in msgid.split() if is_word(token))


def lang_code(directory):
    """`zh_CN` -> `zh-cn`. The translation repository names languages the way
    gettext does; the docs URLs, and so the traffic data, spell the same
    languages lowercase with a hyphen. One of the two has to give, and the
    traffic side has 46 codes from readers typing whatever they liked, so the
    normalisation belongs on this side where the set is known and small."""
    return directory.lower().replace("_", "-")


def build(head):
    pot_root, po_root = CACHE / POT_DIR, CACHE / PO_DIR
    langs = sorted(p.name for p in po_root.iterdir() if p.is_dir())

    pages = {}
    for pot in sorted(pot_root.rglob("*.pot")):
        rel = str(pot.relative_to(pot_root))[:-len(".pot")] + ".rst"
        entries = {mid: words(mid) for mid, _, _ in po_entries(pot)}
        pages[rel] = {"words": sum(entries.values()), "done": {}}
        pages[rel]["_ids"] = entries

    for directory in langs:
        root = po_root / directory / "LC_MESSAGES"
        for rel, page in pages.items():
            po = root / (rel[:-len(".rst")] + ".po")
            if not po.exists():
                continue
            translated = {mid for mid, mstr, fuzzy in po_entries(po)
                          if mstr and not fuzzy}
            done = sum(w for mid, w in page["_ids"].items() if mid in translated)
            page["done"][lang_code(directory)] = done

    for page in pages.values():
        del page["_ids"]

    return {
        "commit": head,
        "branch": BRANCH,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "languages": [lang_code(d) for d in langs],
        "pages": pages,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true",
                    help="use the cache as it is, without touching the network")
    args = ap.parse_args()

    if args.no_fetch:
        head = run(["git", "rev-parse", "HEAD"], cwd=CACHE).strip()
    else:
        head = ensure_clone()

    data = build(head)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))

    total = sum(p["words"] for p in data["pages"].values())
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    print(f"{len(data['pages'])} pages, {total:,} translatable words, "
          f"{len(data['languages'])} languages")
    for lang in data["languages"]:
        done = sum(p["done"].get(lang, 0) for p in data["pages"].values())
        full = sum(1 for p in data["pages"].values()
                   if p["words"] and p["done"].get(lang, 0) == p["words"])
        print(f"  {lang:<6} {done / total:5.0%} of words   "
              f"{full:>3} of {len(data['pages'])} pages complete")


if __name__ == "__main__":
    main()
