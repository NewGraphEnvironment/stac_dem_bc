"""
Shared utilities for STAC DEM BC scripts.

Contains common functions and configuration used across:
- item_create.py
- item_reprocess.py
- item_validate.py
- collection_create.py
"""

import re
import subprocess
from datetime import datetime, timezone


# =============================================================================
# Path Configuration
# =============================================================================

PATH_S3_STAC = "https://stac-dem-bc.s3.amazonaws.com"
PATH_S3_JSON = f"{PATH_S3_STAC}/collection.json"
PATH_S3 = "https://nrs.objectstore.gov.bc.ca/gdwuts"
PATH_RESULTS_CSV = "data/stac_geotiff_checks.csv"

# BC bounding box (hardcoded — provincial boundary is stable)
BBOX_BC = [-140, 48, -114, 60]


def get_output_dir(test_only: bool) -> str:
    """Return the local output directory based on mode."""
    if test_only:
        return "/Users/airvine/Projects/gis/stac_dem_bc/stac/dev/stac_dem_bc"
    return "/Users/airvine/Projects/gis/stac_dem_bc/stac/prod/stac_dem_bc"


# =============================================================================
# Date Extraction
# =============================================================================

def date_extract_from_path(s: str) -> str | None:
    """Extract date string (YYYYMMDD or YYYY) from a GeoTIFF URL path.

    Tries two patterns:
    1. YYYYMMDD or YYYY after _utmXX_ (e.g., _utm10_20230415.tif)
    2. /YYYY/ directory in path (e.g., /2023/some_file.tif)

    Returns date string or None if no date found.
    """
    # Try to extract YYYYMMDD or YYYY after _utmXX_
    match = re.search(r'_utm\d{1,2}_([0-9]{4,8})', s)
    if match:
        val = match.group(1)
        if val.isdigit():
            year = int(val[:4])
            if 2000 <= year <= 2050:
                return val

    # Fallback: look for /YYYY/ in the path
    fallback = re.search(r'/([2][0-9]{3})/', s)
    if fallback:
        year = int(fallback.group(1))
        if 2000 <= year <= 2050:
            return str(year)

    return None


def datetime_parse_item(s: str | None) -> datetime | None:
    """Parse date string to timezone-aware datetime object.

    Accepts:
    - 8-digit string (YYYYMMDD) → datetime at midnight UTC
    - 4-digit string (YYYY) → January 1 of that year, UTC
    """
    if s is None:
        return None
    if len(s) == 8:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
    elif len(s) == 4:
        return datetime.strptime(s, "%Y").replace(tzinfo=timezone.utc)
    return None


# =============================================================================
# GeoTIFF Validation
# =============================================================================

def check_geotiff_cog(url: str) -> dict:
    """Validate GeoTIFF and COG status using rio cogeo validate.

    Returns dict with url, is_geotiff (readable), and is_cog (cloud-optimized).
    """
    try:
        result = subprocess.run(
            ["rio", "cogeo", "validate", f"/vsicurl/{url}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False
        )
        output = result.stdout.decode() + result.stderr.decode()
        return {
            "url": url,
            "is_geotiff": "is NOT a valid cloud optimized GeoTIFF" in output or "is a valid cloud optimized GeoTIFF" in output,
            "is_cog": "is a valid cloud optimized GeoTIFF" in output
        }
    except FileNotFoundError:
        raise RuntimeError("`rio cogeo` is not installed or not in PATH. Install with: pip install rio-cogeo")


# =============================================================================
# URL Helpers
# =============================================================================

def fix_url(url: str) -> str:
    """Fix malformed URLs with single slash after https:."""
    if url.startswith("https:/") and not url.startswith("https://"):
        return url.replace("https:/", "https://", 1)
    return url


def url_to_item_id(url: str) -> str:
    """Convert a GeoTIFF URL to a STAC item ID."""
    return url[len(PATH_S3):].lstrip("/").replace("/", "-").removesuffix(".tif")
