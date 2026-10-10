# Task: tif "footprints" are actually bounding boxes - Recalculate footprints to exclude `no data` values (#2)

Currently our "footprints" are actually bounding boxes: every item's `geometry` is the WGS84 box of
the raster extent, no-data included. We would prefer to exclude the nodata values. Folds in #55
(`lidarbc:delivery`), whose "next full rebuild" is this issue's rewrite.

## Context (from plan approval, 2026-10-10)

Every item's `geometry` is `box(transform_bounds(raster extent))` — `scripts/stac_utils.py:338-347`
(`item_create_from_cache`). So geometry == bbox, nodata included, and `/search?intersects=` returns
tiles whose nodata an AOI falls in. Neighbouring tiles' boxes overlap.

Probes (read-only):
- 2026-10-09: two populations. Small BCGS 1:2,500 tiles (`bc_094j064_4_1_2_…`): Float32, **strip
  layout, no overviews**. Mapsheet tiles (`bc_082f037_…`): 338 MB **COGs with overviews**. Cache:
  98,051 non-COG, 4,409 COG. Three small tiles were 98.9–99.4% valid: the raster is the UTM
  rectangle around a lat/lon grid cell, so the nodata is mostly corner slivers.
- 2026-10-10, throughput from this machine: 96 random small tiles, **2.1 MB/s at 8 workers, 3.4 MB/s
  at 32**, mean file ~15 MB (9–17.5 per batch). GitHub serves this machine 7.2 MB/s on one stream,
  so the cap is at least partly the objectstore. Full read of every small tile ≈ **1.4 TB ≈ 5 days**:
  not workable, and a typical 8k-file month (~120 GB) would not fit the runner either.

**Design decided 2026-10-10: grid cell + read edges.** A small tile's footprint is its BCGS 1:2,500
cell, computed from the tile id with no download. Pixels are read only where real gaps are likely:
tiles at the edge of their delivery (a neighbouring cell is missing from the same mapsheet-year), and
the mapsheet COGs at their coarsest overview. Accepted trade-off: a gap inside a cell whose
neighbours are all delivered goes undetected. **No `file:checksum`** — it needs a full read
(declined on cost, 2026-10-10).

**Folded in: #55 (`lidarbc:delivery`), decided 2026-10-10.** #2's rewrite of all 102k items is the
"next full rebuild" #55 waits for, so one rewrite and one re-registration carries both. Its schema is
crate#23 (expected very soon); pystac fetches it at validation, so the #55 writer and the rewrite wait
for it. No other renaming applies: published items carry only `datetime` + `proj:*`, no `nge:`
fields (checked 2026-10-10). Footprints use standard fields only (`geometry`, `bbox`,
`proj:geometry`); `method`/`valid_fraction` stay in the cache, never on items.

Gate decisions: stage only, publish on the user's word; `proj:geometry` = footprint, `proj:bbox`
stays the raster extent (needed with `proj:shape`/`proj:transform`).

QGIS's STAC plugin draws `bbox`, so QGIS will look the same; the change shows in API spatial queries
and any client drawing `geometry`.

## Approach

- `bcgs_cell_from_tile_id()`: parse NTS 1:250k + BCGS 1:20k number + 10k/5k/2.5k quadrant digits
  → lon/lat cell polygon (NAD83 ≈ 4326; cell edges are parallels/meridians, straight in 4326).
- Edge test: from `data/urls_list.txt` alone, a tile is an edge tile if any of its 8 neighbour cells
  is absent from the same `<block>/<sheet>/<year>` delivery. Monotone month to month: a pixel-read
  footprint stays true when neighbours arrive later.
- `footprint_from_pixels()`: rasterio + shapely (no stactools). Mask = declared nodata AND clamp
  −100..5000 (undeclared −3.4e38). Small tiles downloaded whole to a tempfile (strip layout);
  COGs at coarsest overview. `features.shapes` → union → simplify ~2 px → `transform_geom` to 4326
  (densified) → round 7 dp. Polygon or MultiPolygon; all-nodata refuses.
- Item `bbox` = footprint bounds; `proj:geometry` = footprint reprojected to native CRS (densified).
- Cache `data/footprints.csv` (`url, footprint_wkt` [4326], `method` = cell|pixels|overview,
  `valid_fraction` [pixels/overview only]), written by `scripts/footprint_extract.py`, committed
  like the other caches; monthly step before `item_create.py`.
- Tiles whose names are not BCGS 1:2,500 (e.g. `albers10k2m`, other forms Phase 1 finds) go by
  pixels/overview, or keep today's extent box if unreadable — counted, logged, never silent.
- Published items: new caller of `scripts/item_rewrite.py` (`footprint_apply.py`), editing only
  `geometry`, `bbox`, `proj:geometry` (where present) and adding `lidarbc:delivery` — rewrite, not
  rebuild, as #31/#34.


## Phase 1: Measure before building
- [x] Throughput from this machine: 3.4 MB/s at 32 workers, ~15 MB/tile → full read ~1.4 TB / ~5 days (2026-10-10); full read rejected
- [x] Enumerate tile-name forms in `data/urls_list.txt` (BCGS 1:2,500, mapsheet, `albers10k2m`, other) with counts
- [ ] Count delivery-edge tiles; project bytes and hours to pixel-read them plus the COG overviews
- [x] Validate cell-from-id: on ~100 interior tiles, pixel footprint vs computed cell (IoU, max boundary offset in m); on ~50 edge tiles, share of the cell lacking data
- [x] Note undeclared −3.4e38 and all-nodata tiles in the sample
- [ ] Write `research/footprints.md`; escalate if the cell does not match interior tiles' data or edge reads do not fit a local run

## Phase 2: Footprint functions + tests (tests first)
- [ ] `tests/test_footprint.py`: cell-from-id against hand-checked cells (incl. Phase 1 measured tiles); unparseable id refuses; edge detection on a synthetic delivery; synthetic rasters (MemoryFile) — full valid, nodata corner, undeclared −3.4e38, two islands → MultiPolygon, all-nodata refuses
- [ ] `bcgs_cell_from_tile_id()`, `tile_is_delivery_edge()`, `footprint_from_pixels()` in `stac_utils.py`
- [ ] bbox ⊇ geometry and 4326 output asserted; restore-the-bug check that each guard fires

## Phase 3: Cache + extraction script
- [ ] `scripts/footprint_extract.py` (`--incremental`, `--urls-file`, `--limit`): cell for interior BCGS tiles, pixels for edges/others, overview for COGs; writes `data/footprints.csv` atomically, resumable, errors to a file + rate gate (`item_rewrite.error_tolerable`)
- [ ] `https://` guard on the new cache (`url_scheme_check`) and `tests/test_urls_scheme.py` coverage
- [ ] Full extraction run locally with logging (`logs/`), cache committed; counts by method recorded

## Phase 4: New-item path
- [ ] `item_create_from_cache` takes optional footprint → geometry/bbox/proj:geometry; missing → extent box, counted
- [ ] Same override in item_create's rio_stac fallback and `item_reprocess.py` (one helper)
- [ ] `update.yml`: footprint step before item_create; `footprints.csv` in the cache commit-back; fallback count in the run summary
- [ ] Tests for both paths

## Phase 5: `lidarbc:delivery` (#55) — waits on crate#23
- [ ] Confirm crate#23's `lidarbc` schema: URL answers 200, `$id` equals the URL, `lidarbc:delivery` pattern `^[0-9]{3}/[0-9]{3}[a-p]/[0-9]{4}$`
- [ ] Tests first: delivery from the href's `gdwuts/<block>/<sheet>/<year>/`; omitted for `albers10k2m/_completed_dem`; counts re-derived from `data/urls_list.txt` (#55 measured 100,171 / 2,245)
- [ ] One helper writes `lidarbc:delivery` + the schema URL in `stac_extensions`, used by both item paths
- [ ] Collection: `lidarbc:delivery` in queryables, one `related` link to stac-pointcloud-bc (`collection_patch.py`)

## Phase 6: Rewrite published items (footprint + delivery, one pass)
- [ ] `scripts/footprint_apply.py` on `item_rewrite` (own manifest/migration name), one idempotent edit; tests mirroring `test_item_backfill.py`
- [ ] Dry run + `--verify` sample; `stacs audit`; `item_validate.py` (fetches the crate schema)
- [ ] Hand over publish commands (S3 sync + `stacs register --mode drift` + `stacs verify`) — **not run without the user's word**

If crate#23 is not published when Phases 1–4 are done: commit, report, leave 5–6 open rather than ship a footprint-only rewrite.

## Phase 7: Docs
- [ ] README (remove #2 from "future work"; describe geometry, its cell/pixel methods and the interior-gap limit, `lidarbc:delivery`), scripts/README, CLAUDE.md data tree, NEWS Unreleased (minor bump at merge); PR closes #2 and #55


## Validation

- [ ] Tests pass
- [ ] `/code-check` clean (each commit, or once over the branch with `/code-check branch`)
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
