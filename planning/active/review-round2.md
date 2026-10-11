# Review round 2 — branch 2-tif-footprints-are-actually-bounding-box

Scope: the round-1 fixes in 607d197, re-read against update.yml, footprint_extract.py,
item_create.py, item_fields.py, footprint_apply.py, item_rewrite.py and s3_sync-ci.sh. Probes
ran in a copy (/tmp/cc_round2_copy) and the scratchpad. The repo was not touched, apart from
this file.

## Findings

- **[bug] INSIDE round-1 fix 2.** Files: .github/workflows/update.yml:505-510 (the clear),
  scripts/item_create.py:379-387 and :423, and scripts/stac_utils.py:326-337.
  - **The guard does not test what it needs to.** The rebuild list is emptied when
    `steps.rebuild.outcome == success`. But `item_create.py --urls-file` drops every
    per-URL failure: `filter(None, ...)` at :379, and `process_item` returns None on any
    exception or on an `is_geotiff: False` cache row. It then returns 0 whatever happened
    (:423). Even "Parallel execution failed" at :388 sets `results = []` and exits 0.
  - **What happens to a URL that failed:**
    - Its item is never staged and never synced.
    - The commit step empties the list anyway.
    - Its footprints.csv row is already committed, so footprint_extract never lists it
      again.
    - The item keeps its raster extent permanently. Once data/footprint_done.txt holds
      it, the one-time rewrite skips it too.
    - Nothing warns. The shortfall step counts new items only, and it runs before the
      rebuild.
  - **This is not hypothetical.** Measured on the committed data/stac_geotiff_checks.csv:
    **58,019 of 102,460** rows are `is_geotiff=True` with no `transform`.
    - `load_validation_cache` re-reads every such URL over /vsicurl/ (`needs_upgrade`), in
      a single attempt with no retry.
    - On any transient failure `geotiff_extract_metadata` returns `is_geotiff: False`
      (stac_utils.py:326-337). That value is written over the good row and committed by
      `git add -A data/`.
    - So a network blip on the rebuild of an old item does three things. It silently
      skips the item. It clears the item from the list. It permanently re-marks a
      published tile as unreadable in the validation cache.
    - In steady state, the old items that reach the list are exactly these: tiles whose
      first extract hit a transient error, so they are read in a later monthly run. The
      11 rows with a transform but no epsg go to rio_stac's remote read on every
      rebuild, with the same exposure.
  - **Suggested fix:** in the commit step, remove from the list only URLs whose
    `<url_to_item_id(url)>.json` exists in `$STAC_OUTPUT_DIR`, since that file is what the
    sync uploaded. Keep the rest. Or have item_create `--urls-file` report the URLs it
    dropped, so they can be kept. A URL that fails deterministically then stays listed
    and is retried each month, which is visible and harmless. The proxy here,
    "the step exited 0", stands in for the property "every listed item reached S3", and
    item_create was never written to make them the same.

- **[fragile] INSIDE round-1 fix 2, on the dispatch branch of the clear.**
  update.yml:506, `|| [ "${{ inputs.footprint }}" = "true" ]`.
  - The clear assumes the footprint rewrite rewrote every listed item. But
    footprint_apply.py:245-247 exits 0 when transient fetch failures fall within
    tolerance ("RE-RUN to pick them up"). Those ids are not in the manifest, the sync
    still succeeds, and the list is emptied.
  - Afterwards, only another footprint *dispatch* can fix those items. No monthly run
    will list them again.
  - This is recoverable only if the operator acts on the log line. Same remedy as above:
    clear only the URLs whose item file was staged.

- **[fragile] Not inside a fix.** update.yml:213-224, the comment "A failure here degrades
  geometry for a month" and "the next run computes it". Both the footprint step and the
  rebuild step are gated on `steps.detect.outputs.changes == 'true'`.
  - So a tile the time box did not reach, and any URL left on the cumulative list by a
    failed run, wait for the next month in which the upstream bucket *changes*, not for
    the next run. No-change months do occur ("a no-change month exits in ~6 min").
  - Those items are published at their extent for an unbounded time.
  - The same gate means a footprint dispatch on a no-change day cannot run the extract.
    If urls_list.txt holds tiles the committed cache lacks, footprint_apply.py:122-129
    refuses with "Run footprint_extract.py to completion first", and CI has no path to do
    that. This is likely right after merge, if a cron on main has added tiles since the
    cache was built. It fails loudly, so it publishes no wrong data.

## Checked and found sound

- **Round-1 fix 1 (`migrate=False`): no other migration on any item path.**
  - rio-stac 0.12.0 emits projection v1.1.0 with `proj:epsg` (`PROJECTION_EXT_VERSION =
    "v1.1.0"`).
  - `item.save_object` does not migrate.
  - item_create (cache and rio_stac branches) built from one fixture on cc849006 and on
    HEAD, with no footprint row and no /gdwuts/ path, produces byte-identical JSON apart
    from the temp path. So the round trip is otherwise lossless.
  - The collection path (`Collection.from_file`, which does migrate, then `save_object`)
    was round-tripped on the live 102k-link collection.json. It changed no non-link
    member and left the new `related` link intact. A second `collection_patch` after the
    round trip reports `[]`, so there is no monthly republish loop.
- **Cumulative list mechanics.**
  - A URL is appended only after its row is flushed.
  - New URLs are excluded, and urls_new.txt is rewritten or removed by detect_changes.R
    on every run that has changes.
  - Duplicates from a crash before `changed_finalize` are handled by `sort -u` in the
    rebuild step.
  - The list is not cleared when sync is skipped, failed or cancelled.
  - A rebuild in which every URL failed stages 0 files, so the sync is skipped and the
    list is kept. Only a *partial* failure strands URLs (finding 1).
- **Manifest discard and list clear** are mutually exclusive on `steps.sync.outcome`.
  The `git add` of the emptied file happens after `git add -A data/`.
