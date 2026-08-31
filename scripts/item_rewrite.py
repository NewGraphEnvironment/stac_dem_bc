#!/usr/bin/env python3
"""Harness for rewriting already-published item JSONs in place.

Extracted from scripts/item_backfill.py, which was the first thing to need it
(#31) and is no longer the only one (#34). It is a library: no CLI, no main().
A caller supplies an `edit` function and this module supplies everything around
it -- fetch, retry, resumable manifest, error accounting, and verification.

The extraction is not tidiness. Every behaviour below has a named incident
behind it, and a second copy of this block would fork the policy along with the
code:

  retry            34 transient failures in one local run over ~98k requests,
                   every one of which re-fetched fine on the next attempt
  error_tolerable  a run completed all 98,040 items, then exited non-zero on 2
                   failures (0.002%) -- skipping the publish and discarding
                   16m37s of finished work
  errors to a FILE tqdm writes its bar to stderr with carriage returns, which
                   overwrote the interleaved log lines; that run's 2 failing ids
                   were unrecoverable from the log and had to be reconstructed
                   by differencing a manifest against the input set
  manifest header  data/backfill_done.txt holds 98,040 ids against 102,460
                   published. Handed to a different migration it would look like
                   "98,040 already done", rewrite 4,420 items and exit 0

Rewriting rather than rebuilding is also deliberate, and belongs here rather
than in any one caller: 60,324 of 100,345 metadata-cache rows predate spatial
metadata caching, so a rebuild would silently swap ~60k items from one code path
to another -- a change nobody reviewed, and invisible in a single-item check.
"""

import concurrent.futures
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Callable

from tqdm import tqdm

from stac_utils import PATH_S3_STAC, encode_url_for_gdal

logger = logging.getLogger(__name__)

COLLECTION_URL = f"{PATH_S3_STAC}/collection.json"

# A strict zero-error gate on ~100k network requests is a guard that fails
# toward abort: transient failures are certain at that volume, not exceptional.
# Gate on the RATE against a stated tolerance, and let the retry absorb the
# ordinary case.
ERROR_RATE_MAX = 0.001   # 0.1%
ERROR_ABS_MAX = 200

MANIFEST_HEADER = "# migration: "

# An edit takes (item_id, item) and mutates `item` in place, returning the names
# of what it changed. An empty list means "already correct, do not rewrite" --
# which is what keeps a re-run from re-uploading ~100k unchanged objects.
Edit = Callable[[str, dict], list]


def error_tolerable(errors: int, processed: int) -> bool:
    """The run's exit gate, as a function so a test can assert on the real one.

    It was previously re-implemented inside the test suite, which made it one
    fact derived twice: the copy happened to agree, and nothing made it.
    """
    if errors == 0:
        return True
    rate = errors / processed if processed else 1.0
    return errors <= ERROR_ABS_MAX and rate <= ERROR_RATE_MAX


# =============================================================================
# Manifest — resumability, and refusing another migration's ledger
# =============================================================================

def manifest_load(path: str, migration: str) -> set:
    """Completed ids, refusing a manifest that belongs to a different migration.

    Resumability is not optional at this scale: an interrupted run otherwise
    leaves an unknown subset rewritten, with no record of which. But the same
    mechanism is a loaded gun pointed at the next migration -- `todo` is
    `published - done`, so a ledger from an unrelated run reads as "these are
    already finished" and the run skips them, silently, exiting 0.

    Measured: data/backfill_done.txt holds 98,040 ids against 102,460 published.
    Handed to #34's migration it would have rewritten 4,420 items (4.3%), left
    98,040 carrying the old collection id and the old asset key, and reported
    success.

    A file with no `# migration:` header is refused rather than trusted. There
    is no safe default: guessing "it is probably mine" is exactly the assumption
    that produces the 4.3% run.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        # A zero-byte file is unambiguous -- it lists no ids, so nothing can be
        # skipped on account of it, and refusing would lock the tool out of a
        # path someone merely touched. Only a NON-empty file with no header is
        # ambiguous, and that is the dangerous case.
        return set()

    with open(path) as fh:
        first = fh.readline()
        if not first.startswith(MANIFEST_HEADER):
            raise RuntimeError(
                f"{path} carries no '{MANIFEST_HEADER}' header, so which "
                f"migration it belongs to is unknown. Refusing to read it as "
                f"'{migration}' -- an unrelated ledger reads as work already "
                f"done and is skipped silently."
            )
        owner = first[len(MANIFEST_HEADER):].strip()
        if owner != migration:
            raise RuntimeError(
                f"{path} belongs to migration '{owner}', not '{migration}'. "
                f"Reading it would skip every id it lists."
            )
        return {line.strip() for line in fh if line.strip()}


def manifest_open(path: str, migration: str):
    """Open the manifest for append, stamping the header on a new file."""
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    fh = open(path, "a")
    if fresh:
        fh.write(f"{MANIFEST_HEADER}{migration}\n")
        fh.flush()
    return fh


# =============================================================================
# Fetch and rewrite
# =============================================================================

def published_item_ids(collection_path: str | None = None) -> set:
    """Item ids the published collection actually links.

    An item that is newly detected but not yet published must not be fetched --
    it does not exist yet, and item_create.py builds it correctly from source.
    Reading the real link list rather than assuming keeps the two populations
    from overlapping.
    """
    if collection_path and os.path.exists(collection_path):
        with open(collection_path) as fh:
            collection = json.load(fh)
    else:
        logger.info("Fetching published collection from %s", COLLECTION_URL)
        with urllib.request.urlopen(COLLECTION_URL, timeout=300) as r:
            collection = json.load(r)

    ids = set()
    for link in collection.get("links", []):
        if link.get("rel") != "item":
            continue
        name = link["href"].rsplit("/", 1)[-1]
        if name.endswith(".json"):
            name = name[: -len(".json")]
        # Links are stored percent-encoded; item ids are not.
        ids.add(name.replace("%20", " "))
    return ids


def item_fetch(item_id: str) -> dict:
    """GET a published item JSON. Raises on any non-200 rather than returning {}."""
    url = f"{PATH_S3_STAC}/{encode_url_for_gdal(item_id)}.json"
    with urllib.request.urlopen(url, timeout=60) as r:
        if r.status != 200:
            raise RuntimeError(f"{url} -> HTTP {r.status}")
        return json.load(r)


def process_one(item_id: str, edit: Edit, out_dir: str,
                attempts: int = 3) -> tuple:
    """Returns (item_id, outcome): written | unchanged | error:<msg>.

    Retries before giving up, so that transient failures never reach the run's
    exit code at all.
    """
    last = ""
    for attempt in range(attempts):
        try:
            item = item_fetch(item_id)
            changed = edit(item_id, item)
            if not changed:
                return item_id, "unchanged"
            # Write via a temp file and rename, for the same reason
            # collection_patch does: an interrupted write must not leave a
            # TRUNCATED item, which would be synced to S3 as a published item
            # nothing can parse. A run killed by the job timeout is the real
            # case at this scale, and it lands mid-write on whatever item was
            # in flight. os.replace is atomic on the same filesystem.
            #
            # It also protects the resumability logic: skip_already_staged and
            # the completeness check both treat "a file exists" as "this id is
            # done", and a half-written file would satisfy both.
            dest = os.path.join(out_dir, f"{item_id}.json")
            tmp = f"{dest}.tmp"
            try:
                with open(tmp, "w") as fh:
                    json.dump(item, fh)
                os.replace(tmp, dest)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            return item_id, "written"
        except Exception as e:  # noqa: BLE001 - reported per item, never fatal
            last = str(e)
            if attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))  # linear backoff
    return item_id, f"error:{last}"


def run_rewrite(todo: list, edit: Edit, out_dir: str, manifest_fh, errors_fh,
                workers: int = 16, desc: str = "Rewriting") -> dict:
    """Fan out over `todo`, returning {written, unchanged, error}."""
    lock = threading.Lock()
    counts = {"written": 0, "unchanged": 0, "error": 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(process_one, i, edit, out_dir) for i in todo]
        for fut in tqdm(concurrent.futures.as_completed(futures),
                        total=len(futures), desc=desc):
            item_id, outcome = fut.result()
            with lock:
                if outcome.startswith("error:"):
                    counts["error"] += 1
                    errors_fh.write(f"{item_id}\t{outcome}\n")
                    errors_fh.flush()
                else:
                    counts[outcome] += 1
                    # The manifest records a completed local WRITE, so an
                    # interrupted run resumes without re-fetching. It is
                    # therefore a claim that these ids are PUBLISHED only once
                    # the caller has published them -- `todo = published -
                    # manifest` on the next run, so persisting it after a run
                    # that never synced would make the work be skipped forever
                    # rather than redone. The CI job drops it when the sync did
                    # not run; see update.yml's cache commit.
                    manifest_fh.write(f"{item_id}\n")
                    manifest_fh.flush()
    return counts


def verify_rewrite(sample_ids: list, out_dir: str, edit: Edit,
                   expect=None) -> tuple:
    """Assert the rewritten file is EXACTLY what the edit predicts. (fails, checked)

    Re-fetches the published item, applies `edit` to that fresh copy, and
    DeepDiffs the prediction against the file on disk -- expecting no
    difference at all.

    This replaces the allowlist the backfill used, rather than widening it. An
    allowlist states what MAY differ, so it has to grow a branch per migration
    and each branch enlarges what the check tolerates; re-derivation states that
    the whole document is predicted, and cannot be widened. It also catches
    things no allowlist reaches: a partial write, a race with another writer,
    or a file written under the wrong id.

    `expect(published, rewritten) -> list[str]` carries the INTENT separately.
    Without it a sample in which the edit happened to do nothing would pass
    while proving nothing, which is what the allowlist version tolerated.
    """
    from deepdiff import DeepDiff

    failures = 0
    checked = 0
    for item_id in sample_ids:
        local_path = os.path.join(out_dir, f"{item_id}.json")
        if not os.path.exists(local_path):
            continue
        checked += 1
        published = item_fetch(item_id)
        with open(local_path) as fh:
            rewritten = json.load(fh)

        predicted = json.loads(json.dumps(published))  # a fresh copy to mutate
        edit(item_id, predicted)

        diff = DeepDiff(predicted, rewritten, ignore_order=False)
        if diff:
            failures += 1
            logger.error("UNPREDICTED DIFF %s: %s", item_id, diff)
            continue

        if expect is not None:
            problems = expect(published, rewritten)
            if problems:
                failures += 1
                logger.error("INTENT NOT MET %s: %s", item_id, problems)

    return failures, checked


def skip_already_staged(todo: list, out_dir: str) -> tuple:
    """Drop ids that already have a file in out_dir. (kept, skipped)

    A rewrite fetches the PUBLISHED body. If an earlier step in the same run
    has already built a fresher one into the same directory -- CI rebuilds
    items whose DSM pairing changed before the rewrite step runs -- then
    rewriting would overwrite that rebuild with an edited copy of the stale
    published version, losing the rebuild silently.
    """
    kept, skipped = [], []
    for item_id in todo:
        # `.json` exactly. A leftover `<id>.json.tmp` from an interrupted write
        # is not a staged item, and os.path.exists on the real name is already
        # false for it -- stated here so the distinction is not re-litigated.
        if os.path.exists(os.path.join(out_dir, f"{item_id}.json")):
            skipped.append(item_id)
        else:
            kept.append(item_id)
    return kept, skipped
