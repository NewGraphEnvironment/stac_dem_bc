# Pipeline Scripts

This pipeline builds a searchable catalog of British Columbia's Digital Elevation Model (DEM) data. It takes ~58,000 GeoTIFF files hosted on the provincial objectstore, validates them, generates standardized metadata records, and registers them in a searchable catalog so anyone can find elevation data by location.

## Key Concepts

**DEM (Digital Elevation Model)** — A grid of elevation values representing the shape of the ground surface. Each pixel stores a height value (in metres). Used for slope analysis, flood modelling, watershed delineation, and terrain visualization.

**GeoTIFF** — An image file format that embeds geographic coordinate information (projection, position, pixel size) directly in the file. This means GIS software knows exactly where on Earth the image belongs without needing a separate location file.

**COG (Cloud Optimized GeoTIFF)** — A GeoTIFF organized internally so that a viewer can request just the piece it needs (e.g. a zoomed-in corner) over the internet, without downloading the whole file. The pipeline detects which source files are COGs and tags them accordingly in the catalog.

**STAC (SpatioTemporal Asset Catalog)** — A standard way to describe geographic datasets with where-and-when metadata. Think of it as a library catalog for spatial data: each file gets a JSON record describing its location, date, and download link. This makes the collection searchable — "show me all DEMs that overlap this watershed."

**S3** — Cloud file storage (Amazon-compatible). The generated catalog JSON files are uploaded here so they are accessible via URL from anywhere.

**pgstac** — A PostgreSQL database that stores STAC records and exposes them through a search API. Hosted at `images.a11s.one`, this is what allows users to search the collection by location from QGIS, a web browser, or any STAC-compatible tool.

**Validation caching** — The pipeline reads each remote GeoTIFF once to extract metadata (projection, dimensions, bounds, COG status) and saves the results to a local CSV. On subsequent runs, items are built from the cache instead of re-reading remote files. This is what makes incremental updates fast (minutes instead of hours).

## Quick Start

```bash
# Full safe build (backup, fetch, validate, create, check)
./scripts/build_safe.sh

# Or run individual steps from the project root
Rscript scripts/urls_fetch.R
python scripts/urls_check_access.py
python scripts/collection_create.py
python scripts/item_create.py
python scripts/item_validate.py
Rscript scripts/s3_sync.R
```

## Pipeline Steps

| Step | Script | What it does |
|------|--------|--------------|
| 0 | `detect_changes.R` | Compare the cached URL list against a fresh objectstore listing to find new or deleted files — this drives incremental updates |
| 1 | `urls_fetch.R` | Fetch the master list of DEM GeoTIFF URLs from the BC objectstore (~58,000 files), filtering out filenames with parentheses that fail validation |
| 2 | `urls_check_access.py` | Verify source URLs are actually reachable (parallel HTTP HEAD checks), flagging 403s or other access problems |
| 3 | `collection_create.py` | Create the top-level STAC collection record (`collection.json`) with spatial and temporal extent metadata |
| 4 | `item_create.py` | The main workhorse — read each GeoTIFF's metadata remotely, cache it, and generate a STAC JSON record for each file (32 parallel workers) |
| 5 | `item_validate.py` | Check every generated STAC JSON against the spec using pystac, producing a pass/fail report |
| 6 | `s3_sync.R` | Sync the local catalog to the S3 bucket, uploading only new or changed files |
| — | `build_safe.sh` | Orchestrates steps 1–5 with automatic backups, timestamped build directories, and optional auto-promotion to production |
| — | `catalogue_qa.py` | Spot-check QA — randomly samples items and compares local vs S3 versions to catch sync issues |

### Fix-up Scripts

When validation finds problems, these scripts help:

| Script | What it does |
|--------|--------------|
| `item_extract_invalid.py` | Pull failed item IDs from the validation report and convert them back to source URLs |
| `item_reprocess.py` | Re-create invalid items with improved handling (e.g. placeholder dates for files missing date information) |

### Supporting Scripts

| Script | What it does |
|--------|--------------|
| `stac_utils.py` | Shared Python utilities — metadata extraction, date parsing, URL encoding, constants (paths, BC bounding box) |
| `functions.R` | R utilities for VM deployment and table formatting |
| `staticimports.R` | Auto-generated R helper functions |
| `utils.R` | Minimal R utilities |
| `benchmark_fetch.R` | Timing benchmarks for URL fetching approaches |
| `footprint_visualize.R` | Visualize DEM tile footprints on a map |
| `stac_examples.qmd` | Example STAC API queries for exploring the finished catalog |

## Data Flow

```
BC Objectstore (nrs.objectstore.gov.bc.ca/gdwuts)
  ↓ urls_fetch.R — list all GeoTIFF URLs
data/urls_list.txt
  ↓ urls_check_access.py — verify URLs are reachable
data/urls_access_checks.csv
  ↓ item_create.py — read metadata, cache it, generate STAC records
data/stac_geotiff_checks.csv          (cached metadata)
stac/prod/stac_dem_bc/*.json           (one record per DEM tile)
stac/prod/stac_dem_bc/collection.json  (collection summary)
  ↓ item_validate.py — check all records against STAC spec
data/stac_item_validation.csv
  ↓ s3_sync.R — push to cloud
s3://stac-dem-bc/
  ↓ pgstac registration
images.a11s.one (searchable API)
```

## Re-running is Safe

Every step checks for existing outputs and skips work already done. You can re-run after adding new files or fixing a problem without reprocessing everything:

| Step | What gets skipped |
|------|-------------------|
| `urls_fetch.R` | Reuses cached `urls_list.txt` in test mode |
| `urls_check_access.py` | URLs already checked (cached in CSV) |
| `item_create.py` | GeoTIFFs with cached metadata skip the slow remote read; existing items skip creation |
| `item_validate.py` | In `--incremental` mode, only validates items added since the last run |
| `s3_sync.R` | Only uploads new or changed files |

## Run Modes

Most scripts support flags that control scope:

```bash
# Test mode — process a small sample for development
python scripts/item_create.py --test --test-count 50

# Incremental — only process new files detected by change detection
python scripts/item_create.py --incremental

# Reprocess — fix previously invalid items
python scripts/item_create.py --reprocess-invalid

# Full production — process everything
python scripts/item_create.py
```

## Logs

Each pipeline run generates timestamped log files in `logs/`. The naming convention is `YYYYMMDD_HHMMSS_description.log`.

Logs capture configuration, progress, errors, warnings, and timing — making it possible to debug failures after the fact and track performance over time. When a weekly cron job runs unattended, logs are the only record of what happened.

The `build_safe.sh` orchestrator creates a separate log file for each step, so if step 4 fails you can inspect that log without wading through the output of steps 1–3.

## Performance

| Scenario | Time | Notes |
|----------|------|-------|
| Full build (58,000 items) | ~5–6 hours | Network I/O bound — reading remote GeoTIFFs for metadata |
| Incremental update (50 new files) | 5–15 minutes | Reads only new files, builds from cache for the rest |
| Validation only | ~10 minutes | Local JSON file reads, no network |

The bottleneck is network: each GeoTIFF must be partially read over HTTP to extract its projection, dimensions, and bounds. Once cached, subsequent builds are fast.

## Prerequisites

| Component | What's needed |
|-----------|---------------|
| Python | `pystac`, `rio_stac`, `rasterio`, `rio-cogeo`, `pandas`, `tqdm` |
| R | `ngr` package (for objectstore listing) |
| AWS CLI | Configured with write access to `s3://stac-dem-bc` |
| System | `rio` CLI tools (installed with rasterio) |

## After the Pipeline

Once the catalog is on S3, register it in pgstac to make it searchable:

```bash
ssh root@<VM_IP> "bash /tmp/stac_register-pypgstac.sh stac-dem-bc https://stac-dem-bc.s3.amazonaws.com"
```

This loads the STAC records into PostgreSQL, powering the search API at `images.a11s.one`. Once registered, the collection is browsable in QGIS (STAC Data Source Manager), through the API directly, or any STAC-compatible client.
