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
