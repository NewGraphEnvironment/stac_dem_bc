#!/usr/bin/env python3
"""
Create STAC collection metadata for DEM BC.

Reads URLs from data/urls_list.txt, calculates temporal extent from paths,
uses hardcoded BC spatial extent, and creates collection.json.

Usage:
    python scripts/collection_create.py                         # Production
    python scripts/collection_create.py --test                  # Test (dev output)
    python scripts/collection_create.py --test --test-count 50  # Test with 50 items
"""

import argparse
import glob
import logging
import os
import sys

import pystac
from pystac import Collection, Extent, SpatialExtent, TemporalExtent

from collection_patch import DESCRIPTION, KEYWORDS, PROVIDERS, version_stamp
from stac_utils import (
    BBOX_BC,
    date_extract_from_path,
    datetime_parse_item,
    get_output_dir,
    PATH_S3_JSON,
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Create STAC collection for DEM BC")
    parser.add_argument("--test", action="store_true", help="Test mode (dev output)")
    parser.add_argument("--version", default=None,
                        help="Stamp this release version via the STAC Version Extension. "
                             "Pass explicitly; never derived from git here (CI checks out "
                             "shallow with no tags, and the usual 0.0.0 fallback is a "
                             "silently wrong stamp).")
    parser.add_argument("--test-count", type=int, default=10, help="Number of items for extent calculation in test mode (default: 10)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    path_local = get_output_dir(test_only=args.test)
    path_collection = f"{path_local}/collection.json"
    collection_id = "stac-dem-bc"

    logger.info("Mode: %s", "TEST (dev output)" if args.test else "PRODUCTION (prod output)")
    logger.info("Output: %s", path_collection)

    # Clean up old test items if in test mode
    if args.test:
        old_items = glob.glob(f"{path_local}/*.json")
        old_items = [f for f in old_items if not f.endswith("collection.json")]
        if old_items:
            for item_file in old_items:
                os.remove(item_file)
            logger.info("Test mode: Cleaned up %d old item JSONs", len(old_items))

    # Load URLs
    urls_file = "data/urls_list.txt"
    if not os.path.exists(urls_file):
        logger.error("URLs file not found: %s", urls_file)
        logger.error("Run: Rscript scripts/urls_fetch.R")
        return 1

    with open(urls_file) as f:
        path_items = f.read().splitlines()

    if args.test:
        path_items = path_items[:args.test_count]
        logger.info("Test mode: Using %d items for extent calculation", len(path_items))

    # Calculate temporal extent from URL paths
    times = [datetime_parse_item(date_extract_from_path(p)) for p in path_items]
    times = [t for t in times if t is not None]

    if not times:
        logger.error("No valid datetimes extracted from URLs")
        return 1

    start_time = min(times)
    end_time = max(times)
    temporal_extent = TemporalExtent([[start_time, end_time]])
    logger.info("Temporal extent: %s to %s", start_time.isoformat(), end_time.isoformat())

    # Spatial extent (hardcoded BC bbox — see issue #4)
    spatial_extent = SpatialExtent([BBOX_BC])
    logger.info("Using hardcoded BC bbox: %s", BBOX_BC)

    # Create collection
    extent = Extent(spatial=spatial_extent, temporal=temporal_extent)
    # providers/keywords/description come from collection_patch so a full
    # rebuild and an in-place patch of the published collection cannot drift.
    # See issue #30: CC-BY-4.0 obliges attribution the metadata did not carry.
    collection = Collection(
        id=collection_id,
        description=DESCRIPTION,
        extent=extent,
        license="CC-BY-4.0",
        title=f"Digital Elevation Models from British Columbia - {collection_id}",
        href=path_collection,
        providers=[pystac.Provider.from_dict(p) for p in PROVIDERS],
        keywords=list(KEYWORDS),
    )

    # A rebuild republishes the whole catalogue, so it is a release moment and
    # may carry a version. The monthly path never reaches here -- it patches the
    # fetched collection instead -- which is why the stamp is opt-in on both.
    if args.version:
        version_stamp(collection.extra_fields, args.version)
        collection.stac_extensions = list(collection.extra_fields.pop("stac_extensions"))
        logger.info("Stamped version %s", args.version)

    # Save with correct hrefs
    collection.save(catalog_type=pystac.CatalogType.ABSOLUTE_PUBLISHED)
    collection.set_self_href(PATH_S3_JSON)
    collection.save_object(include_self_link=True, dest_href=path_collection)
    collection.set_self_href(PATH_S3_JSON)

    # Validate
    collection = pystac.Collection.from_file(path_collection)
    collection.validate()
    logger.info("Collection saved and validated: %s", path_collection)

    return 0


if __name__ == "__main__":
    sys.exit(main())
