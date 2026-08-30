# Pipeline Scripts

This pipeline builds a searchable catalog of British Columbia's Digital Elevation Model (DEM) data. It takes ~58,000 GeoTIFF files hosted on the provincial objectstore, validates them, generates standardized metadata records, and registers them in a searchable catalog so anyone can find elevation data by location and time.

## Key Concepts

**DEM (Digital Elevation Model)** — A grid of elevation values representing the shape of the ground surface. Each pixel stores a height value (in metres). Used for slope analysis, flood modelling, watershed delineation, and terrain visualization.

**GeoTIFF** — An image file format that embeds geographic coordinate information (projection, position, pixel size) directly in the file. This means GIS software knows exactly where on Earth the image belongs without needing a separate location file.

**COG (Cloud Optimized GeoTIFF)** — A GeoTIFF organized internally so that a viewer can request just the piece it needs (e.g. a zoomed-in corner) over the internet, without downloading the whole file. The pipeline detects which source files are COGs and tags them accordingly in the catalog.

**STAC (SpatioTemporal Asset Catalog)** — A standard way to describe geographic datasets with where-and-when metadata. Think of it as a library catalog for spatial data: each file gets a JSON record describing its location, date, and download link. This makes the collection searchable — "show me all DEMs that overlap this watershed" or "show me DEMs acquired after 2020."

**S3** — Cloud file storage (Amazon-compatible). The generated catalog JSON files are uploaded here so they are accessible via URL from anywhere.

**pgstac** — A PostgreSQL database that stores STAC records and exposes them through a search API. Hosted at `images.a11s.one`, this is what allows users to search the collection by location from QGIS, a web browser, or any STAC-compatible tool.

**Date extraction** — The source GeoTIFFs don't carry acquisition dates in their internal metadata, so the pipeline infers dates from the filename. It looks for a pattern like `_utm10_20230415.tif` (after `_utmXX_`, grab the 4–8 digit date) to get a full date (`YYYYMMDD`) or just a year (`YYYY`). If neither pattern is found, it falls back to looking for a `/YYYY/` directory in the URL path. Files with no detectable date get a placeholder (`2000-01-01`) and are flagged with `datetime_unknown=True` so they can be filtered or fixed later.

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
| 0 | `detect_changes.R` | Compare the cached URL list against a fresh objectstore listing to find new or deleted files — this drives incremental updates. Also refreshes the DSM listing from the same walk |
| 1 | `urls_fetch.R` | Fetch the master list of DEM and DSM GeoTIFF URLs from the BC objectstore (~100,000 DEM, ~96,000 DSM) in one bucket walk |
| 1b | `dsm_pair.py` | Pair each DEM tile with its DSM sibling on tile id and acquisition date, and report every tile that did not pair |
| 2 | `urls_check_access.py` | Verify source URLs are actually reachable (parallel HTTP HEAD checks), flagging 403s or other access problems |
| 3 | `collection_create.py` | Create the top-level STAC collection record (`collection.json`) with extent, providers and keywords |
| 3b | `collection_patch.py` | Apply collection metadata (providers, keywords, description) to an **existing** `collection.json` — the monthly run fetches the published collection rather than regenerating it, so `collection_create.py` never runs there |
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
| `dsm_verify.py` | Verify on a stratified sample that a DSM really does share its paired DEM's COG status and footprint — the evidence behind inheriting the media type rather than measuring all ~96k |

### Supporting Scripts

| Script | What it does |
|--------|--------------|
| `stac_utils.py` | Shared Python utilities — metadata extraction, date parsing, URL encoding, tile-key parsing for DEM/DSM pairing, constants (paths, BC bounding box) |
| `urls_listing.R` | Shared objectstore listing — one bucket walk yielding DEM keys, DSM keys and `dsm/` directory membership |
| `functions.R` | R utilities for VM deployment and table formatting |
| `staticimports.R` | Auto-generated R helper functions |
| `utils.R` | Minimal R utilities |
| `benchmark_fetch.R` | Timing benchmarks for URL fetching approaches |
| `footprint_visualize.R` | Visualize DEM tile footprints on a map |
| `stac_examples.qmd` | Example STAC API queries for exploring the finished catalog |

## Data Flow

```
BC Objectstore (nrs.objectstore.gov.bc.ca/gdwuts)
  ↓ urls_fetch.R / detect_changes.R — ONE bucket walk
data/urls_list.txt        (DEM .tif)
data/urls_dsm.txt         (DSM .tif)
data/dsm_groups.txt       (mapsheet-years having a dsm/ directory, .laz-only included)
  ↓ dsm_pair.py — match on tile id + acquisition date
data/dem_dsm_pairs.csv          (one row per DEM, always)
data/dsm_pairing_report.md      (what paired, and every tile that did not)
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
  ↓ catalogue_register.sh --drift — upsert into pgstac
images.a11s.one (searchable API)
```

## Re-running is Safe

Every step checks for existing outputs and skips work already done. You can re-run after adding new files or fixing a problem without reprocessing everything:

| Step | What gets skipped |
|------|-------------------|
| `urls_fetch.R` | Reuses cached `urls_list.txt` in test mode |
| `dsm_pair.py` | Nothing — it is pure and fast (~1 s over 100k tiles), and is re-run whenever the listing changes |
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

## Automation

`.github/workflows/update.yml` runs the incremental pipeline monthly (3rd of the month, 09:23 UTC) on a GitHub-hosted runner, and can be run on demand from the Actions tab (`workflow_dispatch`). It authenticates to AWS via OIDC (`role_gha_stac_dem_bc`, provisioned in the rtj infrastructure repo — no stored keys) and:

1. Detects changes against the committed `data/urls_list.txt` cache (exit 0 = no changes → clean early exit; 1 = changes; 2 = error)
2. Builds and validates STAC items for new URLs only, in a runner workspace (`STAC_OUTPUT_DIR`) seeded with the live `collection.json` from S3
3. Syncs item JSONs then `collection.json` (in that order, never `--delete`) via `s3_sync-ci.sh`
4. Commits the refreshed `data/` caches back to `main` — a failed run therefore persists nothing and the next run re-detects cleanly. A deletions-only month (e.g. 2026-08: 0 new, 43 removed upstream) skips the build steps but still records the audit trail

**Where the evidence lives:**

- **Run history** — the [Actions tab](https://github.com/NewGraphEnvironment/stac_dem_bc/actions/workflows/update.yml) keeps every run's logs, and each run uploads a `run-logs` artifact (change-detection log + access-check CSV). Artifacts expire after ~90 days — they are the working record, not the archive.
- **The durable ledger is git** — a successful run with changes ends in one bot commit on `main` ("Monthly incremental update: refresh caches (YYYY-MM)") touching only `data/`. `git log --oneline --author=github-actions -- data/` is the complete month-by-month history. Within those commits: `urls_list.txt` is the current source inventory, `urls_deleted.txt` the cumulative audit of sources removed upstream (their catalog items are retained), and the two CSVs the validation state for sources and outputs. A month absent from the ledger either had no changes or failed — and a failed month self-heals, because nothing was committed to mark its files as seen.
- **The catalog itself** — `s3://stac-dem-bc/` is the only complete copy (`collection.json` plus one JSON per item, bucket versioned). The API at `images.a11s.one` serves whatever was last *registered*, so it can trail S3 between a sync and a registration run. `scripts/catalogue_register.sh --verify` answers "is it behind?" without changing anything.
- **Design history and one-time events** — `planning/archive/2026-07-issue-23-monthly-automation/` records how this system was built, the pre-build review findings, and the July 2026 catch-up (58k → 98k items).

**Failure triage:**

- **One invalid item blocks the whole batch** (the validate step is a deliberate hard gate, and it re-fails monthly until fixed). Remediate with `item_extract_invalid.py` → `item_reprocess.py`, or investigate via the run's `run-logs` artifact.
- **Inaccessible source URLs do not block** — the access check is warn-only (matching `build_safe.sh`); results land in `data/urls_access_checks.csv` for reporting to GeoBC.
- **Item shortfall warning**: the run annotates a warning when fewer items were created than URLs detected (an all-invalid batch stays green — validate/sync are skipped and the batch is recorded as attempted). Individual metadata reads can fail transiently, and a failed read is cached in `data/stac_geotiff_checks.csv` as not-a-GeoTIFF — so those URLs are not retried automatically. To recover: delete the affected rows from `stac_geotiff_checks.csv`, run `urls_reconcile.py --apply`, commit both files, and the next run rebuilds them.
- **Oversized batches**: a month with more than ~35k new files cannot fit the job timeout, and re-running does not help (the run commits nothing, so it repeats identically). Run the pipeline locally instead (the initial 2026 catch-up follows this same local path), then let the cron resume.
- **Cron auto-disable**: GitHub disables scheduled workflows in public repos after ~60 days without repository activity. No-change months produce no commits, so after a quiet stretch check the Actions tab and re-enable/dispatch.

## After the Pipeline

Once the catalog is on S3, register it in pgstac to make it searchable. Registration is client-side and lives in this repo (#27) — one command from any machine with tailnet SSH to the STAC host:

```bash
scripts/catalogue_register.sh --verify   # is the API behind S3? changes nothing
scripts/catalogue_register.sh --drift    # register whatever it is missing
```

`--drift` asks the API which items it actually holds, diffs that against what `collection.json` publishes, and registers the difference. It is stateless — it needs no record of what previous runs did — so a month that nobody registered simply gets picked up by the next run. That matters: registration was skipped once for a month and the API served 60,126 items against 98,040 published.

**Everything here upserts and nothing deletes.** `pypgstac load --method upsert` updates rows in place, so there is no window in which the API serves less than it did before. The older path — rtj's `stac_register-pypgstac.sh` — DELETEs the collection and then reloads it, and on 2026-08-29 it failed in between and left `images.a11s.one` serving **zero items** until it was repaired by hand. `pgstac.items.collection` is `ON DELETE CASCADE`, so dropping the collection row takes every item with it. That same cascade is why the collection must be registered *before* its items, which is the inverse of the S3 sync order — both scripts say so in their headers.

| script | does |
|---|---|
| `catalogue_register.sh` | the orchestrator: `--verify`, `--drift`, `--all`, `--ids-file` |
| `collection_register.sh` | upsert one `collection.json` (run first — the FK requires it) |
| `item_register.sh` | upsert items; paths on **stdin**, never argv |
| `collection_unregister.sh` | the only destructive script here; for #34's cutover |
| `register_manifest.py` | id/NDJSON logic, so the shell stays thin and the logic is testable |

Measured 2026-08-30 against the live catalogue of 102,460 items: `--verify` takes **3m40s**, nearly all of it enumerating registered ids from the API (11 requests of 10,000). A 3-item upsert is a couple of seconds. The database load itself is seconds even at full scale; historically it is the *fetch* of item JSONs from S3 that dominates a full `--all` run, at roughly 45 minutes.

Verification is by **set equality in both directions** — missing and orphaned — never by a count. The API has no aggregation extension (`/aggregate` 404s) and returns `numberMatched: null`, and a `/search` on a list of ids silently omits the ones that do not exist. So "I asked for N and got N back" can be true while the sets differ.

Registration still runs from a laptop rather than from CI, because no GitHub Actions runner can reach the host today — there is no Tailscale action and no SSH deploy key in any of these repos. That decision belongs in the infrastructure repo and unblocks every catalogue repo at once.

Once registered, the collection is browsable in QGIS (STAC Data Source Manager), through the API directly, or any STAC-compatible client.

## Tests

`tests/` holds the pairing contract suite (51 tests, offline). Fixtures under
`tests/fixtures/` are **real objectstore listings** taken 2026-08-28, so the tests
exercise the filename generations that actually exist rather than idealised ones.

```bash
python -m pytest tests/ -q
```

The monthly workflow runs them before anything touches the catalogue. Three of the
guards are mutation-tested — collapsing `no_raster_dsm`, treating an empty listing
as no-DSM, and dropping unpaired DEMs each fail the suite — because a guard nobody
has seen fail is decoration.

## DEM/DSM Pairing

A DEM and its DSM come from the same flight over the same footprint at the same
time, so they belong on one STAC item as two assets — `image` (bare-earth DEM,
named for backward compatibility) and `dsm` (digital surface model).

There is no manifest, so the relationship is inferred from filenames, and the
naming convention is not uniform across deliveries. **Matching is on parsed
semantics** — tile id, acquisition date, utm zone, containing mapsheet-year — and
the naming convention is recorded afterwards as an assertion on an already-matched
pair. The inversion matters: a future delivery using a convention nobody has seen
still pairs, and appears in the report as `convention=unknown` rather than quietly
losing its DSM.

Never construct a sibling path. `/dem/` → `/dsm/` URL swapping is what produced
the documented finding that BC published no surface models: the swap resolves in
the four 2022 mapsheet-years that use identical basenames and 404s in the other
153 (issue #29).

### What the pairing reports

Every DEM lands in exactly one bucket, and the counts are asserted to sum to the
input before anything is written:

| status | meaning |
|---|---|
| `paired` | a DSM was found; the naming convention is recorded |
| `no_raster_dsm` | the mapsheet-year's `dsm/` holds no `.tif` — published as `.laz` only |
| `no_dsm_dir` | the mapsheet-year has no `dsm/` directory |
| `unpaired` | a raster DSM exists in the group but not for this tile |
| `unparseable` | no tile id could be parsed — reported, never guessed at |

As of 2026-08-28, over 102,416 DEM tiles: 95,887 paired (95,768 `suffix`,
117 `identical`, 2 `unknown`), 1,211 `no_raster_dsm` across 11 mapsheet-years,
2,900 `no_dsm_dir`, 2,245 `unparseable` (the whole `albers10k2m` product family,
which carries no mapsheet tile id), 173 `unpaired`.

### The two listings are not redundant

`urls_dsm.txt` holds raster DSM keys. `dsm_groups.txt` holds every mapsheet-year
that has a `dsm/` directory *whatever it contains*. Without the second file a
`.laz`-only delivery produces no keys at all and is indistinguishable from a
delivery that shipped no DSM — which would misreport 1,211 real tiles.

### Media type

The DSM's media type is inherited from its paired DEM's COG status rather than
measured: the two come from one delivery and one processing run. Measuring all
~96k directly is a 15–20 hour network pass that cannot fit the monthly runner.
`dsm_verify.py` is the evidence for the assumption — a stratified sample across
naming convention and acquisition year, asserting exact footprint agreement and
≥99% COG-status agreement, exiting non-zero if either threshold is missed.
