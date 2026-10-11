# LidarBC DEM tile footprints

**Verified:** 2026-10-10 · **Issues:** #2, #55; spawned crate#26, stacs#8 · **Produced by:**
`scripts/footprint.py`, `scripts/footprint_extract.py`; Phase 1 probes and counts in
`planning/archive/2026-10-issue-2-*/findings.md`; population checks in `tests/test_footprint.py`.

What a DEM tile on the BC objectstore (`nrs.objectstore.gov.bc.ca/gdwuts`) covers, how to compute
it, and what it costs.

## Tile ids name a BCGS 1:2,500 cell, numbered from the south

97,723 of 102,416 DEM URLs name a BC Geographic System 1:2,500 cell, in three spellings:
`bc_092g036_1_1_1_…`, `bc_092g036111_…` (2016 deliveries) and `bcts_092g036_1_1_1_…`.

- NTS block `NN-M`: east edge at -(48 + 8·NN)°, south edge at 40 + 4·M°.
- Letters a–p: 1° × 2° blocks, boustrophedon from the SE (a–d westward, e–h eastward, …).
- **1:20k numbers 001–100 count from the SW corner of the letter block**, eastward by rows going
  north. **Quadrants 1, 2 are the south pair** (1 SW, 2 SE, 3 NW, 4 NE), at each of the 10k, 5k
  and 2.5k levels.

The NW-origin assumption mirrors every cell about its block's mid-latitude: 8 of 8 sampled cells
were off by exactly that reflection. With the SW origin, every one of the 44,197 BCGS tiles whose
raster bounds are cached (`data/stac_geotiff_checks.csv`) lies inside its raster, with a worst pad
of −1.25 m on the flush 2 m tiles. The exceptions are 2 tiles that declare the wrong UTM zone
(below).

The other 4,693 do not name a cell. 2,448 carry a 1:20k sheet number with no quadrants, and these
are **not** full 1:20k cells: `bc_082e002_xli1m_utm11_2018` is a 1,875 × 1,454 m raster. The other
2,245 are `albers10k2m/_completed_dem/dem_NNN_NNN`, 10 km squares in EPSG:3005.

## What a delivered raster covers

- A full tile is the UTM rectangle around its cell, plus **~22 m (Hausdorff) of data beyond the
  cell** on every side, overlapping its neighbours. Footprint / cell area is 1.038–1.044 (n=112).
  The cell fills 0.88–0.97 of the rectangle (median 0.925, n=3,000).
- **Gaps inside the cell are common**: >0.1% of the cell lacked data in 21 of 112 interior tiles
  (19%) and 8 of 32 delivery-edge tiles (25%), up to 48% of the cell. Whether a tile sits at the
  edge of its delivery does not predict a gap. That ruled out "read only the edge tiles": it would
  have found 8 of the 29.
- Some deliveries crop rasters to their data. 2017 `082e` tiles start 88 m inside their cell, or are
  a 374 px sliver, so "the cell lies inside the raster" is not a valid integrity test. "The cell
  touches the raster" is.
- No undeclared −3.4e38 and no all-nodata tile in 144 downloaded. The validity mask still treats
  nodata, non-finite values and anything outside −100..5000 as no data.

## Two tiles declare the wrong UTM zone

`bc_082e003_3_2_2_xli1m_utm11_20240721_20240726.tif` and
`bc_082f010_1_1_1_xli1m_utm11_20241008_20250813.tif` are named `utm11` and carry UTM-11
coordinates, but declare EPSG:6657 (NAD83(CSRS) / UTM zone **14**N + CGVD2013). Their published
items sit in Manitoba (bbox −101.5, 49.05). The footprint code refuses them (`crs_mismatch`), and
they are not repaired here.

## Reading a tile

- 1:2,500 tiles are **strip-organised with no overviews** (`Block=<width>x1`): a remote read issues a
  range request per row, which is where the issue's "a minute per raster" came from. Downloaded
  whole they are 5.9 MB on average (median 6.1) and take ~1.5 s.
- `082f037`-type mapsheet COGs (up to ~340 MB) have internal overviews. The `albers10k2m` tiles
  (100 MB, strip) have an **external `.ovr` sidecar**, which `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
  hides: with it a tile took a 23.7 s download, without it a 3.5 s overview read.
- Throughput from one machine to the objectstore: 7.8 MB/s with 16 workers on 1:2,500 tiles.
  Throughput barely scales with workers (2.1 MB/s at 8 → 3.4 MB/s at 32 on a mixed sample), and
  GitHub served the same machine 7.2 MB/s on a single stream. A full read of the 94,808 non-COG
  1:2,500 tiles is 559 GB, ≈ 20 h at 7.8 MB/s. The first estimate, 1.4 TB / 5 days, came from a
  sample that included the 100 MB albers tiles. A GitHub runner's throughput is unmeasured.
