"""The fields #2 adds to an item, applied the same way by every writer.

item_create.py, item_reprocess.py and the one-time rewrite (footprint_apply.py)
all call `item_fields_apply` on an item's dict. One path, so an item built in a
monthly run and one rewritten in place cannot differ in what they carry.
"""

import logging
import os

import pystac

from footprint import FOOTPRINT_METHODS, item_footprint_apply
from footprint_extract import CACHE, cache_load
from stac_utils import ASSET_DEM

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


def item_fields_apply(item: dict, url: str, footprints: dict) -> list[str]:
    """Apply every #2 field to an item dict in place. Returns what changed."""
    return item_footprint_apply(item, footprints.get(url), ASSET_DEM)


def pystac_item_fields_apply(item: pystac.Item, url: str, footprints: dict) -> pystac.Item:
    """The same, for an Item a builder has just made."""
    d = item.to_dict(include_self_link=False, transform_hrefs=False)
    item_fields_apply(d, url, footprints)
    return pystac.Item.from_dict(d)
