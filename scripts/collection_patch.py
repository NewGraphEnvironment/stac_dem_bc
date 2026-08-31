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

Fields applied (#30, and the id since #34):
  id          THE collection id, so a rebuild and an in-place patch cannot
              drift. Renamed to stac-elevation-bc in #34 -- see COLLECTION_ID.
  title       derived from the id, and written to the root link as well: pystac
              copies it there, so the id is spelled three times in the file.
  providers   CC-BY-4.0 obliges attribution and the collection carried none.
              Roles split producer/licensor/host from processor, which matters
              if derived products (CHM) land in this collection later.
  keywords    so the collection is discoverable by something other than its id.
  description names `dem` as the bare-earth asset beside `dsm`.

The STAC Version Extension is handled SEPARATELY, by --version / --clear-version,
and deliberately sits outside collection_patch()'s idempotence contract. If the
version lived in that contract, --check would report "a patch is needed" after
every release and the monthly run would re-stamp with whatever it computed.

Usage:
    python scripts/collection_patch.py                       # patch $STAC_OUTPUT_DIR
    python scripts/collection_patch.py --path some/coll.json
    python scripts/collection_patch.py --check               # exit 1 if unpatched
    python scripts/collection_patch.py --version 1.1.0       # stamp a release
    python scripts/collection_patch.py --clear-version       # un-version (monthly)
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

VERSION_EXT = "https://stac-extensions.github.io/version/v1.2.0/schema.json"

# THE collection id. Every other definition in this repo reads this one:
# collection_create.py builds the file with it, catalogue_register.sh defaults
# to it, and item_create.py takes it from whichever collection.json it loaded.
#
# It was `stac-dem-bc` until #34. The collection stopped holding only DEMs at
# v1.0.0, when every item gained a `dsm` asset, and the name described one of
# two products -- less still as further products land here (#35).
#
# NOT the bucket. `s3://stac-dem-bc` keeps its name: it is IaC-managed in rtj,
# renaming it is a separate and larger decision, and it appears in every asset
# href and item link. The two used to be one fact spelled twice; they are now
# genuinely two, which is why catalogue_register.sh reconciles them at runtime
# rather than deriving one from the other.
COLLECTION_ID = "stac-elevation-bc"
COLLECTION_TITLE = f"Elevation models from British Columbia - {COLLECTION_ID}"

DESCRIPTION = (
    "Bare-earth digital elevation models and digital surface models from "
    "British Columbia - as served on lidarbc. Each item carries the bare-earth "
    "DEM as the `dem` asset and, where the same delivery published one, the "
    "digital surface model as the `dsm` asset. The two are the same flight over "
    "the same footprint at the same time."
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


def version_stamp(collection: dict, version: str) -> dict:
    """Stamp a release version via the STAC Version Extension.

    A version here means "the published catalogue is in this state" -- the
    NEWS.md convention -- not "the scripts changed. So it is only ever written
    at release time, by the release path, with a version passed in explicitly.

    Deliberately NOT derived from `git describe` inside this script. CI checks
    out at fetch-depth 1 with no tags, so git would fail there and the obvious
    fallback ("0.0.0") is a silently wrong stamp that makes every downstream
    comparison fail confusingly. Better to require the caller to know.
    """
    if not version:
        raise ValueError("refusing to stamp an empty version")
    exts = collection.get("stac_extensions") or []
    if VERSION_EXT not in exts:
        exts.append(VERSION_EXT)
    collection["stac_extensions"] = exts
    collection["version"] = version
    return collection


def version_clear(collection: dict) -> bool:
    """Remove the version stamp. Returns True if anything was removed.

    Called by the monthly run, because appending items makes the previous
    version FALSE rather than merely stale -- and false is worse than absent.
    Absent says "unversioned, go and check"; a wrong version says "you already
    have this one", which is the failure that let the API sit 38k items behind
    for a month without anyone noticing.
    """
    changed = collection.pop("version", None) is not None
    exts = collection.get("stac_extensions")
    if exts and VERSION_EXT in exts:
        exts.remove(VERSION_EXT)
        changed = True
        # An empty list is legal but noisy; drop the key to match a collection
        # that never carried an extension.
        if not exts:
            collection.pop("stac_extensions", None)
    return changed


def links_retitle(collection: dict, title: str) -> int:
    """Set the root link's title. Returns how many links changed.

    pystac copies the collection title onto the root link, so the collection id
    appears in the published file THREE times -- `id`, `title`, and here. A
    rename that moves two of the three leaves the root link asserting the old
    name, and nothing else in the pipeline reads it, so nothing would report it.
    """
    n = 0
    for link in collection.get("links", []):
        if link.get("rel") != "root":
            continue
        if link.get("title") != title:
            link["title"] = title
            n += 1
    return n


def collection_patch(collection: dict, allow_id_change: bool = False) -> tuple[dict, list[str]]:
    """Return the patched collection and the names of fields that changed.

    Pure and idempotent: re-running against an already-patched collection
    returns an empty change list, which is what `--check` reports on.

    The collection id belongs INSIDE this contract, unlike the version. A
    version's correct value changes at every release, so folding it in would
    make --check report "a patch is needed" forever. A collection id is a
    constant fact about the collection, exactly like providers and keywords --
    so the monthly run carries it, and --check can see a collection.json whose
    id regressed after a rebuild from a stale checkout.
    """
    # Changing the id is a MIGRATION, not a metadata patch, and it must not
    # happen as a side effect of the monthly run.
    #
    # The id lives inside this contract because it is a constant fact about the
    # collection (see COLLECTION_ID). But the monthly workflow calls this on
    # every month with new URLs, so without this guard the first cron after a
    # rename merges would publish a renamed collection.json over 102,460
    # unmigrated item bodies -- performing half a migration by itself. Nothing
    # would report it: the monthly audit reads the collection id from this very
    # file and inspects only the items staged this month, so it passes green
    # while S3 is split in two.
    #
    # Failing loudly is the point. It means "someone merged a rename and has not
    # dispatched the migration", which is exactly the state that needs a human.
    current = collection.get("id")
    if current is not None and current != COLLECTION_ID and not allow_id_change:
        raise RuntimeError(
            f"collection id would change: {current!r} -> {COLLECTION_ID!r}. "
            f"That is a migration, not a metadata patch, and the item bodies "
            f"must move with it. Run the migration (workflow_dispatch, "
            f"rename=true), which passes --allow-id-change. Patching the id "
            f"alone would publish a collection nothing in it belongs to."
        )

    changed = []
    n_links = links_encode(collection)
    if n_links:
        changed.append(f"{n_links} item link hrefs percent-encoded")
    n_titles = links_retitle(collection, COLLECTION_TITLE)
    if n_titles:
        changed.append("root link title")
    for field, value in (("id", COLLECTION_ID),
                         ("title", COLLECTION_TITLE),
                         ("providers", PROVIDERS),
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
    parser.add_argument("--allow-id-change", action="store_true",
                        help="permit the collection id to move. Migration path "
                             "ONLY -- the item bodies must move with it.")
    parser.add_argument("--check", action="store_true",
                        help="Report whether a patch is needed; exit 1 if it is. Writes nothing.")
    parser.add_argument("--version", default=None,
                        help="Stamp this release version via the STAC Version Extension. "
                             "Release path only; pass it explicitly (never derived from git here).")
    parser.add_argument("--clear-version", action="store_true",
                        help="Remove the version stamp. The monthly path uses this: appending "
                             "items makes the previous version false, and false is worse than absent.")
    args = parser.parse_args()

    if args.version and args.clear_version:
        parser.error("--version and --clear-version are mutually exclusive")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    path = args.path or f"{get_output_dir(test_only=False)}/collection.json"
    if not os.path.exists(path):
        logger.error("Collection not found: %s", path)
        return 2

    with open(path) as fh:
        collection = json.load(fh)

    # The hrefs, not merely how many. A count cannot see a link whose href
    # MUTATED, and #34 introduces exactly that risk: the collection id is being
    # renamed while the bucket in every item link keeps its old name, so a
    # careless substitution would rewrite 102,460 hrefs to a bucket that does
    # not exist. Item links are also the only complete list of what this
    # collection publishes -- the S3 copy is the only one there is.
    item_links_before = [
        l.get("href", "") for l in collection.get("links", []) if l.get("rel") == "item"
    ]

    try:
        collection, changed = collection_patch(
            collection, allow_id_change=args.allow_id_change)
    except RuntimeError as e:
        # The message IS the diagnostic. A traceback buries it in CI log noise,
        # and this one is read by whoever has to decide what to do next.
        logger.error("%s", e)
        return 1

    # Version handling sits outside collection_patch()'s contract on purpose --
    # see the module docstring. It is reported alongside, not folded in.
    if args.version:
        if collection.get("version") != args.version or \
                VERSION_EXT not in (collection.get("stac_extensions") or []):
            version_stamp(collection, args.version)
            changed.append(f"version -> {args.version}")
    elif args.clear_version:
        if version_clear(collection):
            changed.append("version cleared")

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
        item_links_after = [
            l.get("href", "")
            for l in json.load(open(tmp)).get("links", []) if l.get("rel") == "item"
        ]
        # links_encode is the ONE transform allowed to touch an item href.
        # Predicting the whole list rather than allowlisting a difference means
        # this cannot be widened later by adding an exception.
        expected = [encode_url_for_gdal(h) for h in item_links_before]
        if item_links_after != expected:
            if len(item_links_after) != len(expected):
                raise RuntimeError(
                    f"item link count changed during patch: "
                    f"{len(expected)} -> {len(item_links_after)}"
                )
            bad = [(e, a) for e, a in zip(expected, item_links_after) if e != a]
            raise RuntimeError(
                f"{len(bad)} item link href(s) changed during patch, e.g. {bad[0]}"
            )
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    logger.info("Patched %s (%d item links preserved)", path, len(item_links_before))
    return 0


if __name__ == "__main__":
    sys.exit(main())
