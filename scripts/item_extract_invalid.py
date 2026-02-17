#!/usr/bin/env python3
"""
Extract URLs for invalid STAC items from validation CSV.

This script:
1. Reads data/stac_item_validation.csv
2. Filters for invalid items (json_valid=False)
3. Converts item_id back to original URL
4. Writes URLs to data/urls_invalid_items.txt for re-processing

Usage:
    python scripts/item_extract_invalid.py
"""

import csv
import sys
from pathlib import Path

# Paths
VALIDATION_CSV = "data/stac_item_validation.csv"
OUTPUT_FILE = "data/urls_invalid_items.txt"
PATH_S3 = "https://nrs.objectstore.gov.bc.ca/gdwuts"

def item_id_to_url(item_id: str) -> str:
    """
    Convert item_id back to original URL.

    Reverses transformation: path_item[len(path_s3):].replace("/", "-").removesuffix(".tif")

    Example:
        albers10k2m-_completed_dem-dem_165_071 →
        https://nrs.objectstore.gov.bc.ca/gdwuts/albers10k2m/_completed_dem/dem_165_071.tif
    """
    # Replace first "-" with "/" to restore path structure
    url_path = item_id.replace("-", "/")
    return f"{PATH_S3}/{url_path}.tif"

def main():
    print("=" * 60)
    print("Extract URLs for Invalid STAC Items")
    print("=" * 60)
    print(f"Reading: {VALIDATION_CSV}")
    print()

    # Read validation CSV and extract invalid items
    invalid_items = []
    with open(VALIDATION_CSV, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['json_valid'].lower() == 'false':
                invalid_items.append(row)

    print(f"Found {len(invalid_items)} invalid items")

    if not invalid_items:
        print("✓ All items are valid!")
        return 0

    # Convert item_ids to URLs
    urls = []
    for item in invalid_items:
        url = item_id_to_url(item['item_id'])
        urls.append(url)

    # Write to output file
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        f.write('\n'.join(urls))

    print(f"Wrote {len(urls)} URLs to: {OUTPUT_FILE}")
    print()

    # Show sample URLs
    print("Sample URLs (first 5):")
    for url in urls[:5]:
        print(f"  {url}")

    if len(urls) > 5:
        print(f"  ... and {len(urls) - 5} more")

    print()
    print("=" * 60)
    print(f"Next: Re-process these {len(urls)} items with updated datetime handling")
    print("=" * 60)

    return 0

if __name__ == "__main__":
    sys.exit(main())
