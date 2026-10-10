"""The fields #2 and #55 add to an item, applied the same way by every writer.

item_create.py, item_reprocess.py and the one-time rewrite (footprint_apply.py)
all call `item_fields_apply` on an item's dict. One path, so an item built in a
monthly run and one rewritten in place cannot differ in what they carry.

  footprint   geometry, bbox, proj:geometry; raster:bands valid_percent and
              file:checksum/size on `dem` (#2)
  lidarbc:    lidarbc:delivery (#55); lidarbc:datetime_unknown, renamed from
              the unprefixed datetime_unknown (#2, under crate#23's rule)

`audit` checks a directory of staged items for homogeneity in those fields.
stacs audit checks the collection id and asset keys only, so without this a
writer that forgot a field would publish a mixed catalogue nothing reports --
the shape of #34.

Usage:
    python scripts/item_fields.py audit --dir "$STAC_OUTPUT_DIR"
"""

import argparse
import glob
import json
import logging
import os
import sys

import pystac
from shapely.geometry import shape

from footprint import FILE_EXT, FOOTPRINT_METHODS, RASTER_EXT, item_footprint_apply
from footprint_extract import CACHE, cache_load
from stac_utils import (
    ASSET_DEM,
    DATETIME_UNKNOWN_LEGACY,
    LIDARBC_DATETIME_UNKNOWN,
    LIDARBC_DELIVERY,
    LIDARBC_EXT,
    lidarbc_delivery,
)

logger = logging.getLogger(__name__)


def footprints_load(path: str = CACHE) -> dict:
    """Footprint rows by URL. A missing cache warns: every item then keeps its extent."""
    if not os.path.exists(path):
        logger.warning("No footprint cache at %s - items keep their raster extent "
                       "as geometry. Run scripts/footprint_extract.py first.", path)
        return {}
    rows = cache_load(path)
    logger.info("Loaded %d footprint rows from %s", len(rows), path)
    return rows


def has_footprint(url: str, footprints: dict) -> bool:
    return (footprints.get(url) or {}).get("method") in FOOTPRINT_METHODS


def item_lidarbc_apply(item: dict) -> list[str]:
    """lidarbc:delivery from the dem href, and the datetime_unknown rename. Returns what changed."""
    before = json.dumps(item, sort_keys=True)
    props = item["properties"]
    delivery = lidarbc_delivery(item["assets"][ASSET_DEM]["href"])
    if delivery:
        props[LIDARBC_DELIVERY] = delivery
    if props.pop(DATETIME_UNKNOWN_LEGACY, False):
        props[LIDARBC_DATETIME_UNKNOWN] = True
    if any(k.startswith("lidarbc:") for k in props):
        exts = set(item.get("stac_extensions") or [])
        exts.add(LIDARBC_EXT)
        item["stac_extensions"] = sorted(exts)
    return [] if json.dumps(item, sort_keys=True) == before else ["lidarbc"]


def item_fields_apply(item: dict, url: str, footprints: dict) -> list[str]:
    """Apply every #2/#55 field to an item dict in place. Returns what changed."""
    return (item_footprint_apply(item, footprints.get(url), ASSET_DEM)
            + item_lidarbc_apply(item))


def pystac_item_fields_apply(item: pystac.Item, url: str, footprints: dict) -> pystac.Item:
    """The same, for an Item a builder has just made."""
    d = item.to_dict(include_self_link=False, transform_hrefs=False)
    item_fields_apply(d, url, footprints)
    return pystac.Item.from_dict(d)


# =============================================================================
# Audit
# =============================================================================

def item_faults(item: dict) -> list[str]:
    props = item.get("properties", {})
    exts = set(item.get("stac_extensions") or [])
    dem = item.get("assets", {}).get(ASSET_DEM, {})
    faults = []
    want = lidarbc_delivery(dem.get("href", ""))
    got = props.get(LIDARBC_DELIVERY)
    if want and got is None:
        faults.append("missing lidarbc:delivery")
    elif want != got:
        faults.append("wrong lidarbc:delivery")
    if DATETIME_UNKNOWN_LEGACY in props:
        faults.append("unprefixed datetime_unknown")
    if any(k.startswith("lidarbc:") for k in props) and LIDARBC_EXT not in exts:
        faults.append("lidarbc extension not declared")
    if "raster:bands" in dem and RASTER_EXT not in exts:
        faults.append("raster extension not declared")
    if "file:checksum" in dem and FILE_EXT not in exts:
        faults.append("file extension not declared")
    if "raster:bands" in dem and item.get("geometry"):
        if list(shape(item["geometry"]).bounds) != item.get("bbox"):
            faults.append("bbox is not the bounds of the geometry")
    return faults


def audit_dir(d: str) -> dict:
    """Faults by kind (with up to 5 example ids each) and footprint coverage, over `d`'s items."""
    paths = [p for p in glob.glob(os.path.join(d, "*.json")) if os.path.basename(p) != "collection.json"]
    if not paths:
        raise SystemExit(f"audit: no items in {d} - an empty set is not a pass")
    faults, n_fp = {}, 0
    for p in paths:
        with open(p) as fh:
            item = json.load(fh)
        n_fp += "raster:bands" in item.get("assets", {}).get(ASSET_DEM, {})
        for f in item_faults(item):
            faults.setdefault(f, []).append(item.get("id"))
    return {"items": len(paths), "with_footprint": n_fp,
            "faults": {k: v[:5] + ([f"... {len(v)} in all"] if len(v) > 5 else []) for k, v in faults.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit staged items for the #2/#55 fields")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--dir", required=True)
    args = ap.parse_args()
    r = audit_dir(args.dir)
    print(f"{r['items']} items, {r['with_footprint']} with a footprint, "
          f"{r['items'] - r['with_footprint']} keeping their extent")
    for k, ids in r["faults"].items():
        print(f"FAULT {k}: {ids}")
    return 1 if r["faults"] else 0


if __name__ == "__main__":
    sys.exit(main())
