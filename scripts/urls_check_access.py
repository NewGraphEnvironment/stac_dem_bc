#!/usr/bin/env python3
"""Check source GeoTIFF URL accessibility on BC objectstore.

Performs HTTP HEAD requests against source URLs to detect permission issues
(e.g., 403 Forbidden). Produces a CSV report shareable with GeoBC.

Usage:
    python scripts/urls_check_access.py                              # Check new URLs only
    python scripts/urls_check_access.py --urls-file data/urls_list.txt  # Specify URL file
    python scripts/urls_check_access.py --recheck                    # Re-check all URLs
"""

import argparse
import concurrent.futures
import logging
import sys

import pandas as pd
from tqdm import tqdm

from stac_utils import check_url_accessible, fix_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PATH_CACHE = "data/urls_access_checks.csv"


def main():
    parser = argparse.ArgumentParser(description="Check source URL accessibility")
    parser.add_argument(
        "--urls-file", default="data/urls_list.txt",
        help="File containing URLs to check (default: data/urls_list.txt)",
    )
    parser.add_argument(
        "--recheck", action="store_true",
        help="Re-check all URLs, ignoring cache",
    )
    parser.add_argument(
        "--workers", type=int, default=16,
        help="Number of parallel workers (default: 16)",
    )
    parser.add_argument(
        "--timeout", type=int, default=10,
        help="HTTP timeout in seconds (default: 10)",
    )
    args = parser.parse_args()

    # Load URLs
    with open(args.urls_file) as f:
        all_urls = [fix_url(line.strip()) for line in f if line.strip()]
    logger.info("Loaded %d URLs from %s", len(all_urls), args.urls_file)

    # Load cache
    if not args.recheck:
        try:
            df_cached = pd.read_csv(PATH_CACHE)
            cached_urls = set(df_cached["url"])
            logger.info("Loaded %d cached results from %s", len(df_cached), PATH_CACHE)
        except FileNotFoundError:
            df_cached = pd.DataFrame()
            cached_urls = set()
    else:
        df_cached = pd.DataFrame()
        cached_urls = set()
        logger.info("Recheck mode: ignoring cache")

    # Determine which URLs need checking
    urls_to_check = [u for u in all_urls if u not in cached_urls]
    logger.info("%d URLs to check (%d already cached)", len(urls_to_check), len(all_urls) - len(urls_to_check))

    if not urls_to_check:
        logger.info("Nothing to check")
        # Still report from cache
        if len(df_cached) > 0:
            n_inaccessible = (~df_cached["accessible"]).sum()
            if n_inaccessible > 0:
                logger.warning("%d URLs are inaccessible (from cache)", n_inaccessible)
                sys.exit(1)
        sys.exit(0)

    # Run checks in parallel
    def _check(url):
        return check_url_accessible(url, timeout=args.timeout)

    logger.info("Checking %d URLs with %d workers...", len(urls_to_check), args.workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(tqdm(
            executor.map(_check, urls_to_check),
            total=len(urls_to_check),
            desc="Checking URLs",
        ))

    # Combine with cache and save
    df_new = pd.DataFrame(results)
    df_all = pd.concat([df_cached, df_new], ignore_index=True) if len(df_cached) > 0 else df_new
    df_all.to_csv(PATH_CACHE, index=False)
    logger.info("Saved %d results to %s", len(df_all), PATH_CACHE)

    # Summary
    n_checked = len(df_new)
    n_accessible = df_new["accessible"].sum()
    n_inaccessible = n_checked - n_accessible

    logger.info("Results: %d accessible, %d inaccessible (out of %d checked)", n_accessible, n_inaccessible, n_checked)

    if n_inaccessible > 0:
        logger.warning("Inaccessible URLs:")
        for _, row in df_new[~df_new["accessible"]].iterrows():
            logger.warning("  %s → %s (%s)", row["url"], row["status_code"], row["error"])
        sys.exit(1)


if __name__ == "__main__":
    main()
