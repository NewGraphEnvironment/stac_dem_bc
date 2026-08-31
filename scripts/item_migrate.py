#!/usr/bin/env python3
"""Migrate published items to the renamed collection and asset key (#34).

Rewrites all 102,460 published item JSONs in place. Two fields change per item:

    "collection": "stac-dem-bc"  ->  the current COLLECTION_ID
    assets["image"]              ->  assets["dem"]

Item ids do NOT change, so S3 keys do not change: this overwrites in place, and
there are no new objects, no orphans, no cleanup pass, and no href anywhere
becomes invalid. Named `migrate` rather than `rename` for that reason -- nothing
here renames an item.

WHAT IT DOES NOT TOUCH, and why each is load-bearing:

  asset hrefs      They contain the literal path segment `/dem/` -- the source
                   product directory on the BC objectstore. Any substitution
                   over the href text would corrupt them.
  links[collection] Points at the S3 BUCKET, which keeps the name stac-dem-bc.
                   The bucket is IaC-managed in rtj and out of scope.
  collection.json  Its own edit is collection_patch.py --check/--path. Item ids
                   and hrefs are unchanged, so every link in it stays valid.
  anything else    No pystac round-trip: json.load -> edit -> json.dump. pystac
                   normalises key order, stac_version, links and self-href, and
                   on ~102k items that is a large unreviewed diff hiding the
                   two-line one we intend.

WHY A HALF-DONE RUN IS THE THING TO FEAR. Item ids are unchanged by design, so
`catalogue_register.sh` set equality reports IN SYNC over a fully mixed
catalogue; item_register.sh routes each item by its OWN `collection` field, so a
stale body upserts back into the old collection successfully, with no error; and
both asset keys are legal STAC. Nothing that existed before #34 could see it.
Hence the reconciliation at the end of main() -- over the full population, from
one producer -- and `register_manifest.py audit-items`, which is the same
property checked against files on disk.

Usage:
    python scripts/item_migrate.py --limit 20 --dry-run   # inspect, write nothing
    python scripts/item_migrate.py --limit 200 --verify 20
    python scripts/item_migrate.py                        # the whole migration
"""

import argparse
import json
import logging
import os
import sys

from collection_patch import COLLECTION_ID
from item_rewrite import (
    ERR_EDIT,
    ERR_FETCH,
    ERROR_ABS_MAX,
    ERROR_RATE_MAX,
    error_tolerable,
    item_fetch,
    manifest_load,
    manifest_open,
    published_item_ids,
    run_rewrite,
    skip_already_staged,
    verify_rewrite,
)
from stac_utils import ASSET_DEM, get_output_dir

logger = logging.getLogger(__name__)

MANIFEST = "data/migrate_done.txt"
ERRORS_LOG = "data/migrate_errors.txt"
# Stamped into the manifest, and checked when it is read back. NOT the same
# ledger as the backfill's: data/backfill_done.txt holds 98,040 ids against
# 102,460 published, so reading it here would present 98,040 items as finished,
# migrate the remaining 4,420, and exit 0.
MIGRATION = "34-collection-rename"

# old key -> new key. A mapping rather than a pair so a future migration can
# state its own without editing the function.
ASSET_RENAMES = {"image": ASSET_DEM}


def item_migrate(item: dict, collection_id: str = COLLECTION_ID,
                 asset_renames: dict = None) -> list:
    """Apply the migration in place. Returns the names of what changed.

    An empty list means the item is already correct and must not be rewritten --
    that is what keeps a re-run from re-uploading ~102k unchanged objects, and
    what makes this safe to run twice.

    Raises when an item carries BOTH the old and the new asset key. That is a
    half-done previous run, and there is no correct way to continue: keeping
    both leaves a duplicate asset in the published catalogue, and choosing one
    silently discards whichever the other run wrote. Failing names it instead.
    """
    renames = ASSET_RENAMES if asset_renames is None else asset_renames
    # Two old keys mapping to one new key would make the comprehension below
    # silently drop one asset -- the both-keys guard checks each pair against
    # its own target, not the new keys against each other. Not reachable with
    # the single-entry ASSET_RENAMES, and stated here so it stays unreachable.
    new_keys = list(renames.values())
    if len(set(new_keys)) != len(new_keys):
        raise ValueError(f"asset_renames maps two keys onto one: {renames}")
    changed = []

    assets = item.get("assets")
    if isinstance(assets, dict):
        for old, new in renames.items():
            if old in assets and new in assets:
                raise ValueError(
                    f"{item.get('id', '<no id>')} carries both '{old}' and "
                    f"'{new}' assets -- a half-done migration. Refusing to "
                    f"guess which one to keep."
                )
        # Rebuilt by comprehension rather than pop-then-assign, so the new key
        # sits where the old one sat. A byte diff of a published item then shows
        # one key changed instead of one removed and another appended at the
        # end, which is what a reviewer has to read at 102k scale.
        renamed = {renames.get(k, k): v for k, v in assets.items()}
        if list(renamed) != list(assets):
            item["assets"] = renamed
            changed.extend(f"asset:{o}->{n}" for o, n in renames.items()
                           if o in assets)

    if item.get("collection") != collection_id:
        item["collection"] = collection_id
        changed.append("collection")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate published items to the renamed collection and asset key")
    parser.add_argument("--collection", default=None,
                        help="Local collection.json (default: fetch the published one)")
    parser.add_argument("--collection-id", default=COLLECTION_ID,
                        help=f"Target collection id (default: {COLLECTION_ID})")
    parser.add_argument("--out-dir", default=None, help="Default: $STAC_OUTPUT_DIR")
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--errors-log", default=ERRORS_LOG)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N items (rehearsal only; suppresses "
                             "the completeness check)")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    parser.add_argument("--verify", type=int, default=0,
                        help="After the run, deepdiff N rewritten items against published")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    out_dir = args.out_dir or get_output_dir(test_only=False)
    os.makedirs(out_dir, exist_ok=True)

    published = published_item_ids(args.collection)
    logger.info("Published items: %d", len(published))

    done = manifest_load(args.manifest, MIGRATION)
    if done:
        logger.info("Manifest: %d items already done, skipping", len(done))

    todo = sorted(published - done)

    # An earlier step in the same run may have built a FRESHER item into this
    # directory -- CI rebuilds items whose DSM pairing changed before this step.
    # Migrating one would fetch the published body and overwrite that rebuild
    # with an edited copy of the stale version, silently losing it.
    # `if args.limit:` would read 0 as "no limit" and run the whole catalogue for
    # someone who asked for nothing. Test for None, and reject a negative rather
    # than silently slicing from the end.
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")

    todo, staged = skip_already_staged(todo, out_dir)
    # A dry run previews; it must not exit 1 over the contents of a directory it
    # was never going to write to. A preview flag is only safe if it previews.
    if staged and not args.dry_run:
        logger.info("Skipping %d id(s) already staged in %s by an earlier step",
                    len(staged), out_dir)
        # "A file exists" is not "this item is correct". Staged bodies are
        # counted as migrated by the completeness reconciliation below, so
        # counting them unchecked would let `item_backfill --out-dir D` followed
        # by `item_migrate --out-dir D` report the whole catalogue complete
        # having migrated nothing. Check them where they are cheap to check.
        wrong = []
        for item_id in staged:
            path = os.path.join(out_dir, f"{item_id}.json")
            try:
                with open(path) as fh:
                    body = json.load(fh)
                if item_migrate(body, args.collection_id):
                    wrong.append(item_id)
            except (OSError, ValueError) as e:
                wrong.append(f"{item_id} ({e})")
        if wrong:
            logger.error("%d staged item(s) are NOT in the migrated shape, so "
                         "they cannot be counted as done: %s",
                         len(wrong), wrong[:3])
            return 1
        logger.info("All %d staged item(s) are already in the migrated shape",
                    len(staged))

    if args.limit is not None:
        todo = todo[: args.limit]
    logger.info("To process: %d items", len(todo))

    def edit(item_id: str, item: dict) -> list:
        return item_migrate(item, args.collection_id)

    def expect(published_item: dict, rewritten: dict) -> list:
        """The INTENT, asserted separately from the prediction.

        verify_rewrite proves the file is exactly what `edit` produces. That is
        true even of an edit that did nothing, so a sample where the migration
        was a no-op would otherwise pass while proving nothing.
        """
        problems = []
        if rewritten.get("collection") != args.collection_id:
            problems.append(f"collection is {rewritten.get('collection')!r}")
        assets = rewritten.get("assets") or {}
        for old, new in ASSET_RENAMES.items():
            if old in assets:
                problems.append(f"still carries the old asset key {old!r}")
            if old in (published_item.get("assets") or {}) and new not in assets:
                problems.append(f"lost the {new!r} asset")
        return problems

    if args.dry_run:
        logger.info("Dry run - inspecting %d items without writing", min(len(todo), 20))
        for item_id in todo[:20]:
            item = item_fetch(item_id)
            logger.info("  %s -> %s", item_id[:60], edit(item_id, item) or "unchanged")
        return 0

    manifest_fh = manifest_open(args.manifest, MIGRATION)
    # Per-item failures go to a FILE, never to a stream tqdm shares: its bar
    # uses carriage returns and overwrites interleaved log lines, which is how
    # a previous run's failing ids became unrecoverable from the log.
    errors_fh = open(args.errors_log, "w")
    try:
        counts, errors = run_rewrite(todo, edit, out_dir, manifest_fh, errors_fh,
                                     workers=args.workers, desc="Migrating")
    finally:
        manifest_fh.close()
        errors_fh.close()

    logger.info("written %d | unchanged %d | error %d",
                counts["written"], counts["unchanged"], counts["error"])

    processed = sum(counts.values())
    # The population, not this run's share of it. A resumable job's runs shrink
    # towards zero, so measuring against the run tightens the gate the closer
    # you get to finishing -- see error_tolerable. A --limit rehearsal is not
    # doing the whole job and passes no population.
    tolerable = error_tolerable(counts["error"], processed,
                                population=0 if args.limit is not None else len(published))
    if counts["error"]:
        rate = counts["error"] / processed if processed else 0.0
        logger.warning("error rate %.5f (%d/%d); tolerance %.5f / %d abs -> %s",
                       rate, counts["error"], processed, ERROR_RATE_MAX,
                       ERROR_ABS_MAX, "ACCEPTED" if tolerable else "EXCEEDED")
        logger.warning("failed ids written to %s - re-run to retry only those",
                       args.errors_log)

    if args.verify:
        sample = [i for i in todo
                  if os.path.exists(os.path.join(out_dir, f"{i}.json"))][: args.verify]
        logger.info("Verifying %d migrated items against published...", len(sample))
        failures, checked = verify_rewrite(sample, out_dir, edit, expect)
        if failures:
            logger.error("VERIFY FAILED on %d of %d items", failures, checked)
            return 1
        if checked == 0:
            # NOT a failure. An empty sample means either the manifest is
            # already complete or every item came back correct -- both are
            # success, and both are the routine state of a re-run. Failing here
            # would abort the very case the dispatch description calls safe to
            # repeat. The statement that covers the population is the
            # completeness reconciliation below, and it still runs.
            logger.info("Verify sampled 0 items (nothing was rewritten this "
                        "run); completeness is asserted below")
        else:
            logger.info("Verify passed on %d items", checked)

    # THE statement that covers all 102,460, and the only one that can. A sample
    # -- any sample -- says nothing about the population, and a mixed population
    # is invisible to every other check this repo has.
    #
    # Both sides come from the published collection's item links, so they cannot
    # drift the way a count taken from one artifact and a set produced from
    # another can.
    #
    # Staged ids count as migrated, and must: they were skipped precisely
    # because an earlier step built a FRESHER body for them, and item_create
    # builds with the current collection id and asset key. Suppressing the whole
    # check whenever anything was staged would have disabled it on exactly the
    # runs that matter -- CI rebuilds items whose DSM pairing changed, so a real
    # cutover in a month with pairing changes would have asserted nothing.
    if args.limit is None:
        migrated = manifest_load(args.manifest, MIGRATION) | set(staged)
        missing = published - migrated
        extra = migrated - published
        # Only `missing` is a failure. `extra` -- an id in the manifest that is
        # no longer in collection.json -- is what an UPSTREAM DELETION looks
        # like, and deletion pruning is still open (#28). Aborting on it would
        # fail a healthy resumed migration across a month in which tiles were
        # removed. Worth saying, not worth stopping for.
        if extra:
            logger.warning("%d migrated id(s) are no longer published (an "
                           "upstream deletion looks like this; see #28): %s",
                           len(extra), sorted(extra)[:3])
        # Split `missing` by CAUSE. An id this run failed to fetch is missing
        # for a reason the error-rate gate already judges, and the next run
        # retries it for free. An id missing for any OTHER reason was never
        # attempted, which is a harness fault and is always fatal.
        #
        # Without this split the completeness check makes error_tolerable
        # unreachable: an errored id is always in `todo`, so ANY error means
        # `missing`, means exit 1 -- which in CI skips the sync, which discards
        # the manifest, which throws away every completed item. At 102,460 items
        # against a measured history of 34 and 2 transient failures per run, the
        # migration could only ever finish on a run where none of 102,460
        # fetches failed three times. A loop with no exit, produced by two
        # guards that are each correct alone.
        # Three causes now, needing three different answers. Telling an operator
        # to re-run something that cannot succeed sends them round a loop, which
        # is the same misdirection as a guard that fires and then points at the
        # wrong fix.
        transient = {i for i, o in errors.items() if o.startswith(ERR_FETCH)}
        deterministic = {i for i, o in errors.items() if o.startswith(ERR_EDIT)}
        unattempted = missing - transient - deterministic

        if unattempted:
            logger.error("INCOMPLETE: %d published, %d never attempted",
                         len(published), len(unattempted))
            logger.error("  e.g. %s", sorted(unattempted)[:3])
            return 1

        if deterministic:
            # The item's own content is wrong -- a half-done prior run leaving
            # both asset keys is the reachable case. Every re-run raises the
            # identical error. Say so, and do NOT say "re-run".
            logger.error("%d item(s) CANNOT be migrated without a human: their "
                         "published content is inconsistent, and re-running "
                         "raises the same error every time.", len(deterministic))
            for i in sorted(deterministic)[:3]:
                logger.error("  %s -> %s", i, errors[i])
            logger.error("  full list: %s", args.errors_log)

        if transient and tolerable:
            logger.warning("%d item(s) remain unmigrated, all of them this "
                           "run's transient fetch failures. The run still "
                           "publishes what it completed; RE-RUN to pick them "
                           "up, and do not register until a run reports "
                           "Complete.", len(transient))
            logger.warning("  failed ids: %s", args.errors_log)
        elif transient:
            # `tolerable` is False, so main() returns 1 -- which in CI skips the
            # sync, which discards the manifest, which throws away everything
            # this run completed. Promising a publish here would be a message
            # that contradicts what the run is about to do. The exit itself is
            # correct: an error rate this high means something is broken, and
            # publishing half a catalogue on the strength of it is worse.
            logger.error("%d item(s) failed, ABOVE tolerance. This run's work "
                         "is discarded, not published -- fix the cause and run "
                         "again from the last committed manifest.",
                         len(transient))
            logger.error("  failed ids: %s", args.errors_log)
        else:
            logger.info("Complete: all %d published items are migrated (%d in "
                        "the manifest, %d staged by an earlier step)",
                        len(published), len(migrated) - len(staged), len(staged))
    else:
        logger.warning("Partial run (--limit); completeness NOT asserted. "
                       "Re-run without --limit before publishing.")

    return 0 if tolerable else 1


if __name__ == "__main__":
    sys.exit(main())
