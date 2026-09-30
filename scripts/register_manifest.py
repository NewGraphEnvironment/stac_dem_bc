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
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
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


def _search_pages(session, api: str, body: dict):
    """Yield each page's features from POST /search, following keyset paging.

    stac-fastapi returns the continuation in the next link's BODY, not its
    href. Every way out other than "no next link" raises: a short enumeration
    makes every unenumerated item report as missing (and, since #45, makes a
    stale body invisible), which would send --drift to re-register a
    catalogue that was fine.
    """
    url = f"{api.rstrip('/')}/search"
    seen_tokens: set[str] = set()
    n = 0
    while True:
        page = _post(session, url, body)
        feats = page.get("features", [])
        n += len(feats)
        yield feats
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            # The only NORMAL way out: the server says there is no more.
            return
        token = (nxt.get("body") or {}).get("token")
        if not token:
            raise RuntimeError(
                f"paging stopped early: 'next' link with no token after {n} items"
            )
        if token in seen_tokens:
            raise RuntimeError(f"paging token repeated after {n} items — not advancing")
        seen_tokens.add(token)
        body = {**body, **nxt["body"]}


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
    return [f["id"] for feats in _search_pages(session, api, body) for f in feats]


def bodies_registered(collection_id: str, api: str = API_DEFAULT,
                      page_size: int = PAGE_SIZE, session=None) -> dict[str, str]:
    """{id: body_digest} for every item the API serves in a collection (#45).

    The same enumeration as `ids_registered`, but of FULL bodies -- no
    `fields` include, since a digest of an id-only stub would compare equal to
    nothing and differ from everything. Measured 2026-09-28: a 10,000-item page
    is 18 MB and 29 s, so the whole catalogue is ~5.5 min. Only the digest is
    kept, so memory is ~100 bytes an item rather than the body.

    A repeated id raises. The API's primary key makes one impossible, so seeing
    one means the paging overlapped -- and a dict keyed by id would have
    collapsed it silently into a single entry.
    """
    session = session or requests.Session()
    body = {"collections": [collection_id], "limit": page_size}
    out: dict[str, str] = {}
    for feats in _search_pages(session, api, body):
        for f in feats:
            if f["id"] in out:
                raise RuntimeError(f"id {f['id']!r} returned twice while paging "
                                   f"{collection_id}: the enumeration overlapped")
            out[f["id"]] = body_digest(f)
    return out


def search_body(ids, collection_id: str, ids_only: bool = True) -> dict:
    """The POST /search body for an id lookup, scoped to one collection.

    Pure, and separated out purely so it can be asserted on offline. Two
    parameters, each of which exists because omitting it fails silently.

    `limit`: the API's DEFAULT LIMIT IS 10, so a body without one silently
    returns the first 10 of however many ids were asked for. That reads as
    "590 of my 600 items are missing" and fails a verification whose subject
    was in fact fine. Measured against the live API: 600 registered ids, no
    limit -> 10 features; limit=600 -> 600.

    `collections`: without it a /search asks "is this id served ANYWHERE",
    which is a different question from the one every caller means. It was
    harmless for as long as one collection existed. #34 puts two collections
    on the endpoint by design, sharing all 102,460 ids -- so an unscoped
    verification of the NEW collection passes green while the OLD one is
    answering, even if zero items registered. The collection id is required
    rather than defaulted, because a default is exactly the thing that would
    have gone unnoticed here.

    `ids_only=False` drops the `fields` include, for a caller comparing
    content (#45): the digest of an id-only stub matches no real body.
    """
    ids = list(ids)
    if not collection_id:
        raise ValueError("collection_id is required: an unscoped /search "
                         "answers about every collection on the endpoint")
    body = {
        "collections": [collection_id],
        "ids": ids,
        "limit": max(len(ids), 1),
    }
    if ids_only:
        body["fields"] = {"include": ["id"]}
    return body


def bodies_serving(ids, collection_id: str, api: str = API_DEFAULT,
                   chunk: int = 500, session=None) -> dict[str, str]:
    """{id: body_digest} for those of these ids the API serves IN THIS COLLECTION.

    Batched because a very long id list is a real request-size ceiling. An id
    the API does not serve is simply absent from the result -- a /search omits
    ids that do not exist without erroring -- so the caller compares key SETS,
    never a count, and then the digests.
    """
    session = session or requests.Session()
    url = f"{api.rstrip('/')}/search"
    ids = list(ids)
    got: dict[str, str] = {}
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        page = _post(session, url, search_body(batch, collection_id, ids_only=False))
        for f in page.get("features", []):
            got[f["id"]] = body_digest(f)
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
# Content (#45) -- the ids match and the bodies do not
# =============================================================================
#
# Id-set equality is blind to a rebuild that rewrites existing items, because a
# rebuild keeps the ids: --verify said IN SYNC and --drift had nothing to do
# while the API served the old bodies. So the comparison is of bodies, by
# digest -- and every way a digest can be absent raises rather than comparing.

def _canonical(x):
    """The form both sides of the comparison are reduced to before hashing.

    Two things pgstac's round trip does not preserve, each measured on the live
    catalogue, and each of which would otherwise report items "changed" forever
    and make --drift re-register them every month without converging:

    - null object members are dropped. pgstac stores jsonb with nulls stripped,
      so `"proj:epsg": null` is served with the key absent (160 items, the first
      full --verify, 2026-09-29). Like Postgres' `jsonb_strip_nulls`, this
      removes object FIELDS and never array elements -- a null in an array is
      positional
    - an integral float comes back as an integer. JSON has one number type, and
      PostGIS rebuilds geometry: `-126.0` is served as `-126` (29 items,
      2026-09-29). The same rule covers `-0.0` (numeric has no negative zero)
      and floats of 1e16 and up (numeric emits them as integers). `bool` is
      left alone -- it is an int in Python, not a float, and `true` is not `1`.
    """
    if isinstance(x, dict):
        return {k: _canonical(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_canonical(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def body_digest(doc) -> str:
    """sha256 of a STAC object's canonical JSON: `links` removed, null members
    dropped and integral floats written as integers (see `_canonical`), keys
    sorted.

    `links` is the one member the API rewrites: it replaces the published
    `collection` link with its own self/root/parent/collection set on its own
    host. Everything else round-trips through pgstac -- measured on all 102,460
    live items (2026-09-29, geometry at full float precision), all 10,100
    stac-airphoto-bc items, and both collections -- apart from the two
    differences `_canonical` absorbs.

    Refuses anything but an object. A None that hashed would equal every other
    None, which is how the spot-check in #45's history passed with neither
    body fetched.
    """
    if not isinstance(doc, dict):
        raise TypeError(f"expected a JSON object, got {type(doc).__name__}")
    canon = _canonical({k: v for k, v in doc.items() if k != "links"})
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def content_diff(published: dict, registered: dict) -> tuple[list, list, list]:
    """(missing, orphaned, changed) from two {id: digest} maps.

    `changed` = in both, digests differ. An empty or None digest on EITHER side
    raises: it means a body was never read, and two unread bodies must not
    compare equal.
    """
    for side, d in (("published", published), ("registered", registered)):
        bad = sorted(k for k, v in d.items() if not isinstance(v, str) or not v)
        if bad:
            raise ValueError(f"{len(bad)} {side} id(s) have no body digest, "
                             f"e.g. {bad[:3]}: a body was not read")
    missing, orphaned = ids_diff(published, registered)
    changed = sorted(k for k in published.keys() & registered.keys()
                     if published[k] != registered[k])
    return missing, orphaned, changed


def fetch_key(url: str) -> str:
    """The fetch file's basename for a URL -- md5 of the URL, as the shell
    fetcher named them, so ids with spaces and parentheses need no quoting."""
    return hashlib.md5(url.encode()).hexdigest()


_local = threading.local()


def _read_url(url: str, timeout: int) -> bytes:
    if url.startswith("file://"):
        # The offline tests publish a file:// bucket. urllib reads it without
        # any network, so there is one code path rather than a test branch.
        with urllib.request.urlopen(url, timeout=timeout) as fh:
            return fh.read()
    # One Session per worker thread: a shared one caps at 10 pooled
    # connections per host and serialises the rest.
    s = getattr(_local, "session", None)
    if s is None:
        s = _local.session = requests.Session()
    resp = s.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def fetch_bodies(urls, out_dir, workers: int = 32, retries: int = 3,
                 timeout: int = 60, backoff: float = 2.0) -> list[str]:
    """Fetch each URL to `out_dir/<fetch_key(url)>.json`. Returns the URLs that
    failed after `retries` attempts, in input order.

    In-process with a thread pool rather than one `curl` per item: the shell
    loop this replaces took ~45 min for the whole catalogue; 32 threads in one
    process fetched 2,000 in 4.9 s (measured 2026-09-28), and #45 needs every
    published body on every --verify.

    A body counts only if it parses as a JSON object -- a truncated transfer is
    a failed attempt, retried, not a file that counts as present. Written to
    `.part` and renamed, so a failure never leaves a partial file behind.
    """
    out_dir = Path(out_dir)
    urls = list(urls)

    def one(url):
        out = out_dir / f"{fetch_key(url)}.json"
        part = out.with_suffix(".json.part")
        for attempt in range(1, retries + 1):
            try:
                data = _read_url(url, timeout)
                if not isinstance(json.loads(data), dict):
                    raise ValueError("body is not a JSON object")
                part.write_bytes(data)
                os.replace(part, out)
                return None
            except (OSError, ValueError, requests.RequestException) as e:
                part.unlink(missing_ok=True)
                last = e
                if attempt < retries:
                    time.sleep(attempt * backoff)
        print(f"fetch failed after {retries} attempts: {url}: {last}",
              file=sys.stderr)
        return url

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        results = list(ex.map(one, urls))
    except BaseException:
        # Ctrl-C would otherwise wait for every queued fetch -- ~100k of them.
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown()
    return [u for u in results if u is not None]


def published_digests(links, fetch_dir) -> dict[str, str]:
    """{id: body_digest} from the fetched bodies of `links` [(id, href)].

    Every way a published digest could be absent raises -- a body not fetched,
    not parseable, or naming a different id than its link (which would compare
    the wrong pair) -- and so does an id with two links, which a dict would
    otherwise collapse to whichever came last.
    """
    fetch_dir = Path(fetch_dir)
    out: dict[str, str] = {}
    for item_id, href in links:
        if item_id in out:
            raise ValueError(f"id {item_id!r} has more than one item link")
        path = fetch_dir / f"{fetch_key(href)}.json"
        if not path.exists():
            raise FileNotFoundError(f"body of {item_id!r} was not fetched ({href})")
        try:
            doc = json.loads(path.read_text())
        except ValueError as e:
            raise ValueError(f"body of {item_id!r} is not JSON ({href}): {e}") from e
        if not isinstance(doc, dict) or doc.get("id") != item_id:
            got = doc.get("id") if isinstance(doc, dict) else doc
            raise ValueError(f"link for {item_id!r} fetched a body that names "
                             f"id {got!r} ({href})")
        out[item_id] = body_digest(doc)
    return out


def collection_state(collection_file, collection_id: str, api: str = API_DEFAULT,
                     session=None) -> str:
    """'same', 'changed' or 'missing': the registered collection vs the file.

    404 is 'missing' (never registered). Any other failure raises after
    retries, so an unreachable API cannot read as 'same'.
    """
    with open(collection_file) as fh:
        published = json.load(fh)
    session = session or requests.Session()
    url = f"{api.rstrip('/')}/collections/{urllib.parse.quote(collection_id)}"
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.get(url, timeout=180)
            if resp.status_code == 404:
                return "missing"
            resp.raise_for_status()
            registered = resp.json()
            break
        except requests.RequestException as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(attempt * 2)
    else:
        raise RuntimeError(f"API request failed after {RETRIES} attempts: {last}") from last
    return "same" if body_digest(published) == body_digest(registered) else "changed"


def read_hrefs(path) -> list[tuple[str, str]]:
    """[(id, href)] from a `hrefs-published` TSV."""
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            item_id, href = line.split("\t")
            out.append((item_id, href))
    return out


# =============================================================================
# NDJSON
# =============================================================================

def ndjson_write(paths, out, expect_collection: str | None = None) -> int:
    """Compact one item JSON per line. Returns the number of lines written.

    json.dumps never emits a raw newline, so a record cannot straddle lines
    however odd the source formatting is.

    `expect_collection` is the last checkpoint before pgstac. Each item is
    routed by its OWN `collection` field -- item_register.sh passes no
    collection id at all -- so an item whose body still names the previous
    collection upserts into the previous collection SUCCESSFULLY, with no error
    anywhere. During #34 that is the difference between a renamed catalogue and
    a silently split one.
    """
    n = 0
    with open(out, "w") as fh:
        for path in paths:
            path = path.rstrip("\n")
            if not path:
                continue
            with open(path) as src:
                doc = json.load(src)
            if expect_collection is not None:
                got = doc.get("collection")
                if got != expect_collection:
                    raise RuntimeError(
                        f"{path} names collection {got!r}, expected "
                        f"{expect_collection!r}. Loading it would register the "
                        f"item into {got!r} without erroring."
                    )
            fh.write(json.dumps(doc, separators=(",", ":")))
            fh.write("\n")
            n += 1
    return n


# =============================================================================
# Which bucket a URL names -- the catalogue's identity when the id is not enough
# =============================================================================

# Virtual-hosted (`<bucket>.s3[.<region>|-<region>|.dualstack...].amazonaws.com`)
# and path-style (`s3[...].amazonaws.com/<bucket>`). Bucket names may contain
# dots, so the virtual-hosted bucket is everything before the LAST `.s3` label.
_S3_VIRTUAL = re.compile(r"^(?P<bucket>[a-z0-9][a-z0-9.-]*)\.s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$")
_S3_PATH = re.compile(r"^s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$")


def s3_bucket_name(url: str) -> str | None:
    """The S3 bucket a URL addresses, or None when it is not an S3 URL.

    catalogue_register.sh decides whose catalogue it is registering partly by
    bucket (#42), and one bucket has many spellings: regional or global
    endpoint, http or https, virtual-hosted or path-style, any case in the
    host. All of them answer 200 for the same collection.json, so comparing
    URL strings would read an alias of this repo's bucket as someone else's --
    and a foreign catalogue gets no asset audit.
    """
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme == "s3":
        return host or None
    if scheme not in ("http", "https"):
        return None
    m = _S3_VIRTUAL.match(host)
    if m and not _S3_PATH.match(host):
        return m.group("bucket")
    if _S3_PATH.match(host):
        seg = parts.path.lstrip("/").split("/", 1)[0]
        return seg.lower() or None
    return None


# =============================================================================
# Population homogeneity — the property a half-done migration breaks
# =============================================================================

def audit_items(paths, collection_id: str, require_asset: str | None = None,
                forbid_assets=None) -> dict:
    """Which items disagree with the collection they claim to belong to.

    The property is HOMOGENEITY, not size, and nothing else in this repo checks
    it. Item ids do not change during a rename, so set equality reports IN SYNC
    over a fully mixed catalogue; item_register.sh routes by each body's own
    collection field, so a stale item registers successfully; item_validate.py
    sees legal STAC either way, because both asset keys are legal; and a count
    of assets cannot tell {image, dsm} from {dem, dsm}.

    Returns {"checked": n, "wrong_collection": [...], "missing_asset": [...],
    "forbidden_asset": [...], "unreadable": [...]} with paths, not counts --
    a count of offenders is no more use here than a count of items.
    """
    out = {"checked": 0, "wrong_collection": [], "missing_asset": [],
           "forbidden_asset": [], "unreadable": []}
    for path in paths:
        path = path.rstrip("\n")
        if not path:
            continue
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            out["unreadable"].append(f"{path}: {e}")
            continue
        out["checked"] += 1
        if doc.get("collection") != collection_id:
            out["wrong_collection"].append(path)
        assets = doc.get("assets") or {}
        if require_asset is not None and require_asset not in assets:
            out["missing_asset"].append(path)
        # A LIST, not a string. A rename map can have more than one old key, and
        # the caller builds this from ASSET_RENAMES -- so a single-string
        # parameter meant the workflow's comma-joined value ("image,other")
        # matched no real asset key and the check silently stopped checking
        # anything. A guard that fails toward "nothing to report" is worse than
        # no guard, because it still reads as a pass.
        for key in (forbid_assets or ()):
            if key in assets:
                out["forbidden_asset"].append(path)
                break
    return out


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

    p = sub.add_parser("diff",
                       help="published vs registered: missing, orphaned, and "
                            "changed bodies")
    p.add_argument("--collection-file", required=True)
    p.add_argument("--collection-id", required=True)
    p.add_argument("--api", default=API_DEFAULT)
    # Required, not optional: an id-only diff is the #45 bug, and a flag that
    # restores it would be a guard that fails toward "in sync".
    p.add_argument("--fetch-dir", required=True,
                   help="every published body, as written by fetch-bodies")
    p.add_argument("--missing-out", help="write missing ids here")
    p.add_argument("--orphaned-out", help="write orphaned ids here")
    p.add_argument("--changed-out", help="write changed ids here")

    p = sub.add_parser("fetch-bodies",
                       help="fetch URLs (one per line) to DIR/<md5(url)>.json")
    p.add_argument("--urls-file", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--failed-out", required=True,
                   help="URLs that failed after retries, one per line")
    p.add_argument("--workers", type=int, default=32)

    p = sub.add_parser("fetched-paths",
                       help="the fetched body path of each id, one per line")
    p.add_argument("--hrefs-file", required=True, help="hrefs-published TSV")
    p.add_argument("--ids-file", required=True)
    p.add_argument("--fetch-dir", required=True)

    p = sub.add_parser("collection-state",
                       help="print same / changed / missing: the registered "
                            "collection body against the published file")
    p.add_argument("--collection-file", required=True)
    p.add_argument("--collection-id", required=True)
    p.add_argument("--api", default=API_DEFAULT)

    p = sub.add_parser("ndjson", help="build NDJSON from item paths on stdin")
    p.add_argument("--out", required=True)
    p.add_argument("--expect-collection",
                   help="refuse any item whose body names a different collection")

    p = sub.add_parser("audit-items",
                       help="assert every item agrees with its collection")
    p.add_argument("--dir", help="directory of item JSONs (default: paths on stdin)")
    p.add_argument("--collection-id", required=True)
    p.add_argument("--require-asset", help="every item must carry this asset key")
    p.add_argument("--forbid-asset",
                   help="no item may carry any of these asset keys "
                        "(comma-separated; a rename map may retire more than one)")
    p.add_argument("--expect", type=int,
                   help="the number of items there should be. Derive it from the "
                        "artifact the consumer reads, not from a separate count.")

    p = sub.add_parser("same-bucket",
                       help="print 'same' if both URLs address one S3 bucket, "
                            "'different' if not (or either is not an S3 URL)")
    p.add_argument("url_a")
    p.add_argument("url_b")

    p = sub.add_parser("hrefs-in-bucket",
                       help="count item links whose href is in the given S3 bucket")
    p.add_argument("--collection-file", required=True)
    p.add_argument("--bucket-url", required=True)

    p = sub.add_parser("verify-serving",
                       help="assert every id in a file is served by the API, "
                            "with the body that was fetched")
    p.add_argument("--ids-file", required=True)
    p.add_argument("--collection-id", required=True)
    p.add_argument("--api", default=API_DEFAULT)
    p.add_argument("--hrefs-file", required=True, help="hrefs-published TSV")
    p.add_argument("--fetch-dir", required=True)

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
        published = published_digests(
            collection_item_links(args.collection_file), args.fetch_dir)
        registered = bodies_registered(args.collection_id, args.api)
        missing, orphaned, changed = content_diff(published, registered)
        print(f"published  {len(published)}", file=sys.stderr)
        print(f"registered {len(registered)}", file=sys.stderr)
        print(f"missing    {len(missing)}", file=sys.stderr)
        print(f"orphaned   {len(orphaned)}", file=sys.stderr)
        print(f"changed    {len(changed)}", file=sys.stderr)
        for item_id in orphaned[:10]:
            print(f"  orphaned: {item_id}", file=sys.stderr)
        for path, ids in ((args.orphaned_out, orphaned),
                          (args.changed_out, changed)):
            if path:
                Path(path).write_text("".join(f"{i}\n" for i in ids))
        if args.missing_out:
            Path(args.missing_out).write_text("".join(f"{i}\n" for i in missing))
        else:
            for item_id in missing:
                print(item_id)

    elif args.cmd == "fetch-bodies":
        urls = [l.rstrip("\n") for l in open(args.urls_file) if l.strip()]
        failed = fetch_bodies(urls, args.out_dir, workers=args.workers)
        # Exit 0 with failures listed: the caller's gate is a count of the
        # files actually on disk, which a wrapper's exit status cannot fake.
        Path(args.failed_out).write_text("".join(f"{u}\n" for u in failed))

    elif args.cmd == "fetched-paths":
        href = dict(read_hrefs(args.hrefs_file))
        fetch_dir = Path(args.fetch_dir)
        for item_id in (l.rstrip("\n") for l in open(args.ids_file)):
            if not item_id:
                continue
            if item_id not in href:
                raise SystemExit(f"{item_id!r} has no href in {args.hrefs_file}")
            path = fetch_dir / f"{fetch_key(href[item_id])}.json"
            if not path.exists():
                raise SystemExit(f"body of {item_id!r} was not fetched: {path}")
            # item_register.sh upserts by the body's OWN id, so a link whose
            # body names another item would overwrite THAT item (code-check
            # round 3). This is the last step before the loader in every mode,
            # so the check lives here rather than only in published_digests,
            # which --all and --ids-file first reach after the write.
            try:
                got = json.loads(path.read_text()).get("id")
            except (ValueError, AttributeError) as e:
                raise SystemExit(f"body of {item_id!r} is not a JSON object: {e}")
            if got != item_id:
                raise SystemExit(f"link for {item_id!r} fetched a body that "
                                 f"names id {got!r} ({href[item_id]})")
            print(path)

    elif args.cmd == "collection-state":
        print(collection_state(args.collection_file, args.collection_id, args.api))

    elif args.cmd == "ndjson":
        n = ndjson_write(sys.stdin, args.out, args.expect_collection)
        print(n)

    elif args.cmd == "audit-items":
        if args.dir:
            paths = sorted(
                os.path.join(args.dir, f) for f in os.listdir(args.dir)
                if f.endswith(".json") and f != "collection.json"
            )
        else:
            paths = [l.rstrip("\n") for l in sys.stdin if l.strip()]

        # Zero items is never a pass. A loop over an empty set prints nothing
        # and exits 0, which is indistinguishable from "everything checked out"
        # -- and here it would bless an unpublished catalogue.
        if not paths:
            print("FAIL: audit-items found no item JSONs to check", file=sys.stderr)
            return 1

        forbid = [k for k in (args.forbid_asset or "").split(",") if k.strip()]
        forbid = [k.strip() for k in forbid]
        r = audit_items(paths, args.collection_id, args.require_asset, forbid)
        # The asset rules as APPLIED, not as passed: "--forbid-asset ," is a
        # non-empty argument that parses to no keys, and a caller printing its
        # own flags would report a check that never ran (#42).
        if args.require_asset or forbid:
            rules = f"require={args.require_asset or '-'} forbid={','.join(forbid) or '-'}"
        else:
            rules = "no asset checks"
        print(f"checked {r['checked']} item(s) against {args.collection_id} "
              f"({rules})", file=sys.stderr)

        bad = False
        for kind, label in (("wrong_collection", "name another collection"),
                            ("missing_asset", f"lack asset {args.require_asset!r}"),
                            ("forbidden_asset", f"still carry a retired asset key {forbid!r}"),
                            ("unreadable", "could not be read")):
            hits = r[kind]
            if hits:
                bad = True
                print(f"FAIL: {len(hits)} item(s) {label}, e.g. {hits[:3]}",
                      file=sys.stderr)

        # The count belongs to the same statement, not a separate one: `--expect`
        # exists so a run that silently processed a SUBSET -- a reused manifest
        # is the way that happens -- fails here rather than publishing.
        if args.expect is not None and r["checked"] != args.expect:
            bad = True
            print(f"FAIL: expected {args.expect} item(s), audited {r['checked']}",
                  file=sys.stderr)

        if bad:
            return 1
        print(f"OK: every item agrees with its collection ({rules})",
              file=sys.stderr)

    elif args.cmd == "same-bucket":
        # An answer on stdout, not an exit status: an uncaught exception exits
        # 1 too, and a caller reading 1 as "different" would fail toward the
        # foreign policy -- which is the one with no asset audit.
        a, b = s3_bucket_name(args.url_a), s3_bucket_name(args.url_b)
        print("same" if (a is not None and a == b) else "different")

    elif args.cmd == "hrefs-in-bucket":
        want = s3_bucket_name(args.bucket_url)
        if want is None:
            print(f"not an S3 bucket URL: {args.bucket_url}", file=sys.stderr)
            return 1
        print(sum(1 for _, href in collection_item_links(args.collection_file)
                  if s3_bucket_name(href) == want))

    elif args.cmd == "verify-serving":
        wanted = [l.rstrip("\n") for l in open(args.ids_file) if l.strip()]
        if not wanted:
            print("nothing to verify (0 ids)", file=sys.stderr)
            return 0
        # The digests of what was just sent, from the files that were sent.
        # If pgstac ever normalised a field, --drift would re-register the same
        # items every month and never converge; checking content here makes
        # that fail on the first run instead.
        wanted_set = set(wanted)
        links = [(i, h) for i, h in read_hrefs(args.hrefs_file) if i in wanted_set]
        sent = published_digests(links, args.fetch_dir)
        if set(sent) != wanted_set:
            print(f"FAIL: {len(wanted_set - set(sent))} id(s) have no href in "
                  f"{args.hrefs_file}", file=sys.stderr)
            return 1
        got = bodies_serving(wanted, args.collection_id, args.api)
        missing = sorted(set(wanted) - set(got))
        _, _, differ = content_diff(sent, {k: got[k] for k in sent if k in got})
        print(f"requested {len(wanted)}, serving {len(got)} "
              f"in {args.collection_id}", file=sys.stderr)
        bad = False
        if missing:
            print(f"FAIL: {len(missing)} id(s) not served by "
                  f"{args.collection_id}, e.g. {missing[:3]}", file=sys.stderr)
            bad = True
        if differ:
            print(f"FAIL: {len(differ)} id(s) are served with a body that "
                  f"differs from the one registered, e.g. {differ[:3]}",
                  file=sys.stderr)
            bad = True
        if bad:
            return 1
        print(f"OK: every requested id is served by {args.collection_id}, "
              f"with the body that was registered", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
