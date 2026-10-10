# Findings — tif "footprints" are actually bounding boxes (#2)

## Issue context

https://stactools.readthedocs.io/en/stable/api.html#stactools.core.utils.raster_footprint.RasterFootprint.footprint

currently our "footprints" are actually bounding boxes which I am uncertain whether we are reading from metadata or calculating with our `stac-item-create` script in https://github.com/NewGraphEnvironment/stac_dem_bc/blob/main/stac_create_item.qmd

we actually would prefer to exclude the nodata values

this actually does not matter in QGIS currently though as it appears to be using "bounding boxes" as the "footprint" (vs the "geometry")

tested by altering the geometry of a couple of json manually using above function (takes almost a minute per raster with code in the file linked here!!!)

Changing the "geometry" did not change the "footprint" in QGIS (yet).  

<img width="1811" height="1078" alt="Image" src="https://github.com/user-attachments/assets/4893c85b-2a64-44f5-9cfc-be0ffd681af4" />

<img width="1811" height="1078" alt="Image" src="https://github.com/user-attachments/assets/5130a069-dbca-4e0d-9093-a5830ea49909" />

this is actually a diff area but you get the idea
<img width="793" height="494" alt="Image" src="https://github.com/user-attachments/assets/4e37a391-d7a4-4ecf-a4b9-91c85a759c09" />

<img width="1811" height="1078" alt="Image" src="https://github.com/user-attachments/assets/4f5b42d7-8161-4782-9abe-bc2febf5564c" />

doesn't matter if we update the native crs properties either...

<img width="1811" height="1078" alt="Image" src="https://github.com/user-attachments/assets/8542ca5b-339e-4af5-be91-802376db24ac" />


If the cogs have overviews it would make getting the footprints remotely much faster




## Probes before the baseline

### 2026-10-09 — tile populations (gdalinfo, 3 small tiles downloaded)
- `bc_082f037_l1m_utm11_20180830.tif`: 338 MB, 14670x11275, COG, DEFLATE, 512x512 blocks, overviews 7335x5638 and 3668x2819, NoData -32767.
- `bc_094j064_4_1_2_xli1m_utm10_20240915_20240930.tif`: 5.7 MB, 1485x1426, LZW, `Block=1485x1` (strip), no overviews, NoData -32767.
- Valid percent (`gdalinfo -stats`) on three small tiles: 99.41, 98.85, 99.10. Download 1.4–1.6 s each, serial.
- `data/stac_geotiff_checks.csv`: 98,051 is_cog=False, 4,409 is_cog=True.

### 2026-10-10 — throughput from this machine (96 random non-COG tiles, urllib, ThreadPoolExecutor)
| workers | files | MB | wall s | MB/s | mean MB |
|---|---|---|---|---|---|
| 8 | 32 | 290 | 135.3 | 2.1 | 9.0 |
| 32 | 64 | 1118 | 329.9 | 3.4 | 17.5 |

GitHub release download, one stream: 7.2 MB/s. Full read ≈ 98k × ~15 MB ≈ 1.4 TB ≈ 5 days → rejected.
Design moved to "BCGS cell from tile id + pixel-read delivery-edge tiles" (user, 2026-10-10).

### 2026-10-10 — custom fields
Published item properties: `datetime`, `proj:bbox`, `proj:epsg`, `proj:geometry`, `proj:shape`,
`proj:transform`; extensions: projection v1.1.0 only. No `nge:` fields, so crate#23's rename has
nothing to do here; #55 adds `lidarbc:delivery`, schema in crate#23.

## Errors Encountered

| Error | Resolution |
|-------|------------|

## Phase 1 measurements (2026-10-10)

Scripts: scratch probes `cell_probe.py`, `edge_probe.py`, `pix_probe.py` (to be committed as
`scripts/footprint_measure.py` with the functions they prototype).

### Tile-name forms in `data/urls_list.txt` (102,416 URLs)
| form | example | count | COG |
|---|---|---|---|
| BCGS 1:2,500 (`bc_`/`bcts_`, quadrants `_q_q_q_` or concatenated `qqq`) | `bc_094o003_2_2_2_xli1m_…`, `bc_093l031242_xl2m_…`, `bcts_092l005_1_1_2_x_2012_dem` | 97,723 | 2,915 True / 94,808 False |
| `bc_<20k sheet>_` without quadrants | `bc_082e002_xli1m_utm11_2018` (1875x1454 px at 1 m: NOT a full 1:20k cell) | 2,448 | 1,494 True / 954 False |
| `albers10k2m/_completed_dem/dem_NNN_NNN` | 5000x5000 px, 100 MB, EPSG:3005, strip, overviews to 157x157 | 2,245 | False |

### BCGS cell from tile id — numbering verified
NTS block NN-M: east lon = -(48+8*NN), south lat = 40+4*M; letters a–p 1°x2°, boustrophedon from
the SE (a–d westward, e–h eastward, …). **1:20k numbers 001–100 count from the SW corner, eastward
by rows going north; quadrants 1,2 are the SOUTH pair (1=SW, 2=SE, 3=NW, 4=NE).** First attempt
assumed NW origin: every cell landed mirrored about the letter's mid-latitude (8/8 centres off by
exactly that reflection). After the fix: 3,000 random cached-bounds tiles, **0** cells extend outside
their raster's bounds; cell/raster-rectangle area median 0.925 (p5 0.894, p95 0.956).

### Pixel footprint vs cell (144 random 1:2,500 tiles, 1 m, downloaded whole)
- Full tiles: valid data covers the cell and extends ~22 m (Hausdorff) beyond it — fp/cell area
  median 1.041 (1.038–1.044, n=112). Tiles overlap their neighbours by a constant buffer.
- **Gaps inside the cell (>0.1% of cell lacking data): 21 of 112 interior tiles (19%), 8 of 32 edge
  tiles (25%).** Interior gaps reach 36%, 48%, 48% of the cell. **The delivery-edge heuristic does not
  predict gaps** — it would read 8 of the 29 gap tiles. Approved design premise falsified.
- Undeclared -3.4e38: 0 of 144. All-nodata: 0 of 144.
- Edge tiles (8-neighbour test): sheet+year grouping 26,305 (27%); year grouping 23,490 (24%).
  484 tiles share a cell+year with another tile (re-deliveries / overlapping zones).

### Throughput, revised
The 2026-10-10 morning sample (2.1–3.4 MB/s, 9–17.5 MB/file) drew from ALL non-COG rows, so it
included 100 MB albers tiles — that inflated the ~1.4 TB / ~5 day full-read estimate. 1:2,500 tiles
alone: mean 5.9 MB (median 6.1). 120 tiles at 16 workers: 714 MB in 92 s = **7.8 MB/s** (22:46 UTC).
Full read of the 94,808 non-COG 1:2,500 tiles: **559 GB ≈ 20 h at 7.8 MB/s (46 h at 3.4 MB/s)**.

## Phase 2 (2026-10-10)

- **Two tiles declare the wrong UTM zone.** `bc_082e003_3_2_2_xli1m_utm11_20240721_20240726.tif`
  and `bc_082f010_1_1_1_xli1m_utm11_20241008_20250813.tif` are named `utm11`, carry UTM-11
  coordinates, and declare EPSG:6657 (NAD83(CSRS) / UTM zone **14**N + CGVD2013). Their published
  items' bbox is in Manitoba (-101.5, 49.05). Found by the full-population cell check (44,197
  cached BCGS tiles): every other cell lies inside its raster, worst -1.25 m (flush 2 m tiles).
  `footprint_from_mask` refuses such tiles (`FootprintCrsMismatch`); not repaired here — an
  upstream data defect to surface.
- albers10k2m overviews are an external `.ovr` sidecar: `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
  hid them and the 100 MB tile was downloaded whole (23.7 s); without it, overview read in 3.5 s.
- Real reads, one per path: gap tile `092f095_1_1_3` → MultiPolygon, 97 vertices, 87.6% valid;
  `082f037` 338 MB COG → overview, 99.34%; `082e002` no-quadrant → download, 93.3%;
  `093l078313` 2 m concatenated id → download, 70.0%. Temp dir empty afterwards.
- Mutation table (`test_footprint.py`, scratch copy): NW-origin quadrants, flipped 1:20k rows,
  no outward buffer, no hygiene, wrong orientation, no vertex cap, no CRS check, no range clamp
  all go RED. Removing the empty-mask guard stays GREEN because `_hygiene` raises the same
  `FootprintEmpty` — redundant, not untested. The first fixtures could not reach two failures
  (pixel-aligned straight edge never simplifies inward; speck sat in the pad outside the cell);
  replaced with a ragged diagonal edge and in-cell speck/hole.

| Error | Resolution |
|-------|------------|
| BCGS regex `bcts?_` never matched `bc_` (needs `bct`) | `bc(?:ts)?_` |
| Every cell mirrored about the letter block's mid-latitude | 1:20k rows and quadrants count from the south |
| Population test: cell 1.3e6 m outside raster | Two tiles declare UTM 14; refused, reported |
