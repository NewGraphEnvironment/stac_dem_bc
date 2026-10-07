#!/usr/bin/env python3
"""Rewrite the URL caches in data/ from `https:/` to `https://` (#51). One-shot.

ngr before 0.0.3 joined bucket URLs with fs::path(), which collapses the scheme's
`//` (ngr#38), so every cache built from a bucket walk spelled its URLs
`https:/host/...`. This rewrites those caches in the same commit that pins the
fixed ngr. A fixed ngr against an unrewritten cache would make change detection
report every URL as new and every cached one as deleted.

Kept after the migration for provenance; re-running it on migrated data changes
nothing.

Each file is rewritten line by line: only a leading `https:/` that is not
already `https://` changes, so every other byte is preserved. Before anything is
written, every file must pass these asserts:

  - the set of URLs is unchanged once both sides are spelled `https://`
  - no line starts with the single-slash form
  - txt files keep their line count
  - stac_geotiff_checks.csv is deduplicated to one row per URL. Its rows held
    the 2,245 albers tiles in both spellings, plus 10 exact repeats. Every row
    it drops must be byte-identical to the row it keeps, so no metadata is lost

Usage:
    python scripts/urls_scheme_migrate.py            # rewrite in place
    python scripts/urls_scheme_migrate.py --dry-run  # report, write nothing
"""

import argparse
import re
import sys

SINGLE = re.compile(r"^https:/(?=[^/])")

TXT_FILES = ["data/urls_list.txt", "data/urls_dsm.txt", "data/urls_deleted.txt"]
CSV_DEDUPE = "data/stac_geotiff_checks.csv"


def scheme_fix(line: str) -> str:
    return SINGLE.sub("https://", line, count=1)


def read_lines(path):
    # newline="" keeps each line's own terminator, so the rewrite is byte-exact
    with open(path, newline="") as f:
        return f.readlines()


def url_of(line: str, csv: bool) -> str:
    text = line.rstrip("\r\n")
    return text.split(",", 1)[0] if csv else text


def migrate_txt(path):
    old = read_lines(path)
    new = [scheme_fix(line) for line in old]
    assert len(new) == len(old), f"{path}: line count changed"
    assert {scheme_fix(url_of(x, False)) for x in old} == {url_of(x, False) for x in new}, \
        f"{path}: URL set changed"
    return old, new


def migrate_csv_dedupe(path):
    old = read_lines(path)
    header, rows = old[0], old[1:]
    assert header.startswith("url,"), f"{path}: first column is not url"
    kept, by_url, dropped = [], {}, 0
    for line in rows:
        fixed = scheme_fix(line)
        url = url_of(fixed, True)
        if url in by_url:
            # A drop is only lossless if every other column agrees
            assert by_url[url] == fixed, f"{path}: rows for {url} differ:\n{by_url[url]}{fixed}"
            dropped += 1
            continue
        by_url[url] = fixed
        kept.append(fixed)
    new = [header] + kept
    assert {scheme_fix(url_of(x, True)) for x in rows} == set(by_url), f"{path}: URL set changed"
    assert len(kept) == len(by_url) == len(rows) - dropped
    return old, new


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    plan = [(p, *migrate_txt(p)) for p in TXT_FILES]
    plan.append((CSV_DEDUPE, *migrate_csv_dedupe(CSV_DEDUPE)))

    # Every file is checked before any is written, so a failed assert leaves
    # data/ exactly as it was.
    for path, old, new in plan:
        single_left = sum(1 for line in new if SINGLE.match(line))
        assert single_left == 0, f"{path}: {single_left} single-slash lines remain"
        changed = sum(1 for a, b in zip(old, new) if a != b) if len(old) == len(new) else None
        print(f"{path}: {len(old)} -> {len(new)} lines"
              + (f", {changed} rewritten" if changed is not None else ""))

    if args.dry_run:
        print("dry run: nothing written")
        return 0
    for path, old, new in plan:
        if new != old:
            with open(path, "w", newline="") as f:
                f.writelines(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
