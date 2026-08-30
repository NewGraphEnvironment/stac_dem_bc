"""
Pure helpers for pgstac registration: which items to register, and the NDJSON
that gets loaded.

Exists as a Python module rather than inline shell for two reasons. The first is
testability — every function here is reachable from `tests/`. The second is a
correctness constraint: stdin is already spoken for twice in the registration
path (a `python3 - "$@" <<'EOF'` heredoc consumes it, and `ssh ... < payload`
needs it), so the NDJSON assembler cannot be a heredoc. It has to be a file.

Used by scripts/item_register.sh, scripts/collection_register.sh and
scripts/catalogue_register.sh.
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests

from stac_utils import PATH_S3, PATH_S3_STAC, url_to_item_id

# The live API. Registration verifies against what the API actually serves,
# not against what we believe we sent.
API_DEFAULT = "https://images.a11s.one"

# Transient-failure retries on API reads. The verifier runs AFTER the upsert has
# already succeeded, so an unretried 5xx would turn a completed registration into
# a traceback -- the same fail-toward-abort shape this whole change exists to
# remove. An --all verify is ~205 POSTs; at that count "transient" is routine.
RETRIES = 3

# Keyset paging page size. The API has no aggregation extension and returns a
# null numberMatched, so enumerating ids is the only way to count anything —
# 102,460 ids came back in 11 requests at this size.
PAGE_SIZE = 10000


def _post(session, url, body, timeout=180):
    """POST with retries on transient failures. Raises after the last attempt."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.post(url, json=body, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(attempt * 2)
    raise RuntimeError(f"API request failed after {RETRIES} attempts: {last}") from last


# =============================================================================
# Item ids
# =============================================================================

def item_ids_from_urls(urls) -> list[str]:
    """Map source GeoTIFF URLs to STAC item ids.

    Raises on any URL outside the objectstore prefix. `url_to_item_id` slices
    by prefix *length* without checking the prefix matches, so an unexpected
    host silently yields a mangled id rather than an error — the id would look
    plausible and register against nothing.
    """
    ids = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        if not url.startswith(PATH_S3):
            raise ValueError(f"URL is not on the objectstore ({PATH_S3}): {url}")
        if not url.endswith(".tif"):
            raise ValueError(f"URL is not a GeoTIFF: {url}")
        ids.append(url_to_item_id(url))
    return ids


def _href_to_id(href: str) -> str:
    """Item id from a published item-link href.

    Hrefs are percent-encoded (#25) — 90 of them carry literal spaces and
    parentheses — so the id is the *decoded* basename minus `.json`.
    """
    name = href.rsplit("/", 1)[-1]
    if not name.endswith(".json"):
        raise ValueError(f"item link does not end in .json: {href}")
    stem = name[: -len(".json")]
    # Decode %20 ONLY, because that is the exact inverse of the encoder that
    # produced these hrefs: stac_utils.encode_url_for_gdal does
    # `url.replace(" ", "%20")` and touches nothing else.
    #
    # urllib.parse.unquote() would decode every escape, which is not the inverse.
    # An id containing a literal '%' (never encoded on the way out, since the
    # encoder only handles spaces) would come back decoded into a DIFFERENT id --
    # permanently "missing" and permanently orphaned at the same time, with
    # --drift failing verification after a successful upsert every single month.
    # No such id exists today; this keeps it that way rather than relying on it.
    return stem.replace("%20", " ")


def collection_item_links(path) -> list[tuple[str, str]]:
    """Read (item_id, href) for every item link in a published collection.json.

    Returns hrefs verbatim — already percent-encoded, so they are usable as
    fetch URLs as-is. Never rebuild a fetch URL from the decoded id.
    """
    with open(path) as f:
        collection = json.load(f)
    out = []
    for link in collection.get("links", []):
        if link.get("rel") != "item":
            continue
        href = link["href"]
        out.append((_href_to_id(href), href))
    return out


def ids_registered(collection_id: str, api: str = API_DEFAULT,
                   page_size: int = PAGE_SIZE, session=None) -> list[str]:
    """Every item id the API currently serves for a collection.

    Keyset paging over POST /search with fields:{include:["id"]}. The API has
    no /aggregate endpoint (404) and returns numberMatched: null, so this
    enumeration is the only available count.
    """
    session = session or requests.Session()
    body = {
        "collections": [collection_id],
        "limit": page_size,
        "fields": {"include": ["id"]},
    }
    url = f"{api.rstrip('/')}/search"
    ids: list[str] = []
    seen_tokens: set[str] = set()
    while True:
        page = _post(session, url, body)
        ids.extend(f["id"] for f in page.get("features", []))
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            # The only NORMAL way out: the server says there is no more.
            break
        token = (nxt.get("body") or {}).get("token")
        if not token:
            # A next link with no continuation token means the enumeration
            # stopped early. Raising matters more than it looks: a short id list
            # makes every unenumerated item report as "missing", which would
            # send a --drift run to re-register a catalogue that was fine.
            raise RuntimeError(
                f"paging stopped early: 'next' link with no token after {len(ids)} ids"
            )
        if token in seen_tokens:
            raise RuntimeError(f"paging token repeated after {len(ids)} ids — not advancing")
        seen_tokens.add(token)
        # stac-fastapi returns the continuation in the link body, not the href
        body = {**body, **nxt["body"]}
    return ids


def search_body(ids) -> dict:
    """The POST /search body for an id lookup.

    Pure, and separated out purely so it can be asserted on offline. The
    `limit` is the whole reason: the API's DEFAULT LIMIT IS 10, so a body
    without one silently returns the first 10 of however many ids were asked
    for. That reads as "590 of my 600 items are missing" and fails a
    verification whose subject was in fact fine. Measured against the live API:
    600 registered ids, no limit -> 10 features; limit=600 -> 600.
    """
    ids = list(ids)
    return {
        "ids": ids,
        "limit": max(len(ids), 1),
        "fields": {"include": ["id"]},
    }


def ids_serving(ids, api: str = API_DEFAULT, chunk: int = 500, session=None) -> set:
    """Which of these ids the API actually serves, as a set.

    Batched because a very long id list is a real request-size ceiling. Returns
    a SET so the caller compares sets — a /search omits ids that do not exist
    without erroring, so a returned count proves nothing.
    """
    session = session or requests.Session()
    url = f"{api.rstrip('/')}/search"
    ids = list(ids)
    got = set()
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        page = _post(session, url, search_body(batch))
        got.update(f["id"] for f in page.get("features", []))
    return got


def ids_diff(published, registered) -> tuple[list[str], list[str]]:
    """(missing, orphaned) — reported in BOTH directions.

    missing  = published but not registered (the API is behind S3)
    orphaned = registered but not published (a delete never propagated)

    Set equality is the only sound check here: a /search on a list of ids
    returns the ones that exist and silently omits the rest, so asserting on
    the returned *count* passes vacuously.
    """
    p, r = set(published), set(registered)
    return sorted(p - r), sorted(r - p)


# =============================================================================
# NDJSON
# =============================================================================

def ndjson_write(paths, out) -> int:
    """Compact one item JSON per line. Returns the number of lines written.

    json.dumps never emits a raw newline, so a record cannot straddle lines
    however odd the source formatting is.
    """
    n = 0
    with open(out, "w") as fh:
        for path in paths:
            path = path.rstrip("\n")
            if not path:
                continue
            with open(path) as src:
                doc = json.load(src)
            fh.write(json.dumps(doc, separators=(",", ":")))
            fh.write("\n")
            n += 1
    return n


# =============================================================================
# CLI
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ids-published", help="item ids from a collection.json")
    p.add_argument("--collection-file", required=True)

    p = sub.add_parser("hrefs-published", help="tab-separated id and fetch href")
    p.add_argument("--collection-file", required=True)
    p.add_argument("--ids-file", help="restrict to these ids (one per line)")

    p = sub.add_parser("ids-from-urls", help="item ids from source GeoTIFF URLs")
    p.add_argument("--urls-file", required=True)

    p = sub.add_parser("ids-registered", help="item ids the API currently serves")
    p.add_argument("--collection-id", required=True)
    p.add_argument("--api", default=API_DEFAULT)

    p = sub.add_parser("diff", help="published vs registered, both directions")
    p.add_argument("--collection-file", required=True)
    p.add_argument("--collection-id", required=True)
    p.add_argument("--api", default=API_DEFAULT)
    p.add_argument("--missing-out", help="write missing ids here")
    p.add_argument("--orphaned-out", help="write orphaned ids here")

    p = sub.add_parser("ndjson", help="build NDJSON from item paths on stdin")
    p.add_argument("--out", required=True)

    p = sub.add_parser("verify-serving",
                       help="assert every id in a file is served by the API")
    p.add_argument("--ids-file", required=True)
    p.add_argument("--api", default=API_DEFAULT)

    args = ap.parse_args()

    if args.cmd == "ids-published":
        for item_id, _ in collection_item_links(args.collection_file):
            print(item_id)

    elif args.cmd == "hrefs-published":
        links = collection_item_links(args.collection_file)
        if args.ids_file:
            wanted = {
                line.rstrip("\n") for line in open(args.ids_file) if line.strip()
            }
            links = [(i, h) for i, h in links if i in wanted]
            # An id with no published link cannot be fetched, so it cannot be
            # registered. Say so rather than silently returning a short list —
            # a shortfall discovered later looks like a network failure.
            found = {i for i, _ in links}
            unknown = wanted - found
            if unknown:
                raise SystemExit(
                    f"{len(unknown)} requested id(s) have no item link in "
                    f"{args.collection_file}, e.g. {sorted(unknown)[:3]}"
                )
        for item_id, href in links:
            print(f"{item_id}\t{href}")

    elif args.cmd == "ids-from-urls":
        with open(args.urls_file) as f:
            for item_id in item_ids_from_urls(f):
                print(item_id)

    elif args.cmd == "ids-registered":
        for item_id in ids_registered(args.collection_id, args.api):
            print(item_id)

    elif args.cmd == "diff":
        published = [i for i, _ in collection_item_links(args.collection_file)]
        registered = ids_registered(args.collection_id, args.api)
        missing, orphaned = ids_diff(published, registered)
        print(f"published  {len(published)}", file=sys.stderr)
        print(f"registered {len(registered)}", file=sys.stderr)
        print(f"missing    {len(missing)}", file=sys.stderr)
        print(f"orphaned   {len(orphaned)}", file=sys.stderr)
        for item_id in orphaned[:10]:
            print(f"  orphaned: {item_id}", file=sys.stderr)
        if args.orphaned_out:
            Path(args.orphaned_out).write_text("".join(f"{i}\n" for i in orphaned))
        if args.missing_out:
            Path(args.missing_out).write_text("".join(f"{i}\n" for i in missing))
        else:
            for item_id in missing:
                print(item_id)

    elif args.cmd == "ndjson":
        n = ndjson_write(sys.stdin, args.out)
        print(n)

    elif args.cmd == "verify-serving":
        wanted = [l.rstrip("\n") for l in open(args.ids_file) if l.strip()]
        if not wanted:
            print("nothing to verify (0 ids)", file=sys.stderr)
            return 0
        got = ids_serving(wanted, args.api)
        missing = sorted(set(wanted) - got)
        print(f"requested {len(wanted)}, serving {len(got)}", file=sys.stderr)
        if missing:
            print(f"FAIL: {len(missing)} id(s) not served, e.g. {missing[:3]}",
                  file=sys.stderr)
            return 1
        print("OK: every requested id is served by the API", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
