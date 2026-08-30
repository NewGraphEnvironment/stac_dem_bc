# Findings — Add `dsm` as a second asset on each item, paired by tile id and acquisition date (#31)

## Planning-phase measurements (2026-08-28)

### The 60,126 figure is pgstac, not the catalog

| source | count | how measured |
|---|---|---|
| `s3://stac-dem-bc/collection.json` item links | **98,040** | `curl` + count `rel == "item"` links |
| `data/urls_list.txt` (local cache) | 97,996 | `wc -l` |
| `data/stac_item_validation.csv` rows | 98,048 | `wc -l` |
| `images.a11s.one/collections/stac-dem-bc` | 60,126 | #29 inventory comment |
| DEM `.tif` in the bucket | 100,171 | #29 inventory comment |

pgstac registration is a manual step on geoserv (#27) and has not been re-run since
the July 2026 catch-up. So the catalog is ~2.2k behind the bucket, not ~40k; the
40k-looking gap is a registration lag.

### Live convention probe

Direct `list-type=2` listings against `nrs.objectstore.gov.bc.ca/gdwuts`:

```
082/082f/2017/dsm/  bc_082f006_1_xno_p75_utm11_180827_dsm.laz   (laz only — no tif)
082/082f/2022/dsm/  bc_082f005_xli1m_utm11_2022.tif             (identical basename)
094/094o/2026/dsm/  bc_094o056_2_1_4_xli1m_utm10_20260506_20260506_dsm.tif
```

Two things this confirms beyond #29:

- Listings return `_$folder$` marker keys that must be filtered before counting.
- The `.laz` names in a laz-only `dsm/` directory follow a *different* naming scheme
  entirely (`xno_p75`, `180827`) from the DEM (`xli1m`, `2017`), so a `.tif`-only
  filter cannot accidentally pair against them.

### Existing plumbing relevant to the change

- `scripts/urls_fetch.R` / `scripts/detect_changes.R` list with
  `ngr::ngr_s3_keys_get(pattern = c("dem", "*.tif"))` — DSM keys are never listed
  today. `data/urls_list.txt` holds zero `/dsm/` keys (verified).
- `scripts/stac_utils.py::item_create_from_cache()` builds items with **no remote
  reads** from `data/stac_geotiff_checks.csv`, which already carries full metadata
  for all ~98k DEM URLs. A whole-catalog rebuild to attach the new asset is
  therefore a local, network-free operation.
- `scripts/item_create.py::process_item()` has two paths: the cache path and a
  `rio_stac` remote fallback for cache misses. Both need the `dsm` asset.
- `.github/workflows/update.yml` **fetches** `collection.json` from S3 rather than
  regenerating it, so collection-level metadata (#30 providers/keywords) needs an
  idempotent patch step, not just a change to `collection_create.py`.
- `scripts/s3_sync-ci.sh` never uses `--delete` (S3 is the only complete copy);
  `scripts/s3_sync.R` is the laptop full-catalog tool that does.
- No Python test suite exists yet — `tests/` and pytest are new here.

## Issue context

## Problem

DSM rasters sit beside the DEMs in the same objectstore and nothing indexes
them. Measured in #29: **95,889 DSM `.tif`** against **100,171 DEM `.tif`**,
with the two 1:1 from 2023 onward. The collection currently holds 60,126 items,
so it is also about 40% behind the bucket.

A DEM and its DSM come from the same flight over the same footprint at the same
time. In STAC terms that is **one item with two assets**, not two items.

## Pairing — the only hard part

There is no manifest, so the relationship is inferred from filenames, and the
convention is not uniform. From #29, over 97,271 DEM tifs in mapsheet-years
carrying both products:

| rule | files | share |
|---|---|---|
| `<dem_basename>` + `_dsm.tif` | 95,768 | **98.5%** |
| identical basename | 117 | 0.1% — four mapsheet-years, all 2022, block 082 |
| neither | 1,386 | 1.4% — see below |

The 1.4% are **not** naming failures. **11 mapsheet-years have a `dsm/`
directory containing only `.laz` and no raster**, stranding 1,211 DEM tifs.
Those deliveries published a surface model as point cloud only.

Two properties make this safe to implement, both measured:

- **No tile carries both conventions.** Zero cases where a suffixed and an
  identical name both exist.
- **No directory mixes conventions.** A `dsm/` directory is entirely one form.

### Implement it as an assertion, not a lookup

Rather than branching on the convention, parse **tile id and acquisition date**
out of both filenames and match on those. Then check the resulting name against
the known conventions and **record which one matched**.

```
for each mapsheet-year:
    dem_keys = list(dem/)          # list containers; never construct a sibling path
    dsm_keys = list(dsm/)

    if no .tif in dsm_keys:
        record mapsheet-year as "no raster DSM"   # declared gap, not a drop
        continue

    for each dem .tif:
        find dsm .tif with the same (tile_id, acquisition_date)
        if found:     attach as the `dsm` asset, record convention matched
        if not found: emit an UNPAIRED row -- never drop silently
```

The point of the inversion: a convention we have not seen — a future delivery
using `_surface`, or a different case — surfaces as an **unpaired tile in a
report** rather than as a tile quietly missing its DSM. The failure mode we
already paid for was a probe that returned nothing and read as "the product does
not exist".

## Two existing warts worth a decision

Current items look like:

```
id     : 094-094o-2026-dem-bc_094o056_2_1_4_xli1m_utm10_20260506_20260506
assets : { "image": { href, roles, type } }
```

1. **The item id embeds `-dem-`.** Once an item carries a DSM too, the id
   describes only one of its assets. Ids are identifiers rather than
   descriptions, so leaving them is defensible — but it should be a decision,
   not an oversight.
2. **The asset key is `image`, not `dem`.** Adding a `dsm` key beside `image`
   reads oddly, and `image` no longer disambiguates. Renaming it is a breaking
   change for any consumer doing `assets["image"]`.

Suggested: add `dsm` now, leave `image` as-is, and revisit both at the point a
collection rename happens so consumers absorb one break rather than two.
Document that `image` is the bare-earth DEM in the meantime.

## Scope

- [ ] Pair DEM to DSM by tile id + acquisition date; record the convention matched
- [ ] Attach `dsm` as a second asset on each paired item
- [ ] Emit a report: paired count by convention, unpaired tiles, mapsheet-years
      with no raster DSM
- [ ] Catch the collection up from 60,126 to 100,171 in the same pass
- [ ] Fix `providers` while the metadata is open (#30)

## Guard

The pairing decides whether a tile gets an asset or gets dropped, so it must not
fail toward "no DSM here". Assert against three known answers from #29:

1. a mapsheet-year using the `_dsm` suffix — expect full pairing
2. one of the four 2022 identical-basename mapsheet-years — expect full pairing
3. one of the 11 `.laz`-only mapsheet-years — expect **zero** pairs and an
   explicit "no raster DSM" record, not an empty success

And confirm a listing error is reported as an error rather than as an empty
product set. A guard that reads the same for "nothing there" and "the call
failed" is indistinguishable from a working one until it drops real coverage.

Blocked-by: #29 (done — see the inventory comment there)
Relates to #27, #30

