#!/usr/bin/env python3
"""
Re-process invalid STAC items with updated datetime handling.

This script:
1. Reads URLs from data/urls_invalid_items.txt
2. Recreates STAC items with placeholder datetime for items missing dates
3. Overwrites invalid JSON files with valid versions
4. Flags items with datetime_unknown=True property

Usage:
    python scripts/reprocess_invalid_items.py
"""

import pathlib
import pystac
import rio_stac
import concurrent.futures
import pandas as pd
import os
import re
from tqdm import tqdm
from datetime import datetime, timezone

# Configuration
PATH_LOCAL = "/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc"
PATH_COLLECTION = f"{PATH_LOCAL}/collection.json"
PATH_S3_STAC = "https://stac-dem-bc.s3.amazonaws.com"
PATH_S3_JSON = f"{PATH_S3_STAC}/collection.json"
PATH_S3 = "https://nrs.objectstore.gov.bc.ca/gdwuts"
PATH_RESULTS_CSV = "data/stac_geotiff_checks.csv"
INVALID_URLS_FILE = "data/urls_invalid_items.txt"

# Date extraction functions (from stac_create_item.qmd)
def date_extract_from_path(s):
    """Extract date from path, trying multiple patterns."""
    # Step 1: Try to extract YYYYMMDD or YYYY after _utmXX_
    match = re.search(r'_utm\d{1,2}_([0-9]{4,8})', s)
    if match:
        val = match.group(1)
        if val.isdigit():
            year = int(val[:4])
            if 2000 <= year <= 2050:
                return val

    # Step 2: Fallback: look for /YYYY/ in the path
    fallback = re.search(r'/([2][0-9]{3})/', s)
    if fallback:
        year = int(fallback.group(1))
        if 2000 <= year <= 2050:
            return str(year)

    return None

def datetime_parse_item(s):
    """Parse date string to datetime object."""
    if s is None:
        return None
    if len(s) == 8:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
    elif len(s) == 4:
        return datetime.strptime(s, "%Y").replace(tzinfo=timezone.utc)
    return None

def process_item(path_item: str, collection, results_lookup) -> dict | None:
    """
    Process a single GeoTIFF URL to create a STAC item with datetime handling.

    Returns dict with item_id and item object, or None if processing fails.
    """
    href_item = path_item.replace("https:/", "https://")
    check = results_lookup.get(href_item)

    # Skip unreadable GeoTIFFs
    if check is None or not check["is_geotiff"]:
        print(f"Skipping unreadable GeoTIFF: {href_item}")
        return None

    item_id = path_item[len(PATH_S3):].replace("/", "-").removesuffix(".tif")

    # Extract datetime from path, use placeholder if not found
    date_str = date_extract_from_path(path_item)
    datetime_is_unknown = False

    if date_str:
        item_time = datetime_parse_item(date_str)
    else:
        # Placeholder datetime for items where date cannot be extracted
        # Common for albers10k2m dataset - see issue #12
        item_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
        datetime_is_unknown = True

    # Set media type based on COG validation results
    media_type = (
        "image/tiff; application=geotiff; profile=cloud-optimized"
        if check["is_cog"] else
        "image/tiff; application=geotiff"
    )

    try:
        item = rio_stac.stac.create_stac_item(
            path_item,
            id=item_id,
            asset_media_type=media_type,
            asset_name='image',
            asset_href=href_item,
            with_proj=True,
            collection=collection.id,
            collection_url=PATH_S3_JSON,
            asset_roles=["data"]
        )
        item.datetime = item_time
        item.assets['image'].href = href_item

        # Flag items with unknown datetime for future improvement
        if datetime_is_unknown:
            item.properties["datetime_unknown"] = True

        # Save item JSON locally (overwrites invalid version)
        path_item_json = f"{PATH_LOCAL}/{item_id}.json"
        item.save_object(dest_href=path_item_json, include_self_link=False)

        return {
            "id": item_id,
            "item": item
        }
    except Exception as e:
        print(f"Error processing {href_item}: {e}")
        return None

def main():
    print("=" * 80)
    print("Re-process Invalid STAC Items")
    print("=" * 80)
    print()

    # Load collection
    print(f"Loading collection: {PATH_COLLECTION}")
    collection = pystac.Collection.from_file(PATH_COLLECTION)
    collection.set_self_href(PATH_S3_JSON)
    print(f"✓ Collection loaded: {collection.id}")
    print()

    # Load validation cache
    print(f"Loading validation cache: {PATH_RESULTS_CSV}")
    if not os.path.exists(PATH_RESULTS_CSV):
        print(f"❌ Validation cache not found: {PATH_RESULTS_CSV}")
        return 1

    df_all = pd.read_csv(PATH_RESULTS_CSV)
    results_lookup = {
        row["url"].replace("https:/", "https://"): {"is_geotiff": row["is_geotiff"], "is_cog": row["is_cog"]}
        for _, row in df_all.iterrows()
    }
    print(f"✓ Loaded {len(results_lookup)} validation results")
    print()

    # Load URLs to re-process
    print(f"Loading invalid URLs: {INVALID_URLS_FILE}")
    if not os.path.exists(INVALID_URLS_FILE):
        print(f"❌ Invalid URLs file not found: {INVALID_URLS_FILE}")
        print("Run scripts/extract_invalid_urls.py first")
        return 1

    with open(INVALID_URLS_FILE) as f:
        urls_to_process = f.read().splitlines()

    print(f"✓ Loaded {len(urls_to_process)} URLs to re-process")
    print()

    # Process items in parallel
    print(f"Re-processing {len(urls_to_process)} items with 32 workers...")
    print("(This will overwrite invalid JSON files with valid versions)")
    print()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            results = list(filter(
                None,
                tqdm(
                    executor.map(
                        lambda url: process_item(url, collection, results_lookup),
                        urls_to_process
                    ),
                    total=len(urls_to_process),
                    desc="Re-processing items"
                )
            ))
    except Exception as e:
        print(f"❌ Parallel execution failed: {e}")
        return 1

    # Summary
    print()
    print("=" * 80)
    print("Re-processing Summary")
    print("=" * 80)
    print(f"Total URLs: {len(urls_to_process)}")
    print(f"✓ Successfully re-processed: {len(results)}")
    print(f"✗ Failed: {len(urls_to_process) - len(results)}")
    print()

    if len(results) > 0:
        # Count how many had datetime_unknown flag
        unknown_count = sum(1 for r in results if r['item'].properties.get('datetime_unknown', False))
        print(f"Items with datetime_unknown flag: {unknown_count}")
        print()

    print("=" * 80)
    print("Next Steps:")
    print("1. Run validation: python scripts/validate_stac_items.py")
    print("2. Verify all items are now valid")
    print("3. Sync to S3 and register to PgSTAC")
    print("=" * 80)

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
