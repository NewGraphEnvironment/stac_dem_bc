#!/usr/bin/env python3
"""Compute the footprint of every DEM tile not yet in data/footprints.csv (#2).

Each tile is read once (scripts/footprint.py): small files are downloaded whole
into a scratch directory, hashed and deleted; large files with overviews are
read at their coarsest overview over HTTP. The cache is keyed by URL like
data/stac_geotiff_checks.csv, so after the first full pass a month reads only
its new tiles.

Disk: nothing is kept. Every download goes to one scratch directory, which is
cleared at start and removed at exit, and the run stops cleanly when free space
falls below --min-free-gb. Peak scratch use is logged. Measured design point:
16 workers x ~8 MB.

Resumable: rows are appended as tiles finish, so a killed or out-of-time run
keeps what it did, and the next run's todo is `urls_list - cache`. At the end
the cache is rewritten sorted by URL (atomically), so diffs stay readable.

Outcomes:
  download | overview     a footprint (cached)
  empty | crs_mismatch    deterministic refusals (cached, so they are not retried
                          monthly; reported, and the item keeps its geometry)
  transient failure       written to --errors, NOT cached, retried next run

--changed-out lists URLs that gained a row and are NOT new this month: items
already published whose footprint arrived after they were built (a run that ran
out of --max-minutes, a transient failure). The workflow rebuilds them, as it
does for a DSM pairing change. The list is CUMULATIVE: each URL is appended the
moment its row is written, and the workflow empties the file only after a
successful sync. The cache rows are committed even when the job fails, so a
list rebuilt from "this run's writes" would drop a URL whose row was committed
but whose item never reached S3, and nothing would list it again.

Usage:
    python scripts/footprint_extract.py --limit 50                 # rehearsal
    python scripts/footprint_extract.py --workers 16               # everything missing
    python scripts/footprint_extract.py --max-minutes 90 \\
        --changed-out data/urls_footprint_changed.txt               # monthly (CI)
"""

import argparse
import concurrent.futures
import csv
import logging
import os
import shutil
import sys
import tempfile
import time

import shapely

from footprint import (
    FootprintCrsMismatch,
    FootprintEmpty,
    FootprintNoGeoref,
    FootprintOutsideBC,
    METHOD_DOWNLOAD,
    METHOD_OVERVIEW,
    footprint_read,
    within_bc,
)
from item_rewrite import error_tolerable
from stac_utils import url_scheme_check, url_to_item_id

logger = logging.getLogger(__name__)

CACHE = "data/footprints.csv"
ERRORS = "logs/footprint_errors.txt"   # transient, retried next run: not a cache
URLS_LIST = "data/urls_list.txt"
FIELDS = ["url", "method", "footprint_wkt", "valid_percent", "checksum", "size"]
METHOD_EMPTY = "empty"
METHOD_CRS_MISMATCH = "crs_mismatch"
METHOD_NO_GEOREF = "no_georef"
METHOD_OUTSIDE_BC = "outside_bc"
METHODS = {METHOD_DOWNLOAD, METHOD_OVERVIEW, METHOD_EMPTY, METHOD_CRS_MISMATCH,
           METHOD_NO_GEOREF, METHOD_OUTSIDE_BC}
REFUSALS = {FootprintEmpty: METHOD_EMPTY, FootprintCrsMismatch: METHOD_CRS_MISMATCH,
            FootprintNoGeoref: METHOD_NO_GEOREF, FootprintOutsideBC: METHOD_OUTSIDE_BC}

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_DISK = 3


def urls_read(path: str) -> list[str]:
    with open(path) as fh:
        return [url_scheme_check(u) for u in (line.strip() for line in fh) if u]


def cache_load(path: str = CACHE) -> dict[str, dict]:
    """Rows by URL. A partial last line (a run killed mid-write) is dropped, not trusted."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as fh:
        text = fh.read()
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
        logger.warning("Dropped a partial last row from %s (interrupted write)", path)
    rows = {}
    for r in csv.DictReader(text.splitlines()):
        if r.get("method") not in METHODS or None in r.values():
            raise ValueError(f"{path}: malformed row for {r.get('url')!r}")
        url_scheme_check(r["url"])
        rows[r["url"]] = r
    return rows


def cache_finalize(path: str = CACHE) -> int:
    """Rewrite the cache sorted by URL, atomically. Returns the row count."""
    rows = cache_load(path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS, lineterminator="\n")
        w.writeheader()
        for url in sorted(rows):
            w.writerow(rows[url])
    os.replace(tmp, path)
    return len(rows)


def changed_finalize(path: str) -> int:
    """Dedupe and sort the cumulative rebuild list in place. Returns its length."""
    if not os.path.exists(path):
        open(path, "w").close()
        return 0
    urls = sorted(set(urls_read(path)))
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.writelines(f"{u}\n" for u in urls)
    os.replace(tmp, path)
    return len(urls)


def changed_prune(path: str, staged_dir: str) -> tuple[int, int]:
    """Drop from the rebuild list every URL whose item is staged in `staged_dir`. (pruned, kept)

    Called only after a successful sync, so a staged item is a published one.
    Not "the rebuild step exited 0": item_create drops a URL it fails on and
    still exits 0, and clearing on that proxy stranded the failures for good.
    A URL that keeps failing stays listed and is retried every run, visibly.
    """
    if not os.path.exists(path):
        return 0, 0
    urls = urls_read(path)
    kept = [u for u in urls
            if not os.path.exists(os.path.join(staged_dir, f"{url_to_item_id(u)}.json"))]
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.writelines(f"{u}\n" for u in sorted(set(kept)))
    os.replace(tmp, path)
    return len(urls) - len(kept), len(set(kept))


def changed_select(written_urls: list[str], new_urls: set[str]) -> list[str]:
    """URLs that gained a row this run and are not new this month: published items to rebuild."""
    return sorted(u for u in written_urls if u not in new_urls)


def todo_select(urls: list[str], cached: dict) -> list[str]:
    seen = set()
    out = []
    for u in urls:
        if u not in cached and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def row_for(url: str, read=footprint_read, workdir: str = ".") -> dict:
    """One tile's cache row. Deterministic refusals become rows; anything else raises."""
    try:
        return {"url": url, **read(url, workdir)}
    except tuple(REFUSALS) as e:
        return {"url": url, "method": REFUSALS[type(e)], "footprint_wkt": "",
                "valid_percent": 0.0 if isinstance(e, FootprintEmpty) else "",
                "checksum": "", "size": ""}


def cache_audit(rows: dict) -> dict:
    """Faults in a cache, by kind. A footprint row's WKT must parse, be valid, and lie in BC.

    The guards in footprint.py stop a bad footprint being written; this checks
    one that was written before a guard existed, or by an older copy of the code.
    """
    faults = {}
    for url, r in rows.items():
        if r["method"] not in (METHOD_DOWNLOAD, METHOD_OVERVIEW) or not r["footprint_wkt"]:
            continue
        try:
            g = shapely.from_wkt(r["footprint_wkt"])
            kind = None if not g.is_valid else (None if within_bc(g) else "outside BC")
            kind = kind or (None if g.is_valid else "invalid geometry")
        except Exception:       # noqa: BLE001 - any parse failure is the fault
            kind = "unparseable WKT"
        if kind:
            faults.setdefault(kind, []).append(url)
    return faults


def _dir_bytes(d: str) -> int:
    total = 0
    for e in os.scandir(d):
        try:
            total += e.stat().st_size
        except FileNotFoundError:
            pass                                    # deleted between scandir and stat
    return total


def run(todo: list[str], cache_path: str, errors_path: str, workers: int,
        max_minutes: float | None, min_free_gb: float, population: int,
        read=footprint_read, tmp_root: str | None = None,
        changed_path: str | None = None, new_urls: set | None = None) -> tuple[int, dict]:
    """Fill the cache for `todo`. Returns (exit code, stats)."""
    stats = {"written": 0, "error": 0, "by_method": {}, "peak_scratch_bytes": 0,
             "stopped": "", "written_urls": []}
    deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
    workdir = tempfile.mkdtemp(prefix="footprint_", dir=tmp_root)
    new_file = not os.path.exists(cache_path) or os.path.getsize(cache_path) == 0
    try:
        changed_fh = open(changed_path, "a") if changed_path else None
        with open(cache_path, "a", newline="") as cache_fh, open(errors_path, "a") as err_fh:
            w = csv.DictWriter(cache_fh, FIELDS, lineterminator="\n")
            if new_file:
                w.writeheader()
            pending = iter(todo)
            in_flight = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                def refill():
                    while len(in_flight) < workers * 2:
                        if deadline and time.monotonic() > deadline:
                            stats["stopped"] = stats["stopped"] or "max-minutes"
                            return
                        free_gb = shutil.disk_usage(workdir).free / 1e9
                        if free_gb < min_free_gb:
                            stats["stopped"] = f"free disk {free_gb:.1f} GB < {min_free_gb} GB"
                            return
                        url = next(pending, None)
                        if url is None:
                            return
                        in_flight[ex.submit(row_for, url, read, workdir)] = url

                refill()
                t0 = time.monotonic()
                done = 0
                while in_flight:
                    finished, _ = concurrent.futures.wait(
                        in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
                    for fut in finished:
                        url = in_flight.pop(fut)
                        done += 1
                        try:
                            row = fut.result()
                        except Exception as e:     # noqa: BLE001 - transient, retried next run
                            stats["error"] += 1
                            err_fh.write(f"{url}\t{e}\n")
                            err_fh.flush()
                            continue
                        w.writerow(row)
                        cache_fh.flush()
                        if changed_fh and url not in (new_urls or set()):
                            # After the row, so a URL is never listed without one.
                            changed_fh.write(f"{url}\n")
                            changed_fh.flush()
                        stats["written"] += 1
                        stats["written_urls"].append(url)
                        m = row["method"]
                        stats["by_method"][m] = stats["by_method"].get(m, 0) + 1
                    stats["peak_scratch_bytes"] = max(stats["peak_scratch_bytes"], _dir_bytes(workdir))
                    if done % 500 == 0 or not in_flight:
                        el = time.monotonic() - t0
                        logger.info("%d/%d done (%d errors) in %.0f s, %.2f tiles/s, peak scratch %.0f MB",
                                    done, len(todo), stats["error"], el, done / el if el else 0,
                                    stats["peak_scratch_bytes"] / 1e6)
                    refill()
    finally:
        if changed_path and changed_fh:
            changed_fh.close()
        shutil.rmtree(workdir, ignore_errors=True)

    if stats["stopped"].startswith("free disk"):
        logger.error("Stopped: %s. Resumable - free space and re-run.", stats["stopped"])
        return EXIT_DISK, stats
    if stats["stopped"]:
        logger.warning("Stopped at --max-minutes with %d of %d not attempted; the next run continues.",
                       len(todo) - stats["written"] - stats["error"], len(todo))
    if not error_tolerable(stats["error"], stats["written"] + stats["error"], population):
        logger.error("%d transient failures exceeds tolerance; see %s", stats["error"], errors_path)
        return EXIT_ERRORS, stats
    return EXIT_OK, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--urls-file", default=URLS_LIST,
                    help="URLs to consider (default: every DEM URL); those already cached are skipped")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--errors", default=ERRORS)
    ap.add_argument("--limit", type=int, default=0, help="Attempt at most N tiles (rehearsal)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--tmp-dir", default=None, help="Where the scratch directory goes (default: system temp)")
    ap.add_argument("--new-urls", default=None,
                    help="URLs new this run (data/urls_new.txt when detect found some). Not "
                         "defaulted: on a run with no changes that file is last month's, and "
                         "treating its URLs as new would keep their published items off the list")
    ap.add_argument("--prune-changed", metavar="STAGED_DIR", default=None,
                    help="After a successful sync: drop from --changed-out every URL whose item is "
                         "staged in STAGED_DIR, and exit; reads nothing")
    ap.add_argument("--audit", action="store_true",
                    help="Check every footprint row in the cache (valid, inside BC) and exit; reads nothing")
    ap.add_argument("--changed-out", default=None,
                    help="Write URLs that gained a row this run and are not in data/urls_new.txt")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    if args.prune_changed:
        if not args.changed_out:
            ap.error("--prune-changed needs --changed-out")
        pruned, kept = changed_prune(args.changed_out, args.prune_changed)
        logger.info("Rebuild list: %d published and pruned, %d kept for the next run", pruned, kept)
        return EXIT_OK

    if args.audit:
        rows = cache_load(args.cache)
        faults = cache_audit(rows)
        counts = {}
        for r in rows.values():
            counts[r["method"]] = counts.get(r["method"], 0) + 1
        logger.info("%d rows %s; %d with WKT", len(rows), dict(sorted(counts.items())),
                    sum(1 for r in rows.values() if r["footprint_wkt"]))
        for kind, urls in faults.items():
            logger.error("FAULT %s: %d, e.g. %s", kind, len(urls), urls[:3])
        return EXIT_ERRORS if faults else EXIT_OK

    urls = urls_read(args.urls_file)
    os.makedirs(os.path.dirname(args.errors) or ".", exist_ok=True)
    if os.path.exists(args.cache):
        # Before appending: a run killed mid-write leaves a partial last line,
        # and the next row would be glued onto it.
        cache_finalize(args.cache)
    cached = cache_load(args.cache)
    todo = todo_select(urls, cached)
    population = len(todo)
    if args.limit:
        todo = todo[: args.limit]
        population = 0                     # judged on what it attempted
    logger.info("%d URLs, %d cached, %d to read%s", len(urls), len(cached), len(todo),
                f" (limit {args.limit})" if args.limit else "")

    new = set(urls_read(args.new_urls)) if args.new_urls else set()
    code, stats = (EXIT_OK, {"written": 0, "error": 0, "by_method": {}, "peak_scratch_bytes": 0,
                             "stopped": "", "written_urls": []})
    if todo:
        code, stats = run(todo, args.cache, args.errors, args.workers, args.max_minutes,
                          args.min_free_gb, population, tmp_root=args.tmp_dir,
                          changed_path=args.changed_out, new_urls=new)
    n = cache_finalize(args.cache) if os.path.exists(args.cache) else 0

    if args.changed_out:
        listed = changed_finalize(args.changed_out)
        logger.info("%d already-published URLs gained a footprint this run; %d listed for "
                    "rebuild in %s (cumulative until a sync succeeds)",
                    len(changed_select(stats["written_urls"], new)), listed, args.changed_out)

    logger.info("Wrote %d rows (%s), %d transient errors; cache holds %d; peak scratch %.0f MB",
                stats["written"], stats["by_method"], stats["error"], n,
                stats["peak_scratch_bytes"] / 1e6)
    return code


if __name__ == "__main__":
    sys.exit(main())
