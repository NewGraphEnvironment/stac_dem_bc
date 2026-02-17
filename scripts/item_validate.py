#!/usr/bin/env python3
"""
Validate STAC item JSON files and track validation status.

This script:
1. Reads STAC item JSONs from the production directory
2. Validates each using pystac
3. Records results in data/stac_item_validation.csv
4. Reports invalid items for investigation/removal

Usage:
    python scripts/item_validate.py [--collection COLLECTION_PATH] [--items-dir ITEMS_DIR]

Examples:
    # Validate all items in prod directory
    python scripts/item_validate.py

    # Validate items from specific paths
    python scripts/item_validate.py --items-dir /path/to/items
"""

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pystac
from tqdm import tqdm


# Default paths
DEFAULT_ITEMS_DIR = "/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc"
DEFAULT_COLLECTION_PATH = "/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc/collection.json"
VALIDATION_RESULTS_PATH = "data/stac_item_validation.csv"


def validate_item(item_path: str) -> Dict[str, any]:
    """
    Validate a single STAC item JSON file.

    Returns dict with validation results:
        {
            'item_path': str,
            'item_id': str,
            'json_exists': bool,
            'json_valid': bool,
            'validation_error': str or None,
            'last_checked': str (ISO timestamp)
        }
    """
    result = {
        'item_path': item_path,
        'item_id': Path(item_path).stem,
        'json_exists': False,
        'json_valid': False,
        'validation_error': None,
        'last_checked': datetime.now().isoformat()
    }

    # Check if file exists
    if not os.path.exists(item_path):
        result['validation_error'] = "File not found"
        return result

    result['json_exists'] = True

    # Try to load and validate with pystac
    try:
        # Load JSON
        with open(item_path, 'r') as f:
            item_dict = json.load(f)

        # Validate with pystac
        item = pystac.Item.from_dict(item_dict)
        item.validate()

        result['json_valid'] = True

    except json.JSONDecodeError as e:
        result['validation_error'] = f"JSON decode error: {str(e)[:100]}"
    except pystac.errors.STACValidationError as e:
        result['validation_error'] = f"STAC validation error: {str(e)[:100]}"
    except Exception as e:
        result['validation_error'] = f"Unexpected error: {type(e).__name__}: {str(e)[:100]}"

    return result


def load_existing_results(results_path: str) -> Dict[str, Dict]:
    """Load existing validation results if they exist."""
    existing = {}

    if os.path.exists(results_path):
        with open(results_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert string booleans back to bool
                row['json_exists'] = row['json_exists'].lower() == 'true'
                row['json_valid'] = row['json_valid'].lower() == 'true'
                existing[row['item_id']] = row

    return existing


def save_results(results: List[Dict], results_path: str):
    """Save validation results to CSV."""
    os.makedirs(os.path.dirname(results_path), exist_ok=True)

    fieldnames = ['item_path', 'item_id', 'json_exists', 'json_valid', 'validation_error', 'last_checked']

    with open(results_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    parser = argparse.ArgumentParser(description="Validate STAC item JSON files")
    parser.add_argument(
        '--items-dir',
        default=DEFAULT_ITEMS_DIR,
        help=f"Directory containing item JSON files (default: {DEFAULT_ITEMS_DIR})"
    )
    parser.add_argument(
        '--collection',
        default=DEFAULT_COLLECTION_PATH,
        help=f"Path to collection.json (default: {DEFAULT_COLLECTION_PATH})"
    )
    parser.add_argument(
        '--incremental',
        action='store_true',
        help="Only validate items not in existing results (faster)"
    )
    parser.add_argument(
        '--output',
        default=VALIDATION_RESULTS_PATH,
        help=f"Output CSV path (default: {VALIDATION_RESULTS_PATH})"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("STAC Item Validation")
    print("=" * 60)
    print(f"Items directory: {args.items_dir}")
    print(f"Collection: {args.collection}")
    print(f"Output: {args.output}")
    print(f"Mode: {'Incremental' if args.incremental else 'Full'}")
    print()

    # Find all item JSON files (exclude collection.json)
    item_pattern = os.path.join(args.items_dir, "*-*.json")
    item_files = glob.glob(item_pattern)

    if not item_files:
        print(f"❌ No item JSON files found in {args.items_dir}")
        return 1

    print(f"Found {len(item_files)} item JSON files")

    # Load existing results if incremental mode
    existing_results = {}
    if args.incremental:
        existing_results = load_existing_results(args.output)
        print(f"Loaded {len(existing_results)} existing validation results")

    # Filter items to validate
    items_to_validate = []
    if args.incremental and existing_results:
        for item_file in item_files:
            item_id = Path(item_file).stem
            if item_id not in existing_results:
                items_to_validate.append(item_file)
        print(f"Incremental: Validating {len(items_to_validate)} new items")
    else:
        items_to_validate = item_files
        print(f"Full validation: Validating all {len(items_to_validate)} items")

    if not items_to_validate:
        print("✓ No new items to validate")
        return 0

    print()
    print("Validating items...")

    # Validate items with progress bar
    validation_results = []
    for item_file in tqdm(items_to_validate, desc="Validating"):
        result = validate_item(item_file)
        validation_results.append(result)

    # Combine with existing results if incremental
    if args.incremental and existing_results:
        all_results = list(existing_results.values()) + validation_results
    else:
        all_results = validation_results

    # Save results
    save_results(all_results, args.output)

    # Generate summary
    print()
    print("=" * 60)
    print("Validation Summary")
    print("=" * 60)

    total = len(all_results)
    valid = sum(1 for r in all_results if r['json_valid'])
    invalid = total - valid

    print(f"Total items: {total}")
    print(f"✓ Valid: {valid} ({valid/total*100:.1f}%)")
    print(f"✗ Invalid: {invalid} ({invalid/total*100:.1f}%)")
    print()

    # Show invalid items
    invalid_items = [r for r in all_results if not r['json_valid']]
    if invalid_items:
        print("Invalid items:")
        for item in invalid_items[:10]:  # Show first 10
            print(f"  ✗ {item['item_id']}")
            print(f"    Error: {item['validation_error']}")

        if len(invalid_items) > 10:
            print(f"  ... and {len(invalid_items) - 10} more")

        print()
        print(f"Full results saved to: {args.output}")
    else:
        print("✓ All items are valid!")

    print()
    print("=" * 60)

    return 0 if invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
