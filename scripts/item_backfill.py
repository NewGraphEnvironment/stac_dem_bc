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
import concurrent.futures
import csv
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from tqdm import tqdm

from dsm_pair import PAIRED, PAIRS_CSV
from stac_utils import (
    PATH_S3,
    PATH_S3_STAC,
    encode_url_for_gdal,
    get_output_dir,
    url_to_item_id,
)

logger = logging.getLogger(__name__)

MANIFEST = "data/backfill_done.txt"
ERRORS_LOG = "data/backfill_errors.txt"
# A strict zero-error gate on ~98k network requests is a guard that fails toward
# abort: transient failures are certain at that volume, not exceptional. The
# first CI run hit 2 errors out of 98,040 -- 0.002%, with the verify passing --
# and exiting non-zero on that discarded 16m37s of completed work and skipped
# the publish entirely. Gate on the RATE against a stated tolerance instead, and
# let the retry above absorb the ordinary case.
ERROR_RATE_MAX = 0.001   # 0.1%
ERROR_ABS_MAX = 200
COLLECTION_URL = f"{PATH_S3_STAC}/collection.json"


def published_item_ids(collection_path: str | None) -> set[str]:
    """Item ids the published collection actually links.

    An item that is paired but not yet published (the 4,420 newly-detected
    tiles) must not be fetched -- it does not exist yet, and item_create.py
    builds it with the dsm asset already attached. Reading the real link list
    rather than assuming keeps those two populations from overlapping.
    """
    if collection_path and os.path.exists(collection_path):
        with open(collection_path) as fh:
            collection = json.load(fh)
    else:
        logger.info("Fetching published collection from %s", COLLECTION_URL)
        with urllib.request.urlopen(COLLECTION_URL, timeout=120) as r:
            collection = json.load(r)

    ids = set()
    for link in collection.get("links", []):
        if link.get("rel") != "item":
            continue
        name = link["href"].rsplit("/", 1)[-1]
        if name.endswith(".json"):
            name = name[: -len(".json")]
        # Links are stored percent-encoded; item ids are not.
        ids.add(name.replace("%20", " "))
    return ids


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

    if dsm_href and "dsm" not in item.get("assets", {}):
        image = item.get("assets", {}).get("image", {})
        item.setdefault("assets", {})["dsm"] = {
            "href": encode_url_for_gdal(dsm_href),
            # Inherited from the DEM asset already on this item -- the published
            # record IS the DEM's measured COG status, so no cache lookup and no
            # second network read is needed to honour that decision.
            "type": image.get("type", "image/tiff; application=geotiff"),
            "roles": ["data"],
            "title": "Digital surface model",
        }
        changed.append("asset:dsm")

    return changed


def item_fetch(item_id: str) -> dict:
    """GET a published item JSON. Raises on any non-200 rather than returning {}."""
    url = f"{PATH_S3_STAC}/{encode_url_for_gdal(item_id)}.json"
    with urllib.request.urlopen(url, timeout=60) as r:
        if r.status != 200:
            raise RuntimeError(f"{url} -> HTTP {r.status}")
        return json.load(r)


def process_one(item_id: str, dsm_href: str | None, out_dir: str,
                attempts: int = 3) -> tuple[str, str]:
    """Returns (item_id, outcome) where outcome is written | unchanged | error:<msg>.

    Retries before giving up. Over ~98k requests a handful of transient failures
    is certain, not exceptional: the local run saw 34 and every one re-fetched
    fine on the next attempt, and CI saw 2. Retrying here is what keeps those
    from reaching the run's exit code at all.
    """
    last = ""
    for attempt in range(attempts):
        try:
            item = item_fetch(item_id)
            changed = item_edit(item, dsm_href)
            if not changed:
                return item_id, "unchanged"
            with open(os.path.join(out_dir, f"{item_id}.json"), "w") as fh:
                json.dump(item, fh)
            return item_id, "written"
        except Exception as e:  # noqa: BLE001 - reported per item, never fatal
            last = str(e)
            if attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))  # linear backoff
    return item_id, f"error:{last}"


def verify(sample_ids: list[str], out_dir: str, dsm_lookup: dict) -> int:
    """Assert the ONLY difference between published and rewritten is the intent.

    A spot check that just eyeballs one item cannot catch a systematic change
    to key order or a dropped property. deepdiff over a sample can, and it is
    already a project dependency.
    """
    from deepdiff import DeepDiff

    failures = 0
    for item_id in sample_ids:
        local_path = os.path.join(out_dir, f"{item_id}.json")
        if not os.path.exists(local_path):
            continue
        published = item_fetch(item_id)
        with open(local_path) as fh:
            rewritten = json.load(fh)

        diff = DeepDiff(published, rewritten, ignore_order=False)
        allowed = set()
        if "dsm" in rewritten.get("assets", {}) and "dsm" not in published.get("assets", {}):
            allowed.add("root['assets']['dsm']")
        added = set(diff.get("dictionary_item_added", []))
        changed_vals = dict(diff.get("values_changed", {}))
        # Only href values may differ, and only by space-encoding.
        bad_values = {
            k: v for k, v in changed_vals.items()
            if not (k.endswith("['href']")
                    and encode_url_for_gdal(v["old_value"]) == v["new_value"])
        }
        removed = diff.get("dictionary_item_removed", [])

        if (added - allowed) or bad_values or removed:
            failures += 1
            logger.error("UNEXPECTED DIFF %s: added=%s values=%s removed=%s",
                         item_id, added - allowed, bad_values, removed)
    return failures


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

    done = set()
    if os.path.exists(args.manifest):
        with open(args.manifest) as fh:
            done = {line.strip() for line in fh if line.strip()}
        logger.info("Manifest: %d items already done, skipping", len(done))

    # Every published item is a candidate: those with a pair gain the asset,
    # and the 90 with raw-space hrefs need repair whether or not they pair.
    todo = sorted(published - done)
    if args.limit:
        todo = todo[: args.limit]
    logger.info("To process: %d items (%d paired, %d published-but-unpaired)",
                len(todo),
                sum(1 for i in todo if i in dsm_lookup),
                sum(1 for i in todo if i not in dsm_lookup))

    if args.dry_run:
        logger.info("Dry run - inspecting %d items without writing", min(len(todo), 20))
        for item_id in todo[:20]:
            item = item_fetch(item_id)
            logger.info("  %s -> %s", item_id[:60], item_edit(item, dsm_lookup.get(item_id)) or "unchanged")
        return 0

    lock = threading.Lock()
    counts = {"written": 0, "unchanged": 0, "error": 0}
    manifest_fh = open(args.manifest, "a")
    # Per-item failures go to a FILE. tqdm writes its progress bar to stderr
    # with carriage returns, which overwrites interleaved log lines -- the first
    # CI run's 2 error messages never appeared in the log at all, so the failing
    # ids could not be identified from it.
    errors_fh = open(args.errors_log, "w")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(process_one, i, dsm_lookup.get(i), out_dir) for i in todo]
            for fut in tqdm(concurrent.futures.as_completed(futures),
                            total=len(futures), desc="Backfilling"):
                item_id, outcome = fut.result()
                with lock:
                    if outcome.startswith("error:"):
                        counts["error"] += 1
                        errors_fh.write(f"{item_id}\t{outcome}\n")
                        errors_fh.flush()
                    else:
                        counts[outcome] += 1
                        # Manifest records COMPLETION, so an interrupted run
                        # resumes without re-fetching what already succeeded.
                        manifest_fh.write(f"{item_id}\n")
                        manifest_fh.flush()
    finally:
        manifest_fh.close()
        errors_fh.close()

    logger.info("written %d | unchanged %d | error %d",
                counts["written"], counts["unchanged"], counts["error"])

    processed = sum(counts.values())
    rate = counts["error"] / processed if processed else 0.0
    tolerable = counts["error"] <= ERROR_ABS_MAX and rate <= ERROR_RATE_MAX
    if counts["error"]:
        logger.warning("error rate %.5f (%d/%d); tolerance %.5f / %d abs -> %s",
                       rate, counts["error"], processed, ERROR_RATE_MAX,
                       ERROR_ABS_MAX, "ACCEPTED" if tolerable else "EXCEEDED")
        logger.warning("failed ids written to %s - re-run to retry only those",
                       args.errors_log)

    if args.verify:
        sample = [i for i in todo if os.path.exists(os.path.join(out_dir, f"{i}.json"))]
        sample = sample[: args.verify]
        logger.info("Verifying %d rewritten items against published...", len(sample))
        failures = verify(sample, out_dir, dsm_lookup)
        if failures:
            logger.error("VERIFY FAILED on %d of %d items", failures, len(sample))
            return 1
        logger.info("Verify passed: the only differences are the intended ones")

    return 0 if tolerable else 1


if __name__ == "__main__":
    sys.exit(main())
