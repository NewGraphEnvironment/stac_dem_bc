# Task: Add `dsm` as a second asset on each item, paired by tile id and acquisition date (#31)

## Problem

DSM rasters sit beside the DEMs in the same objectstore and nothing indexes them.
Measured in #29: **95,889 DSM `.tif`** against **100,171 DEM `.tif`**, with the two
1:1 from 2023 onward. A DEM and its DSM come from the same flight over the same
footprint at the same time. In STAC terms that is **one item with two assets**, not
two items.

The hard part is pairing: there is no manifest, and the filename convention is not
uniform. #29 established it is deterministic but bimodal, and that 11 mapsheet-years
publish the surface model as point cloud only. The failure mode already paid for was
a probe that returned nothing and read as "the product does not exist" — so the
pairing must be written as an **assertion** (parse tile id + acquisition date, then
record which convention the resulting name matched) rather than a lookup, and an
unknown convention must surface as an unpaired row in a report, never a silent drop.

## Correction to the issue's premise (measured during planning)

The issue says the collection is "about 40% behind the bucket" at 60,126 items.
That number is not the catalog:

| source | count |
|---|---|
| `s3://stac-dem-bc/collection.json` item links | **98,040** |
| `data/urls_list.txt` (local cache) | 97,996 |
| `images.a11s.one/collections/stac-dem-bc` (pgstac) | 60,126 |
| DEM `.tif` in the bucket (#29) | 100,171 |

60,126 is the **pgstac registration** count, and registration is a manual step that
has not been re-run since the July catch-up (#27's scope). The catalog itself is
~2,175 DEM tiles behind the bucket, not ~40,000. This plan reconciles that real gap
and posts the correction; the pgstac catch-up stays with #27.

## Decisions taken

- **DSM media type is inherited from the paired DEM's `is_cog`**, verified on a
  stratified sample rather than by a ~15–20 h remote pass over 96k files (which
  cannot fit the 330-minute runner). Same flight, same delivery, same processing.
- **Add `dsm`; leave `image` and the `-dem-` item id as they are.** Non-breaking;
  revisit both at a future collection rename so consumers absorb one break.

## Confirmed live during planning

```
082/082f/2017/dsm/  ->  bc_082f006_1_xno_p75_utm11_180827_dsm.laz   (laz only, no tif)
082/082f/2022/dsm/  ->  bc_082f005_xli1m_utm11_2022.tif             (identical basename)
094/094o/2026/dsm/  ->  bc_094o056_2_1_4_xli1m_utm10_20260506_20260506_dsm.tif
```

Listings also return `_$folder$` marker keys, which must be filtered.

---

## Phase 1: Reconcile the DEM listing gap

- [x] Measure the 97,996 vs 100,171 shortfall: full-bucket listing of `.tif` under
      `dem/` prefixes vs `data/urls_list.txt`; classify the difference (the
      `pattern = c("dem", "*.tif")` filter in `scripts/urls_fetch.R`, `_$folder$`
      markers, parenthesized filenames per #8, or genuinely new arrivals)
- [x] Fix the listing in `scripts/urls_fetch.R` and `scripts/detect_changes.R` if
      the filter is the cause; leave the plausibility guard intact
- [x] Post a comment on #31 (xref #29, #27) recording that 60,126 is the pgstac
      count and the catalog holds 98,040 — the real gap is **4,420** new DEM tiles
      (issue comment 5455765690)

## Phase 2: List DSM keys

- [x] Add DSM listing (`ngr::ngr_s3_keys_get(pattern = c("dsm", "*.tif"))`) writing
      `data/urls_dsm.txt`; filter `_$folder$` markers and non-`.tif` keys
- [x] Guard: assign, test exit status, **then** test emptiness — a failed listing
      must exit non-zero, never write an empty file that reads as "no DSM here"
      (mirrors the plausibility guard in `detect_changes.R`)

## Phase 3: Pairing contract — tests first

- [x] Scaffold `tests/` + pytest (add to `environment.yml` and the workflow's
      `uv pip install`); repo currently has no Python test suite
- [x] `tests/test_dsm_pair.py` — failing tests encoding #29's three known answers,
      run against committed fixture listings (no network):
      1. a `_dsm`-suffix mapsheet-year (`094/094o/2026`) → full pairing,
         `convention == "suffix"`
      2. an identical-basename 2022 mapsheet-year (`082/082f/2022`) → full pairing,
         `convention == "identical"`
      3. a `.laz`-only mapsheet-year (`082/082f/2017`) → **zero** pairs and an
         explicit "no raster DSM" record, not an empty success
- [x] Two more tests for the failure modes the issue names:
      4. a synthetic unknown convention (`..._surface.tif`) → emitted as UNPAIRED
         with the DEM named, never dropped
      5. a listing error → reported as an error, distinguishable from an empty
         product set

## Phase 4: Pairing implementation + report

- [x] `tile_key_parse()` in `scripts/stac_utils.py` — parse a key into
      `(mapsheet_year, tile_id, utm, date_tokens, suffix)`; return `None` (not a
      guess) on an unparseable name
- [x] `scripts/dsm_pair.py` — match DEM to DSM on `(mapsheet_year, tile_id,
      date_tokens)`, then assert the matched name against the known conventions and
      **record which one matched**
- [x] Write `data/dem_dsm_pairs.csv` (keys relative to `PATH_S3`, not full URLs —
      halves a ~96k-row file) and `data/dsm_pairing_report.md`: paired count by
      convention, unpaired tiles, mapsheet-years with no raster DSM
- [x] Reconcile the report against #29's numbers (95,768 suffix / 117 identical /
      1,211 stranded across 11 `.laz`-only mapsheet-years) and explain any drift

## Phase 5: Verify the inherited-media-type assumption

- [x] Stratified sample (~500 DSM tiles across both conventions and all years):
      remote `geotiff_extract_metadata`, assert `is_cog` matches the paired DEM and
      that bounds/shape agree
- [x] Record agreement rates in `data/dsm_pairing_report.md`; a disagreement above a
      stated threshold fails the phase rather than being noted and shipped

## Phase 6: Attach the `dsm` asset

- [x] Second asset keyed `dsm`, `roles: ["data"]` — attached in `item_create.py`
      *after* the item is built rather than inside `item_create_from_cache()`, so
      one piece of code covers both the cached and the `rio_stac` branches
- [x] `scripts/item_create.py` — load the pairs lookup, pass it through
      `process_item()`; cover the `rio_stac` cache-miss fallback path too
- [ ] **Backfill the ~98k existing items — NOT DONE, needs an explicit go-ahead.**
      The plan assumed this was network-free from `data/stac_geotiff_checks.csv`.
      It is not: **60,324 of 100,345 cache rows predate spatial-metadata caching**
      and carry NaN for bounds/shape/epsg, so a rebuild falls through to the
      `rio_stac` remote path for all of them — roughly a 10 h local run, and it
      publishes to production S3.
      Cheaper alternative worth costing first: fetch the existing item JSONs from
      S3 (they already carry `proj:*`) and add the `dsm` asset to each, ~98k small
      GETs rather than 60k GeoTIFF header reads plus COG validation.
- [ ] Build the 4,420 new DEM tiles through the normal incremental path — same
      production-publish gate as the backfill

## Phase 7: `providers` and `keywords` (#30)

- [x] Add `providers` (Province of BC — producer/licensor/host; New Graph
      Environment — processor) and `keywords` to `scripts/collection_create.py`
- [x] `scripts/collection_patch.py` — idempotent patch applying the same metadata to
      an existing `collection.json`. Needed because `update.yml` **fetches**
      collection.json from S3 rather than regenerating it, so `collection_create.py`
      alone would never reach the live collection
- [x] Note in the collection description that `image` is the bare-earth DEM
- [x] Raise the CC-BY-4.0 vs BC OGL question on #30 — flagged, not changed
      (issue comment 5455765978)

## Phase 8: Wire in and document

- [x] Add the DSM listing + pairing steps to `.github/workflows/update.yml`, ahead
      of item creation; keep the detect-step exit contract (0/1/2) intact
- [x] Update `scripts/README.md`, the data-tracking section of `CLAUDE.md`, and the
      README roadmap

## Validation

- [x] `pytest tests/` green, including the three known answers and both failure modes
- [ ] `/code-check` clean on each commit
- [ ] Sample verification (Phase 5) meets its stated threshold
- [x] A rebuilt item validates via `scripts/item_validate.py` and carries both
      `image` and `dsm` assets
- [ ] Live spot-check: fetch a rebuilt item from S3 and from `images.a11s.one`
- [ ] PWF checkboxes match landed work; `/planning-archive` on completion

## Critical files

| file | change |
|---|---|
| `scripts/stac_utils.py` | `tile_key_parse()`; `dsm` asset in `item_create_from_cache()` |
| `scripts/dsm_pair.py` | **new** — pairing + report |
| `scripts/collection_patch.py` | **new** — idempotent providers/keywords patch |
| `scripts/item_create.py` | pairs lookup through `process_item()`, both paths |
| `scripts/urls_fetch.R`, `scripts/detect_changes.R` | DEM listing fix + DSM listing |
| `scripts/collection_create.py` | providers, keywords, description |
| `tests/test_dsm_pair.py` | **new** — pairing contract |
| `.github/workflows/update.yml` | DSM listing + pairing steps, pytest dep |
| `data/urls_dsm.txt`, `data/dem_dsm_pairs.csv`, `data/dsm_pairing_report.md` | **new** artifacts |
