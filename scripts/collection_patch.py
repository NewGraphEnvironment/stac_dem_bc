#!/usr/bin/env python3
"""Apply collection-level metadata to an existing collection.json, idempotently.

Why this exists separately from collection_create.py: the monthly workflow
FETCHES collection.json from S3 rather than regenerating it, because the file
carries ~98k item links that only exist in the published copy. So a change to
collection_create.py alone would never reach the live collection -- it only runs
on a full rebuild, which has not happened since the metadata was first written.

Edits the JSON directly rather than round-tripping through pystac. A pystac
load/save of a 98k-link collection rewrites every link object; a targeted edit
touches only the fields named here, so a diff of the published file shows the
metadata change and nothing else.

Fields applied (see issue #30):
  providers   CC-BY-4.0 obliges attribution and the collection carried none.
              Roles split producer/licensor/host from processor, which matters
              if derived products (CHM) land in this collection later.
  keywords    so the collection is discoverable by something other than its id.
  description names `image` as the bare-earth DEM, now that items also carry a
              `dsm` asset and `image` no longer disambiguates on its own.

Usage:
    python scripts/collection_patch.py                       # patch $STAC_OUTPUT_DIR
    python scripts/collection_patch.py --path some/coll.json
    python scripts/collection_patch.py --check               # exit 1 if unpatched
"""

import argparse
import json
import logging
import os
import sys

from stac_utils import encode_url_for_gdal, get_output_dir

logger = logging.getLogger(__name__)

PROVIDERS = [
    {
        "name": "Province of British Columbia",
        "roles": ["producer", "licensor", "host"],
        # lidar.gov.bc.ca, not the www2.gov.bc.ca content path -- the latter
        # 404s. The repo README has always used this one.
        "url": "https://lidar.gov.bc.ca/",
    },
    {
        "name": "New Graph Environment",
        "roles": ["processor"],
        "url": "https://www.newgraphenvironment.com",
    },
]

KEYWORDS = ["lidar", "elevation", "dem", "dsm", "british columbia", "lidarbc"]

DESCRIPTION = (
    "A collection of Digital Elevation Models from British Columbia - as served "
    "on lidarbc. Each item carries the bare-earth DEM as the `image` asset and, "
    "where the same delivery published one, the digital surface model as the "
    "`dsm` asset. The two are the same flight over the same footprint at the "
    "same time. `image` is named for backward compatibility with existing "
    "consumers; it is the bare-earth DEM."
)


def links_encode(collection: dict) -> int:
    """Percent-encode spaces in item link hrefs. Returns how many changed.

    #25 fixed the code that writes new links but nothing ever repaired the
    published collection, so 90 links still carry literal spaces -- and they are
    self-perpetuating, because the monthly run fetches this file, appends to it,
    and writes the same bad links back out.

    A raw-space href does not merely look untidy: curl cannot even form the
    request (exit 3, no status), while the encoded form returns 200. Encoding
    spaces alone is sufficient -- the parentheses in `..._2018 (2).tif` are
    legal URL sub-delims and resolve as-is -- and space-only is exactly the
    transform item_create.py already applies to new links, so this makes the
    legacy ones consistent rather than differently-encoded.
    """
    n = 0
    for link in collection.get("links", []):
        if link.get("rel") != "item":
            continue
        href = link.get("href", "")
        fixed = encode_url_for_gdal(href)
        if fixed != href:
            link["href"] = fixed
            n += 1
    return n


def collection_patch(collection: dict) -> tuple[dict, list[str]]:
    """Return the patched collection and the names of fields that changed.

    Pure and idempotent: re-running against an already-patched collection
    returns an empty change list, which is what `--check` reports on.
    """
    changed = []
    n_links = links_encode(collection)
    if n_links:
        changed.append(f"{n_links} item link hrefs percent-encoded")
    for field, value in (("providers", PROVIDERS),
                         ("keywords", KEYWORDS),
                         ("description", DESCRIPTION)):
        if collection.get(field) != value:
            collection[field] = value
            changed.append(field)
    return collection, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch STAC collection metadata")
    parser.add_argument("--path", default=None,
                        help="collection.json to patch (default: $STAC_OUTPUT_DIR/collection.json)")
    parser.add_argument("--check", action="store_true",
                        help="Report whether a patch is needed; exit 1 if it is. Writes nothing.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    path = args.path or f"{get_output_dir(test_only=False)}/collection.json"
    if not os.path.exists(path):
        logger.error("Collection not found: %s", path)
        return 2

    with open(path) as fh:
        collection = json.load(fh)

    item_links_before = sum(1 for l in collection.get("links", []) if l.get("rel") == "item")

    collection, changed = collection_patch(collection)

    if not changed:
        logger.info("Collection metadata already current: %s", path)
        return 0

    logger.info("Fields needing update: %s", ", ".join(changed))
    if args.check:
        return 1

    # Write via a temp file and rename, so an interrupted write cannot leave a
    # truncated collection.json -- it is the file every consumer resolves items
    # through, and the S3 copy is the only complete one.
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(collection, fh)
        item_links_after = sum(
            1 for l in json.load(open(tmp)).get("links", []) if l.get("rel") == "item"
        )
        if item_links_after != item_links_before:
            raise RuntimeError(
                f"item link count changed during patch: {item_links_before} -> {item_links_after}"
            )
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    logger.info("Patched %s (%d item links preserved)", path, item_links_before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
