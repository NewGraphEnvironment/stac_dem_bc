#!/usr/bin/env python3
"""
Create STAC items from validated GeoTIFF URLs.

Reads URLs, validates GeoTIFFs (with caching), creates STAC items in parallel,
and updates the collection. Supports test, incremental, and reprocess modes.

Usage:
    python scripts/item_create.py                          # Full production run
    python scripts/item_create.py --test                   # Test with 10 items
    python scripts/item_create.py --test --test-count 50   # Test with 50 items
    python scripts/item_create.py --incremental            # Process only new URLs
    python scripts/item_create.py --reprocess-invalid      # Re-process invalid items
"""

import argparse
import concurrent.futures
import glob
import logging
import os
import sys

import pandas as pd
import pystac
import rio_stac
from datetime import datetime, timezone
from pystac import Link, RelType
from tqdm import tqdm

from stac_utils import (
    geotiff_extract_metadata,
    item_create_from_cache,
    date_extract_from_path,
    datetime_parse_item,
    encode_url_for_gdal,
    fix_url,
    get_output_dir,
    url_to_item_id,
    PATH_S3,
    PATH_S3_JSON,
    PATH_S3_STAC,
    PATH_RESULTS_CSV,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Item Processing
# =============================================================================

def process_item(path_item: str, collection_id: str, path_local: str,
                 results_lookup: dict) -> dict | None:
    """Process a single GeoTIFF URL to create a STAC item.

    Uses cached metadata when available (no remote read). Falls back to
    rio_stac for cache misses (should not happen if validation ran first).

    Returns dict with item_id and item object, or None if processing fails.
    """
    href_item = fix_url(path_item)
    check = results_lookup.get(href_item)

    if check is None or not check["is_geotiff"]:
        logger.warning("Skipping unreadable GeoTIFF: %s", href_item)
        return None

    item_id = url_to_item_id(path_item)

    # Extract datetime from path, use placeholder if not found
    date_str = date_extract_from_path(path_item)
    datetime_is_unknown = False

    if date_str:
        item_time = datetime_parse_item(date_str)
    else:
        item_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
        datetime_is_unknown = True

    media_type = (
        "image/tiff; application=geotiff; profile=cloud-optimized"
        if check["is_cog"] else
        "image/tiff; application=geotiff"
    )

    try:
        # Cache hit: build from metadata (no remote read)
        if check.get("epsg") is not None:
            item = item_create_from_cache(
                url=path_item,
                item_id=item_id,
                metadata=check,
                collection_id=collection_id,
                collection_url=PATH_S3_JSON,
                media_type=media_type,
                item_datetime=item_time,
            )
        else:
            # Cache miss: fall back to rio_stac (remote read)
            logger.info("Cache miss for %s, reading remote file", href_item)
            gdal_path = encode_url_for_gdal(path_item)
            item = rio_stac.stac.create_stac_item(
                gdal_path,
                id=item_id,
                asset_media_type=media_type,
                asset_name='image',
                asset_href=href_item,
                with_proj=True,
                collection=collection_id,
                collection_url=PATH_S3_JSON,
                asset_roles=["data"]
            )
            item.assets['image'].href = href_item

        item.datetime = item_time

        if datetime_is_unknown:
            item.properties["datetime_unknown"] = True

        path_item_json = f"{path_local}/{item_id}.json"
        item.save_object(dest_href=path_item_json, include_self_link=False)

        return {"id": item_id, "item": item}
    except Exception as e:
        logger.error("Error processing %s: %s", href_item, e)
        return None


# =============================================================================
# Validation
# =============================================================================

def load_validation_cache(urls_to_check: list[str]) -> dict:
    """Load cached metadata and extract metadata for new URLs as needed.

    Returns lookup dict: {url: {is_geotiff, is_cog, epsg, height, width, transform, bounds}}

    Old cache rows (missing spatial columns) trigger re-extraction on cache miss
    in process_item via the rio_stac fallback path.
    """
    all_columns = ["url", "is_geotiff", "is_cog", "epsg", "height", "width", "transform", "bounds"]

    if os.path.exists(PATH_RESULTS_CSV):
        df_existing = pd.read_csv(PATH_RESULTS_CSV)
        existing_urls = set(df_existing["url"])
        logger.info("Loaded %d existing validation results", len(df_existing))
    else:
        df_existing = pd.DataFrame(columns=all_columns)
        existing_urls = set()
        logger.info("No existing validation cache found, will validate all URLs")

    # Detect old-format rows missing spatial metadata (all spatial columns null).
    # Rows that were extracted but have epsg=None (no CRS) are NOT re-upgraded —
    # they'll have height/width/transform populated from the extraction.
    needs_upgrade = set()
    if "transform" in df_existing.columns:
        for _, row in df_existing.iterrows():
            if row.get("is_geotiff") and pd.isna(row.get("transform")):
                needs_upgrade.add(row["url"])
    else:
        needs_upgrade = {row["url"] for _, row in df_existing.iterrows() if row["is_geotiff"]}

    urls_to_validate = [url for url in urls_to_check
                        if url not in existing_urls or url in needs_upgrade]
    if needs_upgrade:
        # Drop old rows that will be re-extracted with spatial metadata
        urls_upgrading = needs_upgrade & set(urls_to_validate)
        if urls_upgrading:
            df_existing = df_existing[~df_existing["url"].isin(urls_upgrading)]
            logger.info("%d cached URLs need spatial metadata upgrade", len(urls_upgrading))

    logger.info("%d URLs need metadata extraction (%d already cached with full metadata)",
                len(urls_to_validate), len(urls_to_check) - len(urls_to_validate))

    if urls_to_validate:
        logger.info("Extracting metadata from %d GeoTIFFs...", len(urls_to_validate))
        with concurrent.futures.ThreadPoolExecutor() as executor:
            new_results = list(tqdm(
                executor.map(geotiff_extract_metadata, urls_to_validate),
                total=len(urls_to_validate),
                desc="Extracting GeoTIFF metadata"
            ))

        df_new = pd.DataFrame(new_results)
        df_all = pd.concat([df_existing, df_new], ignore_index=True) if len(df_existing) > 0 else df_new
        df_all.to_csv(PATH_RESULTS_CSV, index=False)
        logger.info("Saved %d validation results to %s", len(df_all), PATH_RESULTS_CSV)
    else:
        df_all = df_existing
        logger.info("All URLs cached, no remote reads needed")

    # Build lookup with full metadata (NaN → None for missing spatial columns)
    result = {}
    for _, row in df_all.iterrows():
        entry = {"is_geotiff": row["is_geotiff"], "is_cog": row["is_cog"]}
        for col in ["epsg", "height", "width", "transform", "bounds"]:
            if col in row and pd.notna(row[col]):
                entry[col] = int(row[col]) if col in ("epsg", "height", "width") else row[col]
            else:
                entry[col] = None
        result[fix_url(row["url"])] = entry
    return result


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Create STAC items from GeoTIFF URLs")
    parser.add_argument("--test", action="store_true", help="Test mode (dev output)")
    parser.add_argument("--test-count", type=int, default=10, help="Number of items in test mode (default: 10)")
    parser.add_argument("--incremental", action="store_true", help="Process only new URLs from data/urls_new.txt")
    parser.add_argument("--reprocess-invalid", action="store_true", help="Re-process items from data/urls_invalid_items.txt")
    parser.add_argument("--workers", type=int, default=32, help="Number of parallel workers (default: 32)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    path_local = get_output_dir(test_only=args.test)
    path_collection = f"{path_local}/collection.json"

    logger.info("Mode: %s", "TEST (dev output)" if args.test else "PRODUCTION (prod output)")
    logger.info("Output directory: %s", path_local)

    # Load collection
    collection = pystac.Collection.from_file(path_collection)
    collection.set_self_href(PATH_S3_JSON)

    # Select URL source based on mode
    if args.reprocess_invalid:
        urls_file = "data/urls_invalid_items.txt"
        mode_desc = "reprocess_invalid"
    elif args.incremental:
        urls_file = "data/urls_new.txt"
        mode_desc = "incremental"
    else:
        urls_file = "data/urls_list.txt"
        mode_desc = "full"

    if not os.path.exists(urls_file):
        logger.error("URLs file not found: %s", urls_file)
        return 1

    with open(urls_file) as f:
        path_items = f.read().splitlines()

    urls_to_check = path_items[:args.test_count] if args.test else path_items
    logger.info("Processing %d URLs (mode=%s, test=%s)", len(urls_to_check), mode_desc, args.test)

    # Handle existing items based on mode
    if args.reprocess_invalid:
        existing_item_count = len([l for l in collection.links if l.rel == 'item'])
        logger.info("Reprocess mode: Updating %d items (collection has %d total)",
                     len(urls_to_check), existing_item_count)
    elif args.test and not args.incremental:
        # Clean slate for test runs
        collection.links = [link for link in collection.links if link.rel != 'item']
        logger.info("Test mode: Cleared existing item links")

        old_jsons = glob.glob(f"{path_local}/*-*.json")
        if old_jsons:
            for json_file in old_jsons:
                os.remove(json_file)
            logger.info("Test mode: Deleted %d old item JSON files", len(old_jsons))
    elif args.incremental:
        existing_item_count = len([l for l in collection.links if l.rel == 'item'])
        logger.info("Incremental mode: Appending to %d existing items", existing_item_count)

    # Pre-validation
    results_lookup = load_validation_cache(urls_to_check)

    # Parallel item creation
    logger.info("Creating STAC items with %d workers...", args.workers)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            results = list(filter(None, tqdm(
                executor.map(
                    lambda url: process_item(url, collection.id, path_local, results_lookup),
                    urls_to_check
                ),
                total=len(urls_to_check),
                desc="Creating STAC Items"
            )))
    except Exception as e:
        logger.error("Parallel execution failed: %s", e)
        results = []

    # Add item links to collection (with duplicate prevention)
    if results:
        existing_item_hrefs = {link.target for link in collection.links if link.rel == 'item'}

        added_count = 0
        skipped_count = 0
        for result in results:
            item_href = f"{PATH_S3_STAC}/{result['id']}.json"
            if item_href not in existing_item_hrefs:
                collection.add_link(Link(
                    rel=RelType.ITEM,
                    target=item_href,
                    media_type="application/json"
                ))
                added_count += 1
            else:
                skipped_count += 1

        logger.info("Created %d items, added %d links, skipped %d duplicates",
                     len(results), added_count, skipped_count)
    else:
        logger.warning("No items were created")

    # Save updated collection
    collection.save_object(dest_href=path_collection)
    logger.info("Collection saved to %s", path_collection)

    return 0


if __name__ == "__main__":
    sys.exit(main())
