#!/usr/bin/env python3
"""Add the `dsm` asset to already-published items, and repair legacy URLs.

Rewrites published item JSONs in place rather than rebuilding them. The choice
is about fidelity, not just cost: 60,324 of 100,345 metadata-cache rows predate
spatial-metadata caching, and those are exactly the items originally built by
item_create.py's `rio_stac` fallback, which emits a different properties set
from `item_create_from_cache`. Rebuilding would quietly replace ~60k items
produced by one code path with output from another -- a change nobody reviewed,
and invisible in a single-item spot check. Fetching and editing changes only
what we mean to change.

Two edits, both idempotent:

  assets.dsm     added where scripts/dsm_pair.py found a pair
  href encoding  spaces percent-encoded in asset hrefs (#25's tail -- the code
                 was fixed but the published catalog never was, leaving 90 items
                 whose download link cannot even be formed into a request)

Deliberately NOT done here:
  - No round-trip through pystac.Item. json.load -> edit -> json.dump. pystac
    normalises key order, stac_version, links and self-href; on ~91k items that
    is a large unreviewed diff hiding the two-line one we intend.
  - collection.json is not touched. Item ids and hrefs do not change, so every
    link stays valid. Its own repair lives in scripts/collection_patch.py.

Resumability is not optional at this scale: an interrupted run otherwise leaves
an unknown subset carrying the asset, with no record of which. Completed ids are
appended to a manifest and skipped on restart.

Usage:
    python scripts/item_backfill.py --limit 20 --dry-run   # inspect, write nothing
    python scripts/item_backfill.py --limit 20             # small real batch
    python scripts/item_backfill.py                        # the whole backfill
    python scripts/item_backfill.py --verify 50            # diff sample vs published
"""

import argparse
import csv
import logging
import os
import sys

from dsm_pair import PAIRED, PAIRS_CSV
from item_rewrite import (
    ERROR_ABS_MAX,
    ERROR_RATE_MAX,
    error_tolerable,
    item_fetch,
    manifest_load,
    manifest_open,
    published_item_ids,
    run_rewrite,
    skip_already_staged,
    verify_rewrite,
)
from stac_utils import (
    ASSET_DEM,
    ASSET_DSM,
    PATH_S3,
    encode_url_for_gdal,
    get_output_dir,
    url_to_item_id,
)

logger = logging.getLogger(__name__)

MANIFEST = "data/backfill_done.txt"
ERRORS_LOG = "data/backfill_errors.txt"
# The manifest's owner. item_rewrite refuses to read a ledger stamped with a
# different name -- data/backfill_done.txt holds 98,040 ids against 102,460
# published, so handing it to another migration would skip 98,040 items and
# exit 0. See item_rewrite.manifest_load.
MIGRATION = "31-dsm-backfill"

# The bare-earth asset was keyed `image` when this script ran, and `dem` after
# #34. This is a recovery path, so it can meet either shape and must not assume
# the one that happened to exist when it was written -- reading only the current
# key would silently fall through to the default media type and downgrade every
# DSM it attached from COG to plain tiff. Order matters: current key first.
DEM_KEYS = (ASSET_DEM, "image")


def _dem_asset(item: dict) -> dict:
    """The bare-earth asset, whichever key this item spells it with."""
    assets = item.get("assets", {})
    for key in DEM_KEYS:
        if key in assets:
            return assets[key]
    return {}


def item_edit(item: dict, dsm_href: str | None) -> list[str]:
    """Apply the backfill edits in place. Returns the names of what changed.

    An empty list means the item is already correct and must not be rewritten --
    that is what keeps a re-run from re-uploading ~91k unchanged objects.
    """
    changed = []

    for key, asset in item.get("assets", {}).items():
        href = asset.get("href", "")
        fixed = encode_url_for_gdal(href)
        if fixed != href:
            asset["href"] = fixed
            changed.append(f"href:{key}")

    if dsm_href and ASSET_DSM not in item.get("assets", {}):
        dem = _dem_asset(item)
        item.setdefault("assets", {})[ASSET_DSM] = {
            "href": encode_url_for_gdal(dsm_href),
            # Inherited from the DEM asset already on this item -- the published
            # record IS the DEM's measured COG status, so no cache lookup and no
            # second network read is needed to honour that decision.
            "type": dem.get("type", "image/tiff; application=geotiff"),
            "roles": ["data"],
            "title": "Digital surface model",
        }
        changed.append("asset:dsm")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill dsm assets onto published items")
    parser.add_argument("--pairs-csv", default=PAIRS_CSV)
    parser.add_argument("--collection", default=None,
                        help="Local collection.json (default: fetch the published one)")
    parser.add_argument("--out-dir", default=None, help="Default: $STAC_OUTPUT_DIR")
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--errors-log", default=ERRORS_LOG)
    parser.add_argument("--limit", type=int, default=None, help="Process at most N items")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    parser.add_argument("--verify", type=int, default=0,
                        help="After the run, deepdiff N rewritten items against published")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    out_dir = args.out_dir or get_output_dir(test_only=False)
    os.makedirs(out_dir, exist_ok=True)

    with open(args.pairs_csv, newline="") as fh:
        dsm_lookup = {
            url_to_item_id(f"{PATH_S3}/{r['dem_key']}"): f"{PATH_S3}/{r['dsm_key']}"
            for r in csv.DictReader(fh)
            if r["status"] == PAIRED and r["dsm_key"]
        }
    logger.info("Loaded %d pairs", len(dsm_lookup))

    published = published_item_ids(args.collection)
    logger.info("Published items: %d", len(published))

    done = manifest_load(args.manifest, MIGRATION)
    if done:
        logger.info("Manifest: %d items already done, skipping", len(done))

    # Every published item is a candidate: those with a pair gain the asset,
    # and the 90 with raw-space hrefs need repair whether or not they pair.
    todo = sorted(published - done)

    # The same clobber guard item_migrate has, and for the same reason: CI
    # rebuilds items whose DSM pairing changed BEFORE this step, into this same
    # directory. Rewriting one would fetch the published body and overwrite the
    # fresher rebuild -- which, for a tile whose DSM was withdrawn, silently
    # restores the dsm asset the rebuild had just removed.
    todo, staged = skip_already_staged(todo, out_dir)
    if staged:
        logger.info("Skipping %d id(s) already staged in %s by an earlier step",
                    len(staged), out_dir)

    # `if args.limit:` reads 0 as "no limit" and would rewrite all 102,460 items
    # for someone who asked for none.
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.limit is not None:
        todo = todo[: args.limit]
    logger.info("To process: %d items (%d paired, %d published-but-unpaired)",
                len(todo),
                sum(1 for i in todo if i in dsm_lookup),
                sum(1 for i in todo if i not in dsm_lookup))

    def edit(item_id: str, item: dict) -> list[str]:
        return item_edit(item, dsm_lookup.get(item_id))

    if args.dry_run:
        logger.info("Dry run - inspecting %d items without writing", min(len(todo), 20))
        for item_id in todo[:20]:
            item = item_fetch(item_id)
            logger.info("  %s -> %s", item_id[:60], edit(item_id, item) or "unchanged")
        return 0

    manifest_fh = manifest_open(args.manifest, MIGRATION)
    # Per-item failures go to a FILE. tqdm writes its progress bar to stderr
    # with carriage returns, which overwrites interleaved log lines -- the first
    # CI run's 2 error messages never appeared in the log at all, so the failing
    # ids could not be identified from it.
    errors_fh = open(args.errors_log, "w")
    try:
        counts, errored = run_rewrite(todo, edit, out_dir, manifest_fh, errors_fh,
                                       workers=args.workers, desc="Backfilling")
    finally:
        manifest_fh.close()
        errors_fh.close()

    logger.info("written %d | unchanged %d | error %d",
                counts["written"], counts["unchanged"], counts["error"])

    processed = sum(counts.values())
    tolerable = error_tolerable(counts["error"], processed)
    if counts["error"]:
        rate = counts["error"] / processed if processed else 0.0
        logger.warning("error rate %.5f (%d/%d); tolerance %.5f / %d abs -> %s",
                       rate, counts["error"], processed, ERROR_RATE_MAX,
                       ERROR_ABS_MAX, "ACCEPTED" if tolerable else "EXCEEDED")
        logger.warning("failed ids written to %s - re-run to retry only those",
                       args.errors_log)

    if args.verify:
        sample = [i for i in todo
                  if os.path.exists(os.path.join(out_dir, f"{i}.json"))][: args.verify]
        logger.info("Verifying %d rewritten items against published...", len(sample))
        failures, checked = verify_rewrite(sample, out_dir, edit)
        if failures:
            logger.error("VERIFY FAILED on %d of %d items", failures, checked)
            return 1
        logger.info("Verify passed on %d items: the rewrite is exactly what "
                    "item_edit predicts", checked)

    # The same population statement item_migrate makes. Dropping --expect from
    # the CI audit removed the only thing that noticed a backfill which
    # processed a subset -- and a reused or truncated manifest is exactly how
    # that happens, silently and with exit 0.
    if args.limit is None:
        rewritten = manifest_load(args.manifest, MIGRATION) | set(staged)
        missing = published - rewritten
        unattempted = missing - errored
        if unattempted:
            logger.error("INCOMPLETE: %d published, %d never attempted",
                         len(published), len(unattempted))
            logger.error("  e.g. %s", sorted(unattempted)[:3])
            return 1
        if missing:
            logger.warning("%d item(s) remain, all of them this run's transient "
                           "failures; re-run to pick them up", len(missing))
        else:
            logger.info("Complete: all %d published items are covered",
                        len(published))
    else:
        logger.warning("Partial run (--limit); completeness NOT asserted.")

    return 0 if tolerable else 1


if __name__ == "__main__":
    sys.exit(main())
