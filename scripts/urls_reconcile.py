#!/usr/bin/env python3
"""One-off cache reconciliation: trim data/urls_list.txt to item-backed URLs.

detect_changes.R only flags URLs that are absent from the cache. URLs that
entered the cache without ever producing a catalog item (the 90
parenthesized files added after the Feb 2026 build, plus ~2k others from a
post-build refresh) are therefore invisible to change detection forever.

Rewriting the cache to just the item-backed subset makes the next
detection run treat every never-built URL as new and process it through
the normal incremental path.

Usage:
    python scripts/urls_reconcile.py            # dry-run: report only
    python scripts/urls_reconcile.py --apply    # rewrite data/urls_list.txt
"""

import argparse
import csv
import sys

from stac_utils import url_to_item_id

URLS_FILE = "data/urls_list.txt"
VALIDATION_CSV = "data/stac_item_validation.csv"


def main():
    parser = argparse.ArgumentParser(
        description="Trim urls_list.txt to URLs backed by a catalog item"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Rewrite data/urls_list.txt (default: dry-run report)"
    )
    args = parser.parse_args()

    with open(URLS_FILE) as f:
        urls = [line for line in f.read().splitlines() if line.strip()]

    with open(VALIDATION_CSV, newline="") as f:
        item_ids = {row["item_id"] for row in csv.DictReader(f)}

    backed = [url for url in urls if url_to_item_id(url) in item_ids]
    orphaned = [url for url in urls if url_to_item_id(url) not in item_ids]

    print(f"URLs in cache:      {len(urls)}")
    print(f"Item-backed:        {len(backed)}")
    print(f"Never built:        {len(orphaned)}")
    for url in orphaned[:10]:
        print(f"  {url}")
    if len(orphaned) > 10:
        print(f"  ... and {len(orphaned) - 10} more")

    if not orphaned:
        print("Cache already reconciled - nothing to do")
        return 0

    if args.apply:
        with open(URLS_FILE, "w") as f:
            f.write("\n".join(backed) + "\n")
        print(f"Rewrote {URLS_FILE} with {len(backed)} item-backed URLs")
        print("Next detect_changes.R run will re-flag the never-built URLs as new")
    else:
        print("Dry-run - pass --apply to rewrite data/urls_list.txt")

    return 0


if __name__ == "__main__":
    sys.exit(main())
