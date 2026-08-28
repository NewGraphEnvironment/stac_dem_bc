#!/usr/bin/env python3
"""Verify the assumption that a DSM shares its paired DEM's COG status and footprint.

Items carry the DSM's media type inherited from the DEM rather than measured,
because measuring all ~96k directly is a 15-20 hour network pass that cannot fit
the monthly runner. "Same flight, same delivery, same processing" is an argument;
this script is the evidence.

It draws a stratified sample across naming conventions and acquisition years --
not a head-of-file sample, which would only ever exercise block 082 and could not
reach the shapes the assumption is most likely to fail on -- reads each sampled
DSM over the network, and compares against the DEM metadata already cached in
data/stac_geotiff_checks.csv.

Thresholds, and why they differ:
  footprint (bounds, shape, epsg)  must agree EXACTLY. The two rasters are the
      same tile; a disagreement means the pairing matched the wrong file, which
      is a correctness bug, not a tolerance question.
  is_cog                           must agree on at least COG_AGREEMENT_MIN of
      the sample. A single delivery reprocessed one product and not the other is
      plausible; wholesale disagreement means the inheritance is wrong.

Exits non-zero when a threshold is missed, so this cannot pass by being ignored.

Usage:
    python scripts/dsm_verify.py                 # 500-tile stratified sample
    python scripts/dsm_verify.py --sample 100
    python scripts/dsm_verify.py --seed 7
"""

import argparse
import concurrent.futures
import csv
import json
import logging
import random
import sys
from collections import defaultdict

import pandas as pd
from tqdm import tqdm

from dsm_pair import PAIRED, PAIRS_CSV, REPORT_MD
from stac_utils import (
    PATH_RESULTS_CSV,
    PATH_S3,
    date_extract_from_path,
    fix_url,
    geotiff_extract_metadata,
)

logger = logging.getLogger(__name__)

COG_AGREEMENT_MIN = 0.99
FOOTPRINT_AGREEMENT_MIN = 1.0
# Below this many footprint-comparable tiles the footprint result is
# inconclusive rather than passing. A threshold met by three tiles is not
# evidence about 96,000.
FOOTPRINT_SAMPLE_MIN = 50


def sample_stratified(pairs: list[dict], n: int, seed: int) -> list[dict]:
    """Draw n pairs spread across (convention, acquisition year) strata.

    Every stratum contributes at least one tile, so the 117 identical-basename
    tiles and the 2 unknown-convention ones are actually exercised rather than
    being rounded out of a proportional sample.
    """
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for row in pairs:
        year = (date_extract_from_path(row["dem_key"]) or "unknown")[:4]
        strata[(row["convention"], year)].append(row)

    rng = random.Random(seed)
    per = max(1, n // len(strata))
    sample: list[dict] = []
    for key in sorted(strata):
        rows = strata[key]
        sample.extend(rng.sample(rows, min(per, len(rows))))

    # Top up to n from whatever is left, so a stratum smaller than `per` does
    # not silently shrink the sample.
    if len(sample) < n:
        chosen = {r["dem_key"] for r in sample}
        rest = [r for r in pairs if r["dem_key"] not in chosen]
        sample.extend(rng.sample(rest, min(n - len(sample), len(rest))))

    logger.info("Sampled %d pairs across %d (convention, year) strata",
                len(sample), len(strata))
    return sample


def compare(dem_meta: dict, dsm_meta: dict) -> dict:
    """Compare a sampled DSM's measured metadata against its DEM's cached row."""
    footprint_fields = ("epsg", "height", "width", "bounds")
    comparable = pd.notna(dem_meta.get("bounds"))
    footprint_ok = comparable and all(
        _norm(dem_meta.get(f)) == _norm(dsm_meta.get(f)) for f in footprint_fields
    )
    return {
        "readable": bool(dsm_meta.get("is_geotiff")),
        "footprint_comparable": bool(comparable),
        "footprint_ok": bool(footprint_ok),
        "cog_ok": bool(dem_meta.get("is_cog")) == bool(dsm_meta.get("is_cog")),
        "dem_is_cog": bool(dem_meta.get("is_cog")),
        "dsm_is_cog": bool(dsm_meta.get("is_cog")),
    }


def _norm(value):
    """Normalise a cached CSV value against a freshly measured one.

    The cache stores bounds as a JSON string and numerics as floats read back by
    pandas; the live extraction returns a JSON string and ints. Comparing the
    raw values would report every single tile as a footprint mismatch.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return value
    if isinstance(value, list):
        return [round(float(v), 6) for v in value]
    return round(float(value), 6)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify DSM inherits its DEM's COG status and footprint")
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--pairs-csv", default=PAIRS_CSV)
    parser.add_argument("--append-report", action="store_true",
                        help=f"Append the result to {REPORT_MD}")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    with open(args.pairs_csv, newline="") as fh:
        pairs = [r for r in csv.DictReader(fh) if r["status"] == PAIRED]
    if not pairs:
        logger.error("No paired rows in %s - nothing to verify", args.pairs_csv)
        return 2
    logger.info("Loaded %d paired rows", len(pairs))

    dem_cache = pd.read_csv(PATH_RESULTS_CSV)
    dem_lookup = {fix_url(r["url"]): r for r in dem_cache.to_dict("records")}
    logger.info("Loaded %d cached DEM metadata rows", len(dem_lookup))

    # Only tiles whose DEM metadata is already cached can be compared; a tile
    # added since the last extraction has nothing to compare against, and
    # silently treating that as agreement would be the exact failure this
    # script exists to rule out.
    comparable = [r for r in pairs if f"{PATH_S3}/{r['dem_key']}" in dem_lookup]
    logger.info("%d of %d paired tiles have cached DEM metadata",
                len(comparable), len(pairs))

    # COG status is cached for every row, but spatial metadata was only added to
    # the cache in the July 2026 build -- 60,324 of 100,345 rows predate it and
    # carry NaN for bounds/shape/epsg. Comparing against those reports "the
    # footprints differ" when the truth is "the DEM footprint was never
    # recorded", which is a different fact and must not be scored as a failure.
    # The two rates are therefore computed over different populations, and the
    # size of each is reported.
    def _has_footprint(row):
        cached = dem_lookup[f"{PATH_S3}/{row['dem_key']}"]
        return pd.notna(cached.get("bounds"))

    n_footprint_comparable = sum(1 for r in comparable if _has_footprint(r))
    logger.info("%d of those also have cached DEM footprint metadata",
                n_footprint_comparable)
    if n_footprint_comparable < FOOTPRINT_SAMPLE_MIN:
        logger.error("Only %d tiles can have their footprint compared (need %d) - "
                     "inconclusive, not a pass", n_footprint_comparable,
                     FOOTPRINT_SAMPLE_MIN)
        return 1

    sample = sample_stratified(comparable, args.sample, args.seed)
    urls = [f"{PATH_S3}/{r['dsm_key']}" for r in sample]

    logger.info("Reading %d DSM rasters with %d workers...", len(urls), args.workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        measured = list(tqdm(ex.map(geotiff_extract_metadata, urls),
                             total=len(urls), desc="Verifying DSM"))

    results = []
    for row, dsm_meta in zip(sample, measured):
        dem_meta = dem_lookup[f"{PATH_S3}/{row['dem_key']}"]
        r = compare(dem_meta, dsm_meta)
        r.update(convention=row["convention"], dem_key=row["dem_key"],
                 dsm_key=row["dsm_key"])
        results.append(r)

    readable = [r for r in results if r["readable"]]
    n = len(readable)
    if n == 0:
        logger.error("No sampled DSM was readable - cannot verify anything")
        return 1

    cog_rate = sum(r["cog_ok"] for r in readable) / n

    with_fp = [r for r in readable if r["footprint_comparable"]]
    footprint_rate = (sum(r["footprint_ok"] for r in with_fp) / len(with_fp)
                      if with_fp else 0.0)

    logger.info("Sampled %d, readable %d (%d unreadable)",
                len(results), n, len(results) - n)
    logger.info("COG status agreement:  %.4f over %d tiles (threshold %.2f)",
                cog_rate, n, COG_AGREEMENT_MIN)
    logger.info("Footprint agreement:   %.4f over %d tiles (threshold %.2f)",
                footprint_rate, len(with_fp), FOOTPRINT_AGREEMENT_MIN)

    for r in readable:
        if not r["cog_ok"] or (r["footprint_comparable"] and not r["footprint_ok"]):
            logger.warning("MISMATCH %s cog_ok=%s footprint_ok=%s",
                           r["dsm_key"], r["cog_ok"], r["footprint_ok"])

    # The population guard above says a comparison is possible at all; this one
    # says THIS run actually made enough of them. Without it a 60-tile smoke run
    # that happened to draw 3 comparable tiles would report PASS.
    if len(with_fp) < FOOTPRINT_SAMPLE_MIN:
        logger.error("Only %d sampled tiles had a comparable DEM footprint "
                     "(need %d) - inconclusive, not a pass. Raise --sample.",
                     len(with_fp), FOOTPRINT_SAMPLE_MIN)
        return 1

    passed = cog_rate >= COG_AGREEMENT_MIN and footprint_rate >= FOOTPRINT_AGREEMENT_MIN

    if args.append_report:
        with open(REPORT_MD, "a") as fh:
            fh.write(
                f"\n## Inherited media type - sample verification\n\n"
                f"Items carry the DSM's media type inherited from the paired DEM\n"
                f"rather than measured. This is the evidence for that.\n\n"
                f"| | |\n|---|---|\n"
                f"| sampled | {len(results)} |\n"
                f"| readable | {n} |\n"
                f"| COG status agreement | {cog_rate:.4f} over {n} "
                f"(threshold {COG_AGREEMENT_MIN}) |\n"
                f"| footprint agreement | {footprint_rate:.4f} over {len(with_fp)} "
                f"(threshold {FOOTPRINT_AGREEMENT_MIN}) |\n"
                f"| footprint not comparable | {n - len(with_fp)} tiles whose DEM "
                f"predates spatial-metadata caching |\n"
                f"| verdict | {'PASS' if passed else 'FAIL'} |\n\n"
                f"Sample is stratified across naming convention and acquisition\n"
                f"year, seed {args.seed}, so the 117 identical-basename tiles and\n"
                f"the 2 unknown-convention ones are exercised rather than rounded\n"
                f"out of a proportional draw.\n"
            )
        logger.info("Appended verification result to %s", REPORT_MD)

    if not passed:
        logger.error("Verification FAILED - the inherited media type is not safe")
        return 1
    logger.info("Verification PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
