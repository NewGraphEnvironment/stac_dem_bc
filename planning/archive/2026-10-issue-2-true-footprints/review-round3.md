# Review round 3 — branch 2-tif-footprints-are-actually-bounding-box

Scope:
- The round-2 fixes (802f7d2) and the decimation change (72c0444), read against update.yml,
  footprint.py, footprint_extract.py, footprint_apply.py, item_fields.py, item_create.py,
  item_rewrite.py, item_validate.py, dsm_pair.py, detect_changes.R and s3_sync-ci.sh.
- Probes ran in /tmp/cc_round3_copy, with data/ copied but footprints.csv excluded, and in
  the scratchpad. The repo was not touched apart from this file, and the running extract was
  only read (`grep`/`awk`).
- The 83 footprint, item-field, workflow and apply tests pass in the copy.

## Mechanism

Every earlier finding has the same shape. **A step decides "done", "skip" or "nothing to
do" from an artifact that is cheap to check locally, and that artifact is not produced by
the event the property depends on.** The artifact also outlives that event's failure,
because the commit step runs under `always()`. It goes wrong in three ways:

1. **Another writer can produce the artifact.** In R2, "the rebuild step exited 0" was also
   produced by an item_create that had dropped URLs. Now "`<id>.json` is staged" can also be
   produced by item_backfill (F2), and "a row is in the cache" by any footprint_extract run
   that was not given `--changed-out` (F3).
2. **The artifact survives from an earlier run and is read as this run's.** This is the
   `urls_new.txt` hazard R2 guarded against. `urls_pairing_changed.txt` now arrives the same
   way (F1).
3. **A library default makes the artifact look like the property.**
   - pystac `migrate=True` (R1).
   - `item_validate --incremental` reads "id in the ledger" as "this body was validated" (F5).
   - item_create writes the raw URL as an href (F4).
   - rasterio's scaled identity transform passes `is_identity` (F6).
   - urllib returns a short body without raising (noted below the table).

Both hints are confirmed. The second is a special case of the first: the default produces
an artifact that passes a check it should fail.

## Enumeration

| # | Decision / artifact | Evidence it acts on | Property it stands for | Equal? |
|---|---|---|---|---|
| 1 | `footprints.csv` row `download`/`overview` (skips the tile forever) | One read passed NoGeoref, CrsMismatch and OutsideBC, and `--audit` re-checks WKT validity and BC | The footprint of the object's current bytes | Yes at read time. detect_changes compares URL **sets**, so an object replaced in place under the same key is never re-read, and its `file:checksum`/`size` go stale. That boundary is shared by every cache here; not flagged. |
| 2 | Refusal rows `empty`, `crs_mismatch`, `no_georef`, `outside_bc` (never retried) | One read raised a deterministic refusal | "No publishable footprint", so the item keeps its geometry | Consequence yes (it fails safe). The label is wrong for decimated no-georef rasters (F6). |
| 3 | Transient failure: not cached, retried next run | The exception was not a Footprint* refusal | The failure was network | Assumed. A deterministic non-Footprint failure would retry forever, and would block footprint_apply's coverage check forever. Measured as unreached: 0 listed URLs are `is_geotiff=False` in stac_geotiff_checks.csv, and the local full run shows 0 errors in its first 1,969 rows. |
| 4 | `checksum`/`size` from `_download` | The read loop reached `b""` | The whole object was hashed | Holds by layout (see the note below the table). Not flagged. |
| 5 | Append to `urls_footprint_changed.txt` | A row was written by a run given `--changed-out`, for a URL not in `--new-urls` | A published item lacks this row | Only for the CI caller. The default CLI and README Quick Start write rows without listing them, and the row is flushed before the list entry (F3). |
| 6 | `--prune-changed`: drop a URL from the list | `<url_to_item_id(url)>.json` exists in `$STAC_OUTPUT_DIR` and the sync succeeded | The published body carries this URL's footprint | **No** (F2). The id mapping matches item_create (both use `url_to_item_id`; spaces are consistent). |
| 7 | `pending` output | The list file is non-empty at the end of the step; written before `exit 1`, so it is set on failure too | Work for the rebuild | Yes |
| 8 | Rebuild input `urls_pairing_changed.txt` | The file exists and is non-empty | The pairing changed **this run** | **No** on a `pending` run with no upstream change (F1) |
| 9 | `--new-urls` only when `new_urls == 'true'` | detect's output | This run's new URLs | Yes. R2's premise was wrong, but the guard is harmless: detect_changes.R:107-112 deletes `urls_new.txt` when there are no new URLs, so it is never last month's. |
| 10 | `data/footprint_done.txt` | Local write or `unchanged`, and dropped when the sync did not succeed | Published in the rewritten shape | Yes |
| 11 | footprint_apply staged-item check | `item_fields_apply(staged)` returns `[]` | The staged body is already rewritten | Yes. This is the stronger predicate F2 should borrow. |
| 12 | footprint_apply coverage check | `urls_list − footprint rows (any method)` is empty | Every listed URL has a decision | Yes |
| 13 | footprint_apply completeness | manifest ∪ staged ⊇ published, split by cause | The rewrite is complete | Yes. A tolerated transient exits 0 (R2, item 2). |
| 14 | `item_fields.py audit` | Field homogeneity; "with_footprint" is the presence of `raster:bands` | No mixed catalogue in the #2/#55 fields | Yes for what it checks. It does not check hrefs, so F4 passes it. |
| 15 | `item_validate --incremental` gate | The id is not yet in `stac_item_validation.csv` | This body was schema-validated | **No** for any rebuilt item (F5) |
| 16 | `stac_geotiff_checks.csv` | One metadata read | The URL is a readable GeoTIFF | #56, out of scope |
| 17 | `dem_dsm_pairs.csv` vs `urls_pairing_changed.txt` | A single-run diff, and the CSV is committed under `always()` | The pairing change is published | No, but pre-existing on main: R1's mechanism in the pairing ledger. A failed rebuild or sync loses a pairing change permanently. Out of scope; worth an issue. |
| 18 | `urls_list.txt` committed under `always()` | — | New URLs were published | Pre-existing (R1 noted it) |
| 19 | pystac / rio_stac / json round trips | `from_dict(migrate=False)`; `Collection.from_file` migrates but R2 measured no change; rio-stac emits v1.1.0; `json.loads(json.dumps(mapping))` | The body is unchanged apart from the edit | Yes (R1, R2) |
| 20 | item_create asset href | `href=url`, the raw URL | The published href is percent-encoded (#25) | **No** (F4) |
| 21 | `_read` decimated transform | `src.transform * scale(w/ow, h/oh)` | Same ground extent | Yes. The probe gave identical extents (500000, 5498700, 501100, 5500000) at full and 1/3 resolution. |
| 22 | NoGeoref guard after decimation | `transform.is_identity` on the **scaled** transform | The raster has no geotransform | **No** (F6) |

**Note on #4 (truncated downloads).**
- A probe confirmed that Python 3.12 urllib `read(1<<20)` returns 300,000 of a declared
  1,000,000 bytes with no exception. So a truncated download would be hashed and sized as if
  it were complete.
- A strip TIFF cut by 4 or 4,400 bytes raises on a full read but **reads OK when decimated**
  (`MAX_READ_PIXELS` patched), because nearest sampling skips the trailing strips.
- In practice the open still fails. The one 712 MB mapsheet tile checked
  (`bc_082e011_xli1m_utm11_2019.tif`) has its IFD at offset 711,573,472, which is in the last
  ~95 KB of the file. Any tail truncation removes it, so the open raises and the read is
  retried.
- So this is not flagged. `size == Content-Length` (the length is already fetched) would make
  it independent of file layout.

## Findings

- **[fragile] .github/workflows/update.yml:284. INSIDE the R2 fix** (the rebuild now runs on
  `pending` with no upstream change).
  - **What happens:** the rebuild concatenates `data/urls_pairing_changed.txt`
    unconditionally. That file is written only by dsm_pair (:162-163), which runs only when
    `changes == 'true' || inputs.backfill`, and it is committed. So on a run with no
    upstream change and `pending=true`, the rebuild re-reads **the last change-month's
    pairing list** and rebuilds and republishes every item on it, every such month, until
    the next upstream change.
  - **Why it was safe before:** on main the rebuild ran only after dsm_pair had just
    rewritten the file.
  - **Cost:**
    - The redundant republish pulls those items through #56: a transient metadata read
      overwrites a good validation row with `is_geotiff=False`.
    - They also go through the rio_stac remote-read branch for the 58k rows that have no
      transform.
    - Any of the 88 space-named items on that list gets F4.
  - **Fix:** the same shape as the `--new-urls` fix. Include the pairing file only when
    `steps.detect.outputs.changes == 'true' || inputs.backfill`, the condition under which
    dsm_pair ran this run.

- **[fragile] .github/workflows/update.yml:533 / scripts/footprint_extract.py:135-152
  (`changed_prune`). INSIDE the R2 fix.**
  - **The gap:** the prune drops a URL when *a file named* `<id>.json` is staged. But on a
    dispatch run, item_backfill and item_migrate also write into `$STAC_OUTPUT_DIR`, and
    they write an edited copy of the **published** body.
  - **How an item gets stranded:**
    1. On a backfill dispatch with `pending`, the rebuild's item_create fails for a listed
       URL. For example, #56 leaves `is_geotiff=False`, which is a silent skip with exit 0.
    2. item_backfill then finds that item needs a pairing or href fix and stages the
       published body, which still has its extent geometry.
    3. The sync succeeds, the prune sees the file, and the URL leaves the list with its
       item at its extent. Its row is cached, so nothing lists it again.
  - **Reach:** narrow, because it needs a backfill dispatch to intersect a failed rebuild.
  - **Fix:** prune only when the staged body also satisfies
    `item_fields_apply(body, url, footprints) == []`, the predicate footprint_apply already
    uses for staged items (enumeration #11). It costs one json.load per listed URL.

- **[fragile] scripts/footprint_extract.py:314 (`--changed-out` default None), :260-265, and
  scripts/README.md:33. The other callers of the R1 fix.**
  - **What the fix covers:** the cumulative list is maintained only when the caller passes
    `--changed-out`. The workflow does.
  - **What it misses:** the tool's default does not, and nor do the README Quick Start
    (`python scripts/footprint_extract.py   # ... then only new tiles`) and build_safe.sh.
    build_safe is safe because it follows with a full item_create. But an ad-hoc local run
    over URLs that are already published commits rows that are never listed, so the next
    CI extract sees them as cached. The plausible case is catching up a backlog the 120-min
    time box could not clear, which is the README's local fallback for oversized months.
  - **Result:** those items stay at their extent until someone thinks to re-dispatch
    `footprint`. This is R1's stranding, through the caller the fix did not reach.
  - **A smaller version in the same file:** the row is flushed (:260-261) **before** the list
    entry (:264-265). An ENOSPC on the list write (the disk guard exists because the runner
    is tight) or a kill between the two strands that URL the same way. The reverse order
    fails safe: a URL listed with no row is rebuilt at its extent, pruned, then listed again
    once its row arrives.
  - **Fix:** default `--changed-out` to `data/urls_footprint_changed.txt` whenever `--cache`
    is the default (or refuse to append to the default cache without it), and write the list
    entry first.

- **[bug] scripts/stac_utils.py:409 (`href=url`) and scripts/item_create.py:160
  (`href = href_item`). Pre-existing on main, not inside a fix; this branch adds a path to
  it.**
  - **The defect:** item_create publishes the raw source URL as `assets.dem.href`, so the 88
    `... (2).tif` items get a **literal space**.
  - **Probe** of `bc_082e003_xli1m_utm11_2018 (2).tif`:
    - `item_create.process_item` writes `'... _2018 (2).tif'`.
    - The live S3 body carries `'..._2018%20(2).tif'`, which is #25's repair.
  - **How the branch reaches it:** any rebuild of those items undoes #25. That includes the
    footprint rebuild this branch adds, the stale pairing re-rebuild (F1), and a footprint
    dispatch that skips them as staged. footprint_apply's staged check, `item_fields.py
    audit` and `stacs audit` all pass the result.
  - **Reach now:** a space-named item reaches the footprint list only if its row is missing
    when the branch merges. The local full run already has 14 of them, all `download` or
    `overview`.
  - **Fix:** a one-line `encode_url_for_gdal(url)` in both branches; footprint lookups stay
    keyed by the raw `href_item`. Alternatively, file it with #56.

- **[fragile] .github/workflows/update.yml:373-382 (`item_validate.py --incremental`).
  Pre-existing mechanism; this branch extends its reach.**
  - **The gap:** `--incremental` skips any item whose id is already in
    `stac_item_validation.csv`. Every rebuilt item (pairing or footprint) keeps its id, so
    its new body, with new geometry and the raster, file and lidarbc extensions, is never
    schema-validated.
  - **The worst case:** a `pending` month with no upstream change publishes only rebuilds,
    and the gate prints "No new items to validate" and passes vacuously. For example, a
    MultiPolygon or a `lidarbc:` value the crate schema rejects ships unchecked. New items
    in a later month would catch the schema URL, but not a body-specific fault.
  - **Fix:** validate the rebuilt ids, the union of the two lists, non-incrementally into a
    scratch output, as the rewrite branch already does.

- **[fragile] scripts/footprint.py:253 vs :398. INSIDE the decimation change.**
  - **The defect:** `FootprintNoGeoref` tests `transform.is_identity` on the transform `_read`
    returns. For a decimated read that is `identity * scale(f)`, which is not identity.
  - **Probe:** a CRS-bearing raster with no geotransform, read with `MAX_READ_PIXELS` low,
    has `is_identity False` (`| 3.00, 0, 0 | 0, 3.00, 0 |`). It is then refused as
    `FootprintOutsideBC ... (-127.488, 0.001, ...)` instead of `no_georef`.
  - **Impact:** no wrong geometry is published, because every refusal keeps the item's
    geometry. But the committed cache records the wrong cause, the one whose docstring says
    "a mislabelled CRS", and that sends triage after the wrong defect.
  - **Fix:** check `src.transform.is_identity` inside `_read` before scaling, or return the
    source transform's identity-ness alongside the scaled one.

Checked and found sound:
- **Decimation:**
  - **Transform alignment:** extents are identical at full and 1/3 resolution.
  - **Nearest, not average:** so the -3.4e38 nodata is never blended into a valid value.
  - **Thin data:** features narrower than about f px can be missed, but at f ≤ 3 on a 1 m
    grid that is inside the existing 1,000 m² speck rule. Edges are re-grown by the
    `2*res` outward buffer.
  - **Checksum:** still covers the whole download.
- **`pending`:** written before `exit 1`, so it is set on a failed extract. It gates the
  rebuild, count and commit steps correctly. `"${NEW[@]}"` on an empty array is safe under
  GitHub's `bash -e` (no `-u`).
- **`--prune-changed` id mapping:** matches item_create (both use `url_to_item_id`), so
  space-named URLs map to the same file name. The prune runs only after a successful sync,
  and `aws s3 sync` uploads every freshly written file, because it compares mtimes and
  `--size-only` is not set.
- **Tests:** the 83 footprint, item-field, workflow and apply tests pass in the copy.
