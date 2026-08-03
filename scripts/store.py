"""Byte-stable ndjson store under data/raw/.

One file per (kind, month), rows sorted by key and written with sorted JSON keys
and compact separators, so re-running a fetch on unchanged data produces
identical bytes and git diffs stay empty. Writes are atomic: a crash mid-write
must not leave a truncated file that silently under-reports next run.
"""

import json
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def path_for(kind, month=None):
    name = f"{kind}-{month}.ndjson" if month else f"{kind}.ndjson"
    return RAW_DIR / name


def write(kind, rows, month=None, key="p"):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = path_for(kind, month)
    tmp = target.with_suffix(".ndjson.tmp")
    with tmp.open("w") as f:
        for row in sorted(rows, key=lambda r: str(r[key])):
            f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    tmp.replace(target)
    return target


def read(kind, month=None):
    target = path_for(kind, month)
    if not target.exists():
        return []
    with target.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def months_present(kind):
    prefix = f"{kind}-"
    return sorted(
        p.name[len(prefix):-len(".ndjson")]
        for p in RAW_DIR.glob(f"{prefix}*.ndjson")
        if not p.name.endswith(".tmp")
    )
