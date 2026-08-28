#!/usr/bin/env python3
"""Pair each DEM tile with its DSM sibling and report what did not pair.

A DEM and its DSM come from the same flight over the same footprint at the same
time, so they belong on one STAC item as two assets. There is no manifest, so
the relationship is inferred from filenames -- and the naming convention is not
uniform across deliveries (see issue #29).

Matching is on **parsed semantics**, not on a name transform: tile id,
acquisition date, utm zone and containing mapsheet-year directory. Which naming
convention relates the two names is then recorded as an *observation* on an
already-matched pair. That inversion is the whole point -- a future delivery
using a convention we have not seen still pairs, and shows up in the report as
`convention=unknown` instead of quietly losing its DSM.

Every DEM lands in exactly one bucket:
  paired         - a DSM was found; the convention that relates the names is recorded
  no_raster_dsm  - the mapsheet-year's dsm/ directory holds no .tif at all
                   (11 such deliveries publish the surface model as .laz only)
  no_dsm_dir     - the mapsheet-year has no dsm/ directory
  unpaired       - a raster DSM exists in the group but not for this tile
  unparseable    - no tile id could be parsed from the DEM name

Nothing is ever dropped silently. The counts of those five buckets sum to the
DEM input count, and the script asserts that before writing anything.

Usage:
    python scripts/dsm_pair.py                    # write pairs csv + report
    python scripts/dsm_pair.py --dry-run          # print the summary only
"""

import argparse
import csv
import logging
import os
import sys
from collections import Counter, defaultdict

from stac_utils import (
    PATH_S3,
    convention_classify,
    fix_url,
    pair_key,
    tile_key_parse,
)

logger = logging.getLogger(__name__)

DEM_KEYS_FILE = "data/urls_list.txt"
DSM_KEYS_FILE = "data/urls_dsm.txt"
# Every mapsheet-year that has a dsm/ directory, whatever it holds. Separate
# from the raster listing so a .laz-only delivery is a declared coverage gap
# rather than looking identical to "no DSM was delivered".
DSM_GROUPS_FILE = "data/dsm_groups.txt"
PAIRS_CSV = "data/dem_dsm_pairs.csv"
REPORT_MD = "data/dsm_pairing_report.md"
# DEM URLs whose paired DSM changed since the last run. A surface model can
# arrive for a DEM that is already in the catalog, and nothing else would ever
# rebuild that item -- item_create.py only ever sees newly-listed DEM URLs.
CHANGED_URLS_FILE = "data/urls_pairing_changed.txt"

# Statuses. `paired` is the only one that yields a dsm asset; the rest are
# declared gaps that appear in the report.
PAIRED = "paired"
NO_RASTER_DSM = "no_raster_dsm"
NO_DSM_DIR = "no_dsm_dir"
UNPAIRED = "unpaired"
UNPARSEABLE = "unparseable"


class ListingError(RuntimeError):
    """A product listing could not be read.

    Raised so a failed listing is distinguishable from a genuinely empty one.
    An empty product set means "this delivery has no DSM"; a failed read means
    we do not know, and must not proceed as though we did.
    """


def keys_load(path: str, label: str) -> list[str]:
    """Read a key listing, refusing to treat a failure as an empty product set.

    A missing or empty file is an error, not "no DSM in the bucket" -- pairing
    against an empty DSM listing would silently reclassify all ~96k tiles as a
    coverage gap and drop every DSM asset from the catalog.
    """
    if not os.path.exists(path):
        raise ListingError(f"{label} listing not found: {path}")
    with open(path) as fh:
        keys = [line.strip() for line in fh if line.strip()]
    if not keys:
        raise ListingError(f"{label} listing is empty: {path}")
    return keys


def key_relative(key: str) -> str:
    """Object key relative to the bucket root, for compact storage in the csv."""
    key = fix_url(key)
    if key.startswith(PATH_S3):
        key = key[len(PATH_S3):]
    return key.lstrip("/")


def pairs_build(dem_keys: list[str], dsm_keys: list[str],
                dsm_dir_groups: set[str]) -> dict:
    """Match DEM keys to DSM keys. Pure -- no I/O, no network.

    `dsm_keys` holds only raster (.tif) DSM keys, so a delivery that published
    its surface model as .laz produces no keys at all and is indistinguishable
    from one that shipped no DSM. `dsm_dir_groups` is the separate input that
    tells those two apart: every mapsheet-year that has a `dsm/` directory,
    whatever it contains. Without it `no_raster_dsm` is unreachable and 1,211
    real tiles would be misreported as having had no surface model delivered.

    Returns a dict with `rows` (one per DEM key, always), `collisions`
    (DSM keys sharing a match key, which would make pairing ambiguous) and
    `dsm_unmatched` (raster DSM tiles no DEM claimed).
    """
    # Index DSM keys by match key, and note which groups hold a raster DSM.
    dsm_by_key: dict[tuple, list[dict]] = defaultdict(list)
    dsm_groups: set[str] = set()
    dsm_unparseable: list[str] = []

    for key in dsm_keys:
        parsed = tile_key_parse(key)
        if parsed is None:
            dsm_unparseable.append(key)
            continue
        parsed["key"] = key
        dsm_by_key[pair_key(parsed)].append(parsed)
        dsm_groups.add(parsed["group"])

    collisions = {k: [p["key"] for p in v] for k, v in dsm_by_key.items() if len(v) > 1}

    rows = []
    claimed: dict[str, list[str]] = defaultdict(list)
    for key in dem_keys:
        parsed = tile_key_parse(key)
        if parsed is None:
            rows.append({
                "dem_key": key_relative(key),
                "dsm_key": "",
                "group": "",
                "tile_id": "",
                "convention": "",
                "status": UNPARSEABLE,
            })
            continue

        group = parsed["group"]
        matches = dsm_by_key.get(pair_key(parsed), [])
        if matches:
            # Several DSMs can share one match key where the bucket holds the
            # same tile under two spellings -- 121 tiles carry both a `utm09`
            # and a `utm9` DSM. Prefer the one whose name relates to this DEM's
            # by a convention we recognise, so the choice among equals is
            # deterministic and the recorded convention stays accurate instead
            # of degrading to `unknown` on an arbitrary pick.
            dsm = next(
                (m for m in matches
                 if convention_classify(parsed["basename"], m["basename"]) != "unknown"),
                matches[0],
            )
            claimed[dsm["key"]].append(key)
            rows.append({
                "dem_key": key_relative(key),
                "dsm_key": key_relative(dsm["key"]),
                "group": group,
                "tile_id": parsed["tile_id"],
                "convention": convention_classify(parsed["basename"], dsm["basename"]),
                "status": PAIRED,
            })
            continue

        if group in dsm_groups:
            status = UNPAIRED
        elif group in dsm_dir_groups:
            status = NO_RASTER_DSM
        else:
            status = NO_DSM_DIR
        rows.append({
            "dem_key": key_relative(key),
            "dsm_key": "",
            "group": group,
            "tile_id": parsed["tile_id"],
            "convention": "",
            "status": status,
        })

    dsm_unmatched = [k for k in dsm_keys if k not in claimed]
    # One DSM claimed by several DEMs is legitimate where a delivery shipped a
    # re-issued DEM beside the original (e.g. `..._2019.tif` and
    # `..._2019_1.tif` sharing one `..._2019_dsm.tif`). Both items describe the
    # same footprint, so both may carry the asset -- but it is surfaced rather
    # than assumed, because the same shape would also appear if the match key
    # were too coarse.
    dsm_shared = {k: v for k, v in claimed.items() if len(v) > 1}

    # Nothing may vanish between input and output. An explicit raise rather
    # than an assert: `python -O` strips asserts, and this is the invariant the
    # whole script exists to uphold.
    if len(rows) != len(dem_keys):
        raise RuntimeError(
            f"row count {len(rows)} != dem input {len(dem_keys)} - "
            "a DEM key was dropped, which must never happen silently"
        )

    return {
        "rows": rows,
        "collisions": collisions,
        "dsm_unmatched": dsm_unmatched,
        "dsm_unparseable": dsm_unparseable,
        "dsm_shared": dsm_shared,
    }


def pairing_changed(rows: list[dict], previous_csv: str) -> list[str]:
    """DEM keys whose paired DSM differs from the previous run's pairing.

    Returns [] when there is no previous file -- a first run has nothing to
    compare against, and treating every tile as changed would queue a 100k-item
    rebuild. That is a deliberate asymmetry, noted in the log by the caller.
    """
    if not os.path.exists(previous_csv):
        return []
    with open(previous_csv, newline="") as fh:
        before = {r["dem_key"]: r["dsm_key"] for r in csv.DictReader(fh)}
    return [
        r["dem_key"] for r in rows
        if r["dem_key"] in before and before[r["dem_key"]] != r["dsm_key"]
    ]


def summarize(result: dict) -> dict:
    """Counts by status and, for paired rows, by naming convention."""
    rows = result["rows"]
    status = Counter(r["status"] for r in rows)
    convention = Counter(r["convention"] for r in rows if r["status"] == PAIRED)
    groups_no_raster = sorted({r["group"] for r in rows if r["status"] == NO_RASTER_DSM})
    groups_unpaired = Counter(r["group"] for r in rows if r["status"] == UNPAIRED)
    return {
        "total": len(rows),
        "status": status,
        "convention": convention,
        "groups_no_raster_dsm": groups_no_raster,
        "groups_unpaired": groups_unpaired,
    }


def report_render(summary: dict, result: dict) -> str:
    """Render the pairing report. Gaps are named, never merely counted away."""
    s = summary["status"]
    total = summary["total"]
    lines = [
        "# DEM/DSM pairing report",
        "",
        "Generated by `scripts/dsm_pair.py`. Matching is on parsed tile id,",
        "acquisition date and utm zone within a mapsheet-year directory; the",
        "naming convention is recorded as an observation on an already-matched",
        "pair, so an unrecognised convention appears below rather than dropping",
        "a tile's DSM silently.",
        "",
        "## DEM tiles by outcome",
        "",
        "| outcome | tiles | share |",
        "|---|---|---|",
    ]
    for name in (PAIRED, NO_RASTER_DSM, NO_DSM_DIR, UNPAIRED, UNPARSEABLE):
        n = s.get(name, 0)
        lines.append(f"| `{name}` | {n:,} | {100.0 * n / total:.2f}% |")
    lines += [f"| **total** | **{total:,}** | |", ""]

    lines += ["## Paired tiles by naming convention", "",
              "| convention | tiles | share of paired |", "|---|---|---|"]
    paired = s.get(PAIRED, 0)
    for name, n in summary["convention"].most_common():
        share = f"{100.0 * n / paired:.2f}%" if paired else "-"
        lines.append(f"| `{name}` | {n:,} | {share} |")
    lines.append("")

    groups = summary["groups_no_raster_dsm"]
    lines += [
        f"## Mapsheet-years with a `dsm/` directory but no raster ({len(groups)})",
        "",
        "These deliveries published the surface model as point cloud only. This",
        "is a declared coverage gap, not a pairing failure.",
        "",
    ]
    lines += [f"- `{g}`" for g in groups] or ["- none"]
    lines.append("")

    unpaired = summary["groups_unpaired"]
    lines += [
        f"## Unpaired tiles in groups that do have a raster DSM ({sum(unpaired.values()):,})",
        "",
        "A DSM raster exists in the mapsheet-year but none matched this tile.",
        "Review these -- an unfamiliar naming convention lands here.",
        "",
    ]
    if unpaired:
        lines += ["| mapsheet-year | unpaired tiles |", "|---|---|"]
        lines += [f"| `{g}` | {n:,} |" for g, n in unpaired.most_common(50)]
        if len(unpaired) > 50:
            lines.append(f"| ... and {len(unpaired) - 50} more groups | |")
    else:
        lines.append("None.")
    lines.append("")

    unparse = [r["dem_key"] for r in result["rows"] if r["status"] == UNPARSEABLE]
    lines += [f"## DEM keys with no parseable tile id ({len(unparse):,})", ""]
    if unparse:
        lines += ["Reported rather than guessed at. Sample:", ""]
        lines += [f"- `{k}`" for k in unparse[:10]]
        if len(unparse) > 10:
            lines.append(f"- ... and {len(unparse) - 10:,} more")
    else:
        lines.append("None.")
    lines.append("")

    lines += [
        f"## Raster DSM tiles no DEM claimed ({len(result['dsm_unmatched']):,})",
        "",
    ]
    if result["dsm_unmatched"]:
        lines += [f"- `{key_relative(k)}`" for k in result["dsm_unmatched"][:10]]
        if len(result["dsm_unmatched"]) > 10:
            lines.append(f"- ... and {len(result['dsm_unmatched']) - 10:,} more")
    else:
        lines.append("None.")
    lines.append("")

    shared = result.get("dsm_shared") or {}
    lines += [f"## DSM rasters claimed by more than one DEM ({len(shared)})", ""]
    if shared:
        lines += [
            "Two DEM keys matched one DSM. Known benign causes: a re-issued",
            "DEM beside its original (`..._2019.tif` and `..._2019_1.tif`), and",
            "the same tile spelled with a padded and an unpadded utm zone. Both",
            "carry the asset. Listed rather than assumed, because a match key",
            "too coarse to separate two genuinely different tiles would produce",
            "exactly this shape, and the two must not look the same.",
            "",
        ]
        for dsm_key, dems in list(shared.items())[:10]:
            lines.append(f"- `{key_relative(dsm_key)}`")
            lines += [f"    - `{key_relative(d)}`" for d in dems]
        if len(shared) > 10:
            lines.append(f"- ... and {len(shared) - 10} more")
    else:
        lines.append("None.")
    lines.append("")

    if result["collisions"]:
        lines += [
            f"## Ambiguous DSM match keys ({len(result['collisions'])})",
            "",
            "More than one DSM raster shares a tile id, date and utm zone within",
            "one mapsheet-year. The first is used; review these.",
            "",
        ]
        for k, keys in list(result["collisions"].items())[:10]:
            lines.append(f"- `{k}`: {', '.join(key_relative(x) for x in keys)}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pair DEM tiles with their DSM siblings")
    parser.add_argument("--dem-keys", default=DEM_KEYS_FILE)
    parser.add_argument("--dsm-keys", default=DSM_KEYS_FILE)
    parser.add_argument("--dsm-groups", default=DSM_GROUPS_FILE)
    parser.add_argument("--pairs-csv", default=PAIRS_CSV)
    parser.add_argument("--report", default=REPORT_MD)
    parser.add_argument("--changed-urls", default=CHANGED_URLS_FILE)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the summary without writing outputs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    try:
        dem_keys = keys_load(args.dem_keys, "DEM")
        dsm_keys = keys_load(args.dsm_keys, "DSM")
        dsm_dir_groups = set(keys_load(args.dsm_groups, "DSM directory"))
    except ListingError as e:
        logger.error("%s", e)
        logger.error("Refusing to pair: a failed listing must not read as "
                     "'this bucket has no DSM'. Re-run scripts/urls_fetch.R.")
        return 2

    logger.info("Pairing %d DEM keys against %d DSM keys", len(dem_keys), len(dsm_keys))
    result = pairs_build(dem_keys, dsm_keys, dsm_dir_groups)
    summary = summarize(result)

    for name, n in summary["status"].most_common():
        logger.info("  %-14s %7d", name, n)
    for name, n in summary["convention"].most_common():
        logger.info("  convention %-10s %7d", name, n)
    if result["collisions"]:
        logger.warning("%d ambiguous DSM match keys - see the report",
                       len(result["collisions"]))

    if args.dry_run:
        logger.info("Dry run - no files written")
        return 0

    # Compute this BEFORE the csv is overwritten -- it is a diff against the
    # previous run, and the previous run's file is the only record of it.
    changed = pairing_changed(result["rows"], args.pairs_csv)
    if not os.path.exists(args.pairs_csv):
        logger.info("No previous pairing to diff against - not queueing rebuilds")
    elif changed:
        logger.info("%d DEM items need rebuilding: their paired DSM changed",
                    len(changed))
    else:
        logger.info("No pairing changes since the last run")

    os.makedirs(os.path.dirname(args.pairs_csv) or ".", exist_ok=True)
    with open(args.changed_urls, "w") as fh:
        fh.write("".join(f"{PATH_S3}/{k}\n" for k in changed))

    with open(args.pairs_csv, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["dem_key", "dsm_key", "group", "tile_id", "convention", "status"]
        )
        writer.writeheader()
        writer.writerows(result["rows"])
    logger.info("Wrote %d rows to %s", len(result["rows"]), args.pairs_csv)

    with open(args.report, "w") as fh:
        fh.write(report_render(summary, result))
    logger.info("Wrote report to %s", args.report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
