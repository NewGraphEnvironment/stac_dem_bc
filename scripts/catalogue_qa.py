#!/usr/bin/env python3
"""
QA script to compare local STAC catalogue with S3 before syncing.

This script:
1. Samples a percentage of local STAC items
2. Downloads their S3 counterparts
3. Compares key fields (id, datetime, properties)
4. Reports differences and errors
5. Logs results to logs/ directory

Usage:
    python scripts/catalogue_qa.py [--sample-percent 1] [--max-items 100]

Examples:
    # Check 1% sample (default)
    python scripts/catalogue_qa.py

    # Check 5% sample, max 200 items
    python scripts/catalogue_qa.py --sample-percent 5 --max-items 200
"""

import argparse
import json
import subprocess
import random
import os
import sys
from datetime import datetime
from pathlib import Path


def download_s3_item(s3_path: str, local_temp_path: str, profile: str = "airvine") -> bool:
    """Download item from S3 to local temp location."""
    result = subprocess.run(
        ["aws", "s3", "cp", s3_path, local_temp_path, "--profile", profile],
        capture_output=True,
        text=True
    )
    return result.returncode == 0, result.stderr.strip()


def compare_items(local_json: dict, s3_json: dict, item_file: str) -> list:
    """Compare key fields between local and S3 items."""
    diffs = []

    # Compare ID
    if local_json.get('id') != s3_json.get('id'):
        diffs.append(f"ID mismatch: local='{local_json.get('id')}' vs s3='{s3_json.get('id')}'")

    # Compare datetime
    local_dt = local_json.get('properties', {}).get('datetime')
    s3_dt = s3_json.get('properties', {}).get('datetime')
    if local_dt != s3_dt:
        diffs.append(f"Datetime mismatch: local='{local_dt}' vs s3='{s3_dt}'")

    # Compare datetime_unknown flag
    local_unknown = local_json.get('properties', {}).get('datetime_unknown')
    s3_unknown = s3_json.get('properties', {}).get('datetime_unknown')
    if local_unknown != s3_unknown:
        diffs.append(f"datetime_unknown mismatch: local={local_unknown} vs s3={s3_unknown}")

    # Compare geometry
    if local_json.get('geometry') != s3_json.get('geometry'):
        diffs.append("Geometry mismatch")

    # Compare bbox
    if local_json.get('bbox') != s3_json.get('bbox'):
        diffs.append("BBox mismatch")

    # Compare asset count
    local_assets = len(local_json.get('assets', {}))
    s3_assets = len(s3_json.get('assets', {}))
    if local_assets != s3_assets:
        diffs.append(f"Asset count mismatch: local={local_assets} vs s3={s3_assets}")

    return diffs


def main():
    parser = argparse.ArgumentParser(description="QA check local STAC catalogue against S3")
    parser.add_argument(
        '--sample-percent',
        type=float,
        default=1.0,
        help="Percentage of items to sample (default: 1.0)"
    )
    parser.add_argument(
        '--max-items',
        type=int,
        default=100,
        help="Maximum number of items to check (default: 100)"
    )
    parser.add_argument(
        '--local-dir',
        default="/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc",
        help="Local STAC directory"
    )
    parser.add_argument(
        '--s3-bucket',
        default="s3://stac-dem-bc",
        help="S3 bucket path"
    )
    parser.add_argument(
        '--profile',
        default="airvine",
        help="AWS profile to use"
    )

    args = parser.parse_args()

    # Setup logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{timestamp}_qa_catalogue_s3_comparison.log"

    def log(msg):
        """Log to both console and file."""
        print(msg)
        with open(log_file, 'a') as f:
            f.write(msg + '\n')

    log("=" * 80)
    log("STAC Catalogue QA: Local vs S3 Comparison")
    log("=" * 80)
    log(f"Timestamp: {timestamp}")
    log(f"Local directory: {args.local_dir}")
    log(f"S3 bucket: {args.s3_bucket}")
    log(f"Sample: {args.sample_percent}% (max {args.max_items} items)")
    log(f"Log file: {log_file}")
    log("")

    # Get all local items
    local_dir = Path(args.local_dir)
    if not local_dir.exists():
        log(f"❌ Error: Local directory not found: {local_dir}")
        return 1

    all_items = [f.name for f in local_dir.glob("*.json") if f.name != "collection.json"]
    log(f"Found {len(all_items)} local items")

    # Calculate sample size
    sample_size = int(len(all_items) * (args.sample_percent / 100))
    sample_size = min(sample_size, args.max_items)
    sample_size = max(1, sample_size)  # At least 1 item

    log(f"Sampling {sample_size} items for comparison")
    log("")

    # Random sample
    sample_items = random.sample(all_items, sample_size)

    # Compare items
    differences = []
    errors = []
    temp_dir = Path("/tmp/stac_qa")
    temp_dir.mkdir(exist_ok=True)

    log("Comparing items...")
    for i, item_file in enumerate(sample_items, 1):
        if i % 10 == 0:
            log(f"  Progress: {i}/{sample_size}")

        local_path = local_dir / item_file
        s3_path = f"{args.s3_bucket}/{item_file}"
        temp_file = temp_dir / item_file

        # Download from S3
        success, error_msg = download_s3_item(s3_path, str(temp_file), args.profile)

        if not success:
            errors.append(f"{item_file}: S3 download failed - {error_msg}")
            continue

        # Load and compare JSONs
        try:
            with open(local_path) as f:
                local_json = json.load(f)
            with open(temp_file) as f:
                s3_json = json.load(f)

            diffs = compare_items(local_json, s3_json, item_file)
            if diffs:
                differences.append({
                    'file': item_file,
                    'diffs': diffs
                })
        except Exception as e:
            errors.append(f"{item_file}: Comparison error - {str(e)}")
        finally:
            # Cleanup temp file
            if temp_file.exists():
                temp_file.unlink()

    # Cleanup temp directory
    if temp_dir.exists():
        temp_dir.rmdir()

    # Report results
    log("")
    log("=" * 80)
    log("QA Results")
    log("=" * 80)
    log(f"Items checked: {sample_size}")
    log(f"Differences found: {len(differences)}")
    log(f"Errors: {len(errors)}")
    log("")

    if differences:
        log("Items with differences:")
        for item in differences:
            log(f"\n  {item['file']}:")
            for diff in item['diffs']:
                log(f"    - {diff}")
        log("")

    if errors:
        log("Errors encountered:")
        for error in errors[:20]:  # Limit to first 20 errors
            log(f"  - {error}")
        if len(errors) > 20:
            log(f"  ... and {len(errors) - 20} more errors")
        log("")

    if not differences and not errors:
        log("✓ All sampled items match between local and S3")
        log("")

    # Summary
    log("=" * 80)
    if differences or errors:
        log("⚠️  DIFFERENCES OR ERRORS FOUND - Review log before syncing")
        log("=" * 80)
        return 1
    else:
        log("✅ QA PASSED - Safe to sync to S3")
        log("=" * 80)
        return 0


if __name__ == "__main__":
    sys.exit(main())
