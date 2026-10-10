# Task: tif "footprints" are actually bounding boxes - Recalculate footprints to exclude `no data` values (#2)

Currently our "footprints" are actually bounding boxes: every item's `geometry` is the WGS84 box of
the raster extent, no-data included. We would prefer to exclude the nodata values. Folds in #55
(`lidarbc:delivery`), whose "next full rebuild" is this issue's rewrite.

## Context (plan approved 2026-10-10, revised the same day)

Every item's `geometry` is the raster extent box (`stac_utils.item_create_from_cache`), or for the
~58k rio_stac-built items a reprojected extent quad. Neither excludes nodata, and neighbouring
tiles overlap.

**Revision 2026-10-10 (Phase 1 + plan review 1).** The approved "grid cell + read edges" design
was falsified: 19% of interior tiles and 25% of edge tiles have gaps inside their cell, so the edge
test finds 8 of 29 (`findings.md`). The full-read cost had been overstated (albers tiles in the
sample): 94,808 small tiles × 5.9 MB ≈ 560 GB ≈ 20 h at 7.8 MB/s, streamed (peak disk ~130 MB).
User decisions, 2026-10-10:
- **Read every tile once. Footprint = BCGS cell ∩ valid data** (non-BCGS tiles: valid data ∩
  raster extent). Files with overviews are read at the coarsest overview; the rest downloaded whole.
- **`file:checksum` (sha256 multihash) + `file:size` on the `dem` asset** of every downloaded tile.
- **`raster:bands[0].statistics.valid_percent` on the `dem` asset.**
- **`datetime_unknown` → `lidarbc:datetime_unknown`** (added to crate#23's `lidarbc` list).
- Earlier: stage only, publish on the user's word; `proj:geometry` = footprint, `proj:bbox` stays
  the raster extent; #55 (`lidarbc:delivery`) folded into the same rewrite.
- Queryables are pgstac-side: NewGraphEnvironment/stacs#8, not this PR.

Review findings and dispositions: `review-1.md`.

## Phase 1: Measure before building
- [x] Throughput from this machine (morning sample, later corrected for albers contamination)
- [x] Enumerate tile-name forms in `data/urls_list.txt` with counts
- [x] Validate cell-from-id against cached bounds (3,000) and pixels (144); numbering is SW-origin
- [x] Note undeclared −3.4e38 and all-nodata tiles in the sample (0 and 0 of 144)
- [x] Escalate: edge heuristic falsified → user chose full read (2026-10-10)
- [ ] `research/footprints.md`: what is known (numbering, overlap pad, gap rates, cost), with producers

## Phase 2: Footprint functions + tests (tests first)
- [x] `tests/test_footprint.py`: cell from every id form (underscore, concatenated, `bcts_`), non-BCGS ids → None; full-population check that every cached-bounds BCGS tile's cell lies inside its raster bounds (zero I/O)
- [x] Synthetic rasters: full cell → "cell" (no WKT); corner gap; two islands → MultiPolygon; specks dropped and small holes filled; undeclared −3.4e38 and out-of-range values invalid; all-nodata refuses
- [x] Acceptance asserted in tests: valid, CCW exterior rings, vertex cap, within cell (BCGS) or raster extent, bbox == bounds of the rounded geometry, data outside the footprint ≤ tolerance (outward bias)
- [x] `scripts/footprint.py`: `bcgs_cell()`, `footprint_from_mask()`, `footprint_read()` (download-whole or coarsest overview by overview presence, sha256 + size when downloaded, `CPL_VSIL_CURL_NON_CACHED` on retry)
- [x] Restore-the-bug check that each guard fires

## Phase 3: Cache + extraction
- [ ] `scripts/footprint_extract.py`: `--incremental` = `urls_list − cache`, `--urls-file`, `--limit`, `--workers`, `--max-minutes`, `--min-free-gb`; streamed temp files in one dir cleared at start/exit; rows appended as they finish (resumable); errors file + rate gate; writes `data/urls_footprint_changed.txt` (computed this run, not new this month)
- [ ] `data/footprints.csv` columns: url, method, footprint_wkt (EPSG:4326, empty = the cell), valid_percent, checksum, size; `https://` guard and `test_urls_scheme.py` coverage
- [ ] Full local run with logging (`logs/`), peak disk logged; cache committed; counts by method recorded in findings

## Phase 4: New-item path
- [ ] One helper applies a footprint row to an item (geometry, bbox, proj:geometry in the item's CRS incl. `proj:wkt2`, ∩ proj:bbox; file + raster fields on `dem`; extension URLs); used by item_create's cache and rio_stac branches and `item_reprocess.py`
- [ ] Missing row → geometry left as built, counted and logged; the URL is picked up by the next extract and rebuilt via `urls_footprint_changed.txt`
- [ ] `update.yml`: footprint step (time-boxed) before item_create; footprint-changed rebuild beside the pairing rebuild; `build_safe.sh` and README Quick Start get the step
- [ ] Tests for both item paths and the helper

## Phase 5: `lidarbc:` fields (#55 + rename) — schema waits on crate#23
- [ ] Tests first: delivery from the href (`gdwuts/<block>/<sheet>/<year>/`), omitted for albers; counts re-derived from `data/urls_list.txt` (100,171 / 2,245); `datetime_unknown` written only as `lidarbc:datetime_unknown`
- [ ] One helper writes `lidarbc:delivery`, `lidarbc:datetime_unknown` and the `lidarbc` schema URL, used by both item paths
- [ ] Collection: one `related` link to stac-pointcloud-bc (`collection_patch.py`)
- [ ] Field audit run on every staged set (each non-albers item has `lidarbc:delivery`; no unprefixed `datetime_unknown`; declared extension URLs present)
- [ ] Confirm crate#23's `lidarbc` schema answers 200 after redirects, `$id` equals its URL, and declares both fields

## Phase 6: Rewrite published items (one pass)
- [ ] `scripts/footprint_apply.py` on `item_rewrite` (manifest `data/footprint_done.txt`, own migration name): footprint fields, `lidarbc:` fields, rename; the 44 sourceless items get only the lidarbc edits, counted apart; tests mirroring `test_item_backfill.py`
- [ ] `update.yml` dispatch input `footprint`, wired at every `backfill || rename` site incl. the conflicting-inputs guard and the manifest discard; full validation to scratch
- [ ] Local dry run on a sample of published items: `stacs audit`, field audit, `item_validate.py`
- [ ] Before the dispatch: rebase on main, re-extract `urls_list − cache`, confirm cache covers every published URL
- [ ] Publish (dispatch after merge, then `stacs register --mode drift` + `stacs verify`) — **only on the user's word**

## Phase 7: Docs
- [ ] README (remove #2 from "future work"; geometry, checksum, valid_percent, `lidarbc:` fields, QGIS bbox note), scripts/README, CLAUDE.md data tree, NEWS Unreleased; PR closes #2, #55 (queryables to stacs#8)

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean (each commit, or once over the branch with `/code-check branch`)
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
