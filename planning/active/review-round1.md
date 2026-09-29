# Code-check round 1 — #45 staged diff

**Verdict: Clean.** No bugs, security issues or data-loss paths found in the diff.

## What was checked

- Worked in a copy (`/tmp/cc45_r1`). `tests/test_catalogue_register.py` and
  `tests/test_register_manifest.py` both pass there: 124 tests.
- **Ways the check could fail toward "in sync" or "pass".** None found:
  - `diff` runs under `set -e`, outside the `set +e` window, so an API, paging or
    digest failure aborts. It never reaches IN SYNC.
  - All three lists must exist before they are counted.
  - `collection-state` is whitelisted to `same|changed|missing`. Any other output
    is an error, and a 404 reads as `missing`, never `same`.
  - `requests`' `JSONDecodeError` subclasses `RequestException`, so a malformed
    API body is retried and then raised.
  - Every missing, unparseable or wrong-id body raises in `published_digests`.
    So does an id repeated during paging, in `bodies_registered`.
  - `_strip_nulls` is applied to both sides. The only thing it can make equal is
    "null" against "absent", which pgstac cannot tell apart anyway. A change from
    null to a value still differs, and that is pinned by a test.
- **Guard ordering and `set -u`.** `N_TODO` is assigned on every path that reads
  it:
  - `--all`/`--ids-file` assign it at line 336.
  - `--drift`/`--verify` assign it at line 429.
  - `--verify` exits before line 491.
  - `COLL_STATE` is initialised at line 397.
  - The dryrun exits and the ssh probe sit where the accepted design puts them.
  - A changed collection with no item changes skips the audit and the item load,
    upserts the collection, and still runs the `COLL_AFTER` check.
- **bash 3.2.** No new bash-4 constructs. The only array expansion is the
  existing `${ARR[@]+...}` form.
- **Fetcher thread safety.**
  - There is one `requests.Session` per thread (`threading.local`).
  - `urls.txt` is `sort -u`, so no two workers share a `.part` or output path.
  - Files are written to `.part` and then `os.replace`d, and `*.json` excludes
    `.json.part`.
  - An unexpected exception kills the process. The on-disk count guard then fails
    the run with the traceback in `fetch_stderr.txt`.
- **One fact derived twice.** `fetch_key(href)` is computed from the same href
  string in all four places:
  - `fetch-bodies`, via `urls.txt` = `cut -f2 hrefs.tsv`
  - `published_digests`, from the collection links
  - `fetched-paths`
  - `verify-serving`
- **Callers of the removed or changed API.** `git grep` finds no remaining caller
  of `ids_serving`, of `diff` without `--fetch-dir`, or of the old
  `verify-serving` signature.
- **Caching.** Live `HEAD` of `/collections/stac-elevation-bc` returns
  `server: uvicorn` and `via: Caddy`, with no cache headers. So the
  post-register re-reads are not at risk of stale-cache false failures.
- **Test harness.**
  - The stub API applies `collections`, `ids`, `limit`, `fields` and the
    keyset token.
  - It adds its own `links`, so the link-stripping is exercised.
  - `test_drift_loads_only_the_todo_bodies` observes the item-load line count
    (2 of 4).
  - The proxy variables keep the Python fetcher off the network, and
    `test_the_harness_is_network_proof` pins this.

## Non-blocking note (not a defect)

- `scripts/register_manifest.py:365`: `s.get(url, timeout=60)` is a requests
  timeout. That means 60 s for the connect and 60 s *between bytes*; it is not a
  total deadline the way the replaced `curl --max-time 60` was.
  - A silent, hung connection still times out, so the failure described in
    code-check-shell's "curl in a parallel fan-out needs --max-time" does not
    apply.
  - Only a connection that trickles bytes forever could hold a worker slot, which
    is implausible against S3.
  - Recorded so the difference is known. No change needed.
