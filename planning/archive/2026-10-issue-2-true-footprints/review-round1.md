# Review round 1 — branch 2-tif-footprints-are-actually-bounding-box

Reviewed `git diff cc849006...HEAD` plus the changed scripts, update.yml and item_rewrite.py
in full. Probes were run from the scratchpad with `PYTHONPATH=scripts`; nothing in the repo
was written except this file.

## Findings

- **[bug]** scripts/item_fields.py:86: `pystac_item_fields_apply` rebuilds the Item with
  `pystac.Item.from_dict(d)`, and in the pinned pystac (1.15.2) `migrate` defaults to **True**.
  That migrates the projection extension from v1.1.0 to v2.0.0 and replaces `proj:epsg` with
  `proj:code`. So from this branch on, every item that `item_create.py` (both the cache and
  rio_stac branches) or `item_reprocess.py` writes is published as `projection/v2.0.0` with
  `proj:code`.
  - The published catalogue does not look like that. A random sample of 60 published items
    (seed 1, from the live collection.json) was 60/60 `projection/v1.1.0` with `proj:epsg` and
    no `proj:code`. The 200 items from the rewrite rehearsal look the same.
  - The one-time `footprint_apply.py` rewrite edits JSON directly, with no pystac round trip,
    so it keeps v1.1.0/`proj:epsg`. Every new monthly item, and every pairing or footprint
    rebuild of a published item, flips to v2.0.0/`proj:code`. The result is a mixed catalogue:
    the shape of #34.
  - Probe: `item_create.process_item` on the tests' TILE fixture writes
    `stac_extensions = [projection/v2.0.0, raster/v1.1.0]` and properties
    `proj:geometry, proj:bbox, proj:shape, proj:transform, proj:code`, with and without a
    footprint row. A DeepDiff across the round trip on an `item_create_from_cache` item shows
    exactly `proj:epsg` removed, `proj:code` added and the projection URL changed.
  - Nothing catches it, so the guard fails toward pass:
    - `item_fields.py audit` does not look at proj.
    - `stacs audit` checks only the collection id and asset keys.
    - `tests/test_item_fields.py:61` compares the cache branch with the rio_stac branch, and
      both are migrated, so they agree.
  - Second-order effect: `footprint._item_crs` (footprint.py:442) reads only
    `proj:epsg`/`proj:wkt2`. Any later `item_footprint_apply` over a published `proj:code` item
    returns crs None and silently leaves `proj:geometry` as the raster extent.
  - Fix: `pystac.Item.from_dict(d, migrate=False)`. Pin it with a test asserting that
    item_create output keeps `proj:epsg` and `projection/v1.1.0` (or, more robustly, has the
    same key set and extension list as a `footprint_apply`-rewritten body). Main did not have
    this, because before this branch item_create never round-tripped through `from_dict`.

- **[bug]** .github/workflows/update.yml:457-462 (`git add -A data/` under `always()`) and
  scripts/footprint_extract.py:303-308 / `changed_select`. The footprint cache is committed
  whatever happens later in the job. But the list of published items to rebuild is derived
  **only from this run's writes**, so the cache acts as a progress ledger without being
  discarded like one.
  - The failure: the extract writes rows for already-published URLs and lists them in
    `urls_footprint_changed.txt`. Then a later step fails or the sync does not run (item_create,
    the rebuild, validation, either audit, or the sync itself).
  - The commit step still commits `data/footprints.csv` with those rows. On the next run those
    URLs are cached, so they are not written, so they are not in the new `--changed-out`, which
    is opened `"w"` and overwrites the list.
  - Those published items then keep their raster-extent geometry **permanently**. Once
    `data/footprint_done.txt` is complete, the one-time rewrite skips them too.
  - The same happens if footprint_extract dies after appending rows but before writing
    `--changed-out`. In that case the rebuild step also reuses last month's committed
    `urls_footprint_changed.txt`.
  - This is the "written data outlives the fix" / manifest-discard hazard that the commit step
    already handles for `*_done.txt`, arriving through a file that is not on its discard list.
    (`urls_list.txt` has the same pre-existing weakness for new URLs. This branch adds a second
    instance.)
  - Possible fixes:
    - Make the changed list cumulative: union it with the previous file, and clear it only
      after a successful sync.
    - Or derive "needs rebuild" from state rather than from this run's writes: published items
      whose URL has a footprint row but whose `assets.dem` lacks `raster:bands`.
    - Or restore `data/footprints.csv` in the existing discard branch when
      `steps.sync.outcome != success`, at the cost of re-reading up to 120 min of tiles.

- **[fragile]** Merge sequencing (update.yml rebuild step + footprint step). If this lands on
  main before the committed `data/footprints.csv` covers the published URLs, the next scheduled
  run works through up to 120 min of *old* tiles.
  - Every one of those tiles is "not new this month", so all of them go into
    `urls_footprint_changed.txt`. They are then rebuilt from source with
    `item_create.py --urls-file`, at roughly 10k published items per run.
  - That is the rebuild path that item_rewrite.py's docstring rejects for published items: 60k
    metadata-cache rows have no spatial metadata, so those items take the rio_stac remote-read
    branch. Each one is a code-path swap nobody reviewed, plus the v2.0.0 flip from finding 1,
    and it risks the 330-min job timeout.
  - Steady state is fine: the only items rebuilt are last month's time-boxed misses, which
    item_create built in the first place.
  - Commit the complete cache on the branch, or at least before the cron on the 3rd, before
    update.yml reaches main.

Checked and found sound:
- **Idempotency:** `item_fields_apply` applied twice across a JSON round trip returns `[]`, for
  both WKT and empty ("the cell") rows, so `proj:geometry` and the bbox are stable.
- **BCGS numbering:** consistent with 092G025 being Vancouver.
- **Rest of footprint_extract.py:** partial-line drop and finalize-before-append, the
  error-gate denominator and the time-box drain behave as their docstrings say.
- **Rewrite dispatch:** the conflict and `if:` conditions are right, and `footprint_done.txt`
  is on the discard list.
- **footprint_apply:** the staged-item check and the completeness reconciliation work.
- **Collection link:** the `related` link target answers 200.
