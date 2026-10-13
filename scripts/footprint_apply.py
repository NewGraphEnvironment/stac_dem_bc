#!/usr/bin/env python3
"""Rewrite every published item with its footprint and lidarbc: fields (#2, #55).

One pass, in place, through scripts/item_rewrite.py -- the third caller of that
harness after item_backfill.py (#31) and item_migrate.py (#34). The edit is
item_fields.item_fields_apply, the same function item_create.py uses, so a
rewritten item and one built monthly cannot differ in what they carry.

Per item, from data/footprints.csv:
  geometry, bbox     the footprint (BCGS cell minus no-data; else valid data)
  proj:geometry      the same, in the item's own CRS (proj:epsg, else proj:wkt2)
  assets.dem         raster:bands[0].statistics.valid_percent; file:checksum and
                     file:size where the whole file was read
  properties         lidarbc:delivery; datetime_unknown -> lidarbc:datetime_unknown
  stac_extensions    raster, file, lidarbc as the fields require

WHAT IT DOES NOT TOUCH: ids, hrefs, links, the collection id, the dsm asset.
No pystac round trip (json.load -> edit -> json.dump), for the reason
item_migrate.py gives.

An item with no usable footprint row keeps its published geometry; it is never
regenerated here. Three kinds, counted separately in the report:
  no source    published, but its URL is no longer in data/urls_list.txt (44
               at #2: deleted upstream, pruning is #28). lidarbc edits only.
  refused      the row says empty or crs_mismatch (two tiles declare UTM 14).
  missing      a listed URL with no row. Refused up front unless --limit:
               the cache must cover data/urls_list.txt before a full run.

Usage:
    python scripts/footprint_apply.py --limit 20 --dry-run
    python scripts/footprint_apply.py --limit 200 --verify 20
    python scripts/footprint_apply.py --verify 40            # the whole rewrite
"""

import argparse
import collections
import json
import logging
import os
import sys
import threading

from footprint import FOOTPRINT_METHODS, item_geometry
from footprint_extract import CACHE, cache_audit, cache_load, urls_read
from item_fields import item_faults, item_fields_apply
from item_rewrite import (
    ERR_EDIT,
    ERR_FETCH,
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
from stac_utils import ASSET_DEM, get_output_dir

logger = logging.getLogger(__name__)

MANIFEST = "data/footprint_done.txt"
ERRORS_LOG = "data/footprint_apply_errors.txt"
# Stamped into the manifest and checked on read: data/backfill_done.txt and
# data/migrate_done.txt each list ~100k ids, and reading either as this
# migration's ledger would skip them all and exit 0.
MIGRATION = "2-footprint"
URLS_LIST = "data/urls_list.txt"


def dem_url(item: dict) -> str:
    """The source URL as data/ spells it: hrefs are published percent-encoded (#25)."""
    return item["assets"][ASSET_DEM]["href"].replace("%20", " ")


class Tally:
    """Why an item kept its geometry, counted across worker threads."""

    def __init__(self, listed: set, footprints: dict):
        self.listed, self.footprints = listed, footprints
        self.counts = collections.Counter()
        self._lock = threading.Lock()

    def kind(self, url: str) -> str:
        row = self.footprints.get(url)
        if row is not None:
            return "footprint" if row["method"] in FOOTPRINT_METHODS else f"refused:{row['method']}"
        return "missing" if url in self.listed else "no source"

    def add(self, url: str):
        with self._lock:
            self.counts[self.kind(url)] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Rewrite published items with footprints and lidarbc: fields")
    ap.add_argument("--collection", default=None, help="Local collection.json (default: fetch the published one)")
    ap.add_argument("--footprints", default=CACHE)
    ap.add_argument("--out-dir", default=None, help="Default: $STAC_OUTPUT_DIR")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--errors-log", default=ERRORS_LOG)
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N items (rehearsal only; suppresses the coverage and completeness checks)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    ap.add_argument("--verify", type=int, default=0,
                    help="After the run, deepdiff N rewritten items against published")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if args.limit is not None and args.limit < 0:
        ap.error("--limit must be >= 0")

    out_dir = args.out_dir or get_output_dir(test_only=False)
    os.makedirs(out_dir, exist_ok=True)

    footprints = cache_load(args.footprints)
    listed = set(urls_read(URLS_LIST))
    uncovered = listed - set(footprints)
    if uncovered and args.limit is None:
        # A full run against an incomplete cache would publish those items with
        # their old geometry and record them as done, and nothing would ever
        # revisit them. Finish the extract first.
        logger.error("%d listed URL(s) have no footprint row, e.g. %s. Run "
                     "scripts/footprint_extract.py to completion first.",
                     len(uncovered), sorted(uncovered)[:2])
        return 1
    faults = cache_audit(footprints)
    if faults and args.limit is None:
        # The guards that refuse a bad footprint arrived after the first full
        # extraction started; this checks what it wrote before publishing it.
        for kind, urls in faults.items():
            logger.error("Footprint cache FAULT %s: %d, e.g. %s", kind, len(urls), urls[:2])
        return 1
    logger.info("Footprint rows: %d (covering %d of %d listed URLs)",
                len(footprints), len(listed) - len(uncovered), len(listed))

    published = published_item_ids(args.collection)
    logger.info("Published items: %d", len(published))
    done = manifest_load(args.manifest, MIGRATION)
    if done:
        logger.info("Manifest: %d items already done, skipping", len(done))
    todo = sorted(published - done)

    todo, staged = skip_already_staged(todo, out_dir)
    if staged and not args.dry_run:
        # A staged body was built this run by item_create (new, or rebuilt for
        # a pairing/footprint change), through the same item_fields_apply. It
        # counts as done only if applying the edit again changes nothing.
        wrong = []
        for item_id in staged:
            try:
                with open(os.path.join(out_dir, f"{item_id}.json")) as fh:
                    body = json.load(fh)
                if item_fields_apply(body, dem_url(body), footprints):
                    wrong.append(item_id)
            except (OSError, ValueError, KeyError) as e:
                wrong.append(f"{item_id} ({e})")
        if wrong:
            logger.error("%d staged item(s) are not in the rewritten shape: %s", len(wrong), wrong[:3])
            return 1
        logger.info("All %d staged item(s) are already in the rewritten shape", len(staged))

    if args.limit is not None:
        todo = todo[: args.limit]
    logger.info("To process: %d items", len(todo))

    tally = Tally(listed, footprints)

    def edit(item_id: str, item: dict) -> list:
        url = dem_url(item)
        tally.add(url)
        return item_fields_apply(item, url, footprints)

    def expect(published_item: dict, rewritten: dict) -> list:
        """The intent, separately from the prediction (see item_rewrite.verify_rewrite)."""
        problems = list(item_faults(rewritten))
        url = dem_url(rewritten)
        row = footprints.get(url)
        if row and row["method"] in FOOTPRINT_METHODS:
            name = url.rsplit("/", 1)[1]
            geom, bbox = item_geometry(row["footprint_wkt"], name)
            if rewritten.get("geometry") != geom:
                problems.append("geometry is not the footprint")
            if rewritten.get("bbox") != bbox:
                problems.append("bbox is not the footprint's bounds")
        elif rewritten.get("geometry") != published_item.get("geometry"):
            problems.append("geometry changed with no footprint row")
        return problems

    if args.dry_run:
        logger.info("Dry run - inspecting %d items without writing", min(len(todo), 20))
        for item_id in todo[:20]:
            item = item_fetch(item_id)
            logger.info("  %s -> %s", item_id[:60], edit(item_id, item) or "unchanged")
        return 0

    manifest_fh = manifest_open(args.manifest, MIGRATION)
    errors_fh = open(args.errors_log, "w")
    try:
        counts, errors = run_rewrite(todo, edit, out_dir, manifest_fh, errors_fh,
                                     workers=args.workers, desc="Rewriting")
    finally:
        manifest_fh.close()
        errors_fh.close()

    logger.info("written %d | unchanged %d | error %d", counts["written"], counts["unchanged"], counts["error"])
    logger.info("geometry source: %s", dict(sorted(tally.counts.items())))

    processed = sum(counts.values())
    tolerable = error_tolerable(counts["error"], processed,
                                population=0 if args.limit is not None else len(published))
    if counts["error"]:
        rate = counts["error"] / processed if processed else 0.0
        logger.warning("error rate %.5f (%d/%d); tolerance %.5f / %d abs -> %s", rate, counts["error"],
                       processed, ERROR_RATE_MAX, ERROR_ABS_MAX, "ACCEPTED" if tolerable else "EXCEEDED")

    if args.verify:
        sample = [i for i in todo if os.path.exists(os.path.join(out_dir, f"{i}.json"))][: args.verify]
        logger.info("Verifying %d rewritten items against published...", len(sample))
        failures, checked = verify_rewrite(sample, out_dir, edit, expect)
        if failures:
            logger.error("VERIFY FAILED on %d of %d items", failures, checked)
            return 1
        logger.info("Verify passed on %d items" if checked else
                    "Verify sampled 0 items (nothing rewritten this run); completeness is asserted below",
                    checked)

    # Completeness over the whole published population, from one producer (the
    # collection's item links), split by cause -- as item_migrate.py does, and
    # for the same reasons, which it records.
    if args.limit is None:
        rewritten = manifest_load(args.manifest, MIGRATION) | set(staged)
        missing = published - rewritten
        extra = rewritten - published
        if extra:
            logger.warning("%d rewritten id(s) are no longer published (upstream deletion; #28): %s",
                           len(extra), sorted(extra)[:3])
        transient = {i for i, o in errors.items() if o.startswith(ERR_FETCH)}
        deterministic = {i for i, o in errors.items() if o.startswith(ERR_EDIT)}
        unattempted = missing - transient - deterministic
        if unattempted:
            logger.error("INCOMPLETE: %d published, %d never attempted, e.g. %s",
                         len(published), len(unattempted), sorted(unattempted)[:3])
            return 1
        if deterministic:
            logger.error("%d item(s) cannot be rewritten without a human (re-running raises the "
                         "same error): %s; full list %s", len(deterministic),
                         [f"{i} -> {errors[i]}" for i in sorted(deterministic)[:3]], args.errors_log)
        if transient and tolerable:
            logger.warning("%d item(s) remain unrewritten, all transient fetch failures. RE-RUN to "
                           "pick them up; do not register until a run reports Complete.", len(transient))
        elif transient:
            logger.error("%d item(s) failed, ABOVE tolerance. This run's work is discarded, not "
                         "published.", len(transient))
        else:
            logger.info("Complete: all %d published items rewritten (%d in the manifest, %d staged)",
                        len(published), len(rewritten) - len(staged), len(staged))
    else:
        logger.warning("Partial run (--limit); coverage and completeness NOT asserted.")

    return 0 if tolerable else 1


if __name__ == "__main__":
    sys.exit(main())
