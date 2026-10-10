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
python scripts/footprint_extract.py   # resumable; ~20 h the first time, then only new tiles
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
| 3c | `footprint_extract.py` | Read every tile not yet in `data/footprints.csv` once and record its **footprint** — the BCGS 1:2,500 cell minus its no-data, or the valid data for tiles named off that grid — with `valid_percent`, and a sha256 + size where the file was downloaded whole. Streams to one scratch directory removed at exit; stops cleanly below `--min-free-gb`; resumable. Logic in `footprint.py` (#2) |
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

### Rewriting what is already published

Some fixes cannot be made by building new items, because the defect is in the
~102k item JSONs already on S3. These rewrite them **in place** — fetch, edit the
one or two fields we mean to change, write back — rather than rebuilding, which
would silently swap ~60k items from one code path to another (60,324 of 100,345
metadata-cache rows predate spatial-metadata caching).

| Script | What it does |
|--------|--------------|
| `item_rewrite.py` | The shared harness. Library only, no CLI: fetch with retry, a resumable manifest, the error-rate gate, and verification by re-derivation. Every behaviour in it has a named incident behind it |
| `item_backfill.py` | #31: add the `dsm` asset to published items, and percent-encode the 90 legacy hrefs carrying literal spaces |
| `item_migrate.py` | #34: move published items to the renamed collection and the renamed DEM asset key |
| `footprint_apply.py` | #2/#55: footprint geometry, `valid_percent` and checksum on `dem`, `lidarbc:delivery`, and `datetime_unknown` → `lidarbc:datetime_unknown`. Uses `item_fields.py`, the same apply path `item_create.py` uses; refuses a full run until the footprint cache covers every listed URL |

Three things to know before running either:

- **The manifest is a claim that its ids are PUBLISHED**, because the next run
  computes `todo = published - manifest`. It carries a `# migration:` header and
  is refused by any other migration — `data/backfill_done.txt` holds 98,040 ids
  against 102,460 published, so reading it as #34's would have rewritten 4.3% of
  the catalogue and exited 0. The CI job discards it when the sync did not run,
  because progress that was never published must be redone, not skipped.
- **Completeness is asserted over the whole population**, not a sample: the
  manifest plus anything staged by an earlier step must equal the published set.
  A sample says nothing about a population, and a half-rewritten catalogue is
  invisible to every other check here — item ids do not change, so set equality
  still reports `IN SYNC`.
- **They never touch `collection.json`**, asset hrefs, or `links[rel=collection]`.
  Item ids and hrefs are unchanged, so every link stays valid; the collection's
  own edit is `collection_patch.py`.

### Supporting Scripts

| Script | What it does |
|--------|--------------|
| `footprint.py` | #2: the BCGS cell from a tile id, a footprint from a validity mask (outward-biased simplification, vertex cap, CCW, rounding), reading a tile, and writing the fields onto an item |
| `item_fields.py` | #2/#55: the one path that applies footprint and `lidarbc:` fields to an item dict, and `audit`, which checks a staged set for them (stacs audit covers ids and asset keys only) |
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
  ↓ footprint_extract.py — read each new tile once: footprint, valid %, sha256
data/footprints.csv             (empty footprint_wkt = the tile's BCGS cell)
  ↓ item_create.py — read metadata, cache it, generate STAC records
data/stac_geotiff_checks.csv          (cached metadata)
stac/prod/stac_dem_bc/*.json           (one record per DEM tile)
stac/prod/stac_dem_bc/collection.json  (collection summary)
  ↓ item_validate.py — check all records against STAC spec
data/stac_item_validation.csv
  ↓ s3_sync.R — push to cloud
s3://stac-dem-bc/
  ↓ stacs register --mode drift — upsert into pgstac
images.a11s.one (searchable API)
```

## Re-running is Safe

Every step checks for existing outputs and skips work already done. You can re-run after adding new files or fixing a problem without reprocessing everything:

| Step | What gets skipped |
|------|-------------------|
| `urls_fetch.R` | Reuses cached `urls_list.txt` in test mode |
| `dsm_pair.py` | Nothing — it is pure and fast (~1 s over 100k tiles), and is re-run whenever the listing changes |
| `urls_check_access.py` | URLs already checked (cached in CSV) |
| `footprint_extract.py` | Tiles already in `data/footprints.csv`; a killed run keeps every row it wrote |
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
| Python | ≥ 3.11; `pystac`, `rio_stac`, `rasterio`, `rio-cogeo`, `pandas`, `tqdm`, and [`stacs`](https://github.com/NewGraphEnvironment/stacs) for registration (pinned tag; see `environment.yml`) |
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
- **The catalog itself** — `s3://stac-dem-bc/` is the only complete copy (`collection.json` plus one JSON per item, bucket versioned). The API at `images.a11s.one` serves whatever was last *registered*, so it can trail S3 between a sync and a registration run. `stacs verify --config stacs.toml` answers "is it behind?" without changing anything.
- **Design history and one-time events** — `planning/archive/2026-07-issue-23-monthly-automation/` records how this system was built, the pre-build review findings, and the July 2026 catch-up (58k → 98k items).

**Failure triage:**

- **One invalid item blocks the whole batch** (the validate step is a deliberate hard gate, and it re-fails monthly until fixed). Remediate with `item_extract_invalid.py` → `item_reprocess.py`, or investigate via the run's `run-logs` artifact.
- **Inaccessible source URLs do not block** — the access check is warn-only (matching `build_safe.sh`); results land in `data/urls_access_checks.csv` for reporting to GeoBC.
- **Item shortfall warning**: the run annotates a warning when fewer items were created than URLs detected (an all-invalid batch stays green — validate/sync are skipped and the batch is recorded as attempted). Individual metadata reads can fail transiently, and a failed read is cached in `data/stac_geotiff_checks.csv` as not-a-GeoTIFF — so those URLs are not retried automatically. To recover: delete the affected rows from `stac_geotiff_checks.csv`, run `urls_reconcile.py --apply`, commit both files, and the next run rebuilds them.
- **Oversized batches**: a month with more than ~35k new files cannot fit the job timeout, and re-running does not help (the run commits nothing, so it repeats identically). Run the pipeline locally instead (the initial 2026 catch-up follows this same local path), then let the cron resume.
- **Cron auto-disable**: GitHub disables scheduled workflows in public repos after ~60 days without repository activity. No-change months produce no commits, so after a quiet stretch check the Actions tab and re-enable/dispatch.

## After the Pipeline

Once the catalog is on S3, register it in pgstac to make it searchable. Registration and verification are the [`stacs`](https://github.com/NewGraphEnvironment/stacs) package (#49), pinned by tag in `environment.yml` and the workflow, and this catalogue declares itself to it in [`stacs.toml`](../stacs.toml) at the repo root. One command from any machine with tailnet SSH to the STAC host:

```bash
.venv/bin/stacs verify   --config stacs.toml --out-dir verify_report/   # is the API behind S3, or serving stale bodies? changes nothing
.venv/bin/stacs register --config stacs.toml --mode drift               # register whatever it is missing or serves stale
.venv/bin/stacs register --config stacs.toml --mode all --dryrun        # the full re-register, previewed
```

`drift` fetches every body `collection.json` publishes, compares each with what the API actually serves, and registers the items it is missing or serves with a different body (#45). Comparing ids alone is not enough: a rebuild keeps every id, so a catalogue whose bodies had all been rewritten used to verify `IN SYNC` while the API served the old ones. It is stateless — it needs no record of what previous runs did — so a month that nobody registered simply gets picked up by the next run. That matters: registration was skipped once for a month and the API served 60,126 items against 98,040 published.

To register a known subset, turn source URLs into ids here, where the URL-to-id mapping lives, and hand them to stacs:

```bash
.venv/bin/python scripts/register_manifest.py ids-from-urls --urls-file data/urls_new.txt > ids.txt
.venv/bin/stacs register --config stacs.toml --mode ids --ids-file ids.txt
```

stacs refuses the whole run, before writing, if any id has no item link in the published `collection.json`. `urls_new.txt` also lists sources that `item_create` skipped as unreadable, so in a month with any shortfall use `--mode drift`, which needs no list.

**Everything here upserts and nothing deletes.** `pypgstac load --method upsert` updates rows in place, so there is no window in which the API serves less than it did before. The older path — rtj's `stac_register-pypgstac.sh` — DELETEs the collection and then reloads it, and on 2026-08-29 it failed in between and left `images.a11s.one` serving **zero items** until it was repaired by hand. `pgstac.items.collection` is `ON DELETE CASCADE`, so dropping the collection row takes every item with it. That same cascade is why stacs registers the collection *before* its items, which is the inverse of the S3 sync order in `s3_sync-ci.sh` — both are right for their transport.

| file | does |
|---|---|
| `stacs.toml` | this catalogue for stacs: API, collection id, bucket, the asset rules, and how to reach the host. No setting is a secret — the password is *named* (`password_env`) and stays on the host. Every value is pinned to its module by `tests/test_stacs_config.py` |
| `register_manifest.py` | `ids-from-urls`: source GeoTIFF URLs → item ids, for `--mode ids`. The only registration helper that knows where items come from |
| `collection_unregister.sh` | the only destructive script here; for #34's cutover. stacs is upsert-only, so this stays |

Measured 2026-09-29 against the live catalogue of 102,460 items, with the scripts stacs replaced (`stacs verify` reads the same way): a full verify takes **12–26 min**, because it reads every body. Paging all registered bodies from the API is a steady ~5.5 min (11 requests of 10,000, ~18 MB each). Fetching every published body from S3 is the variable part, between ~6 and ~20 min across four full runs the same day. stac-airphoto-bc's 10,100 items verify in 2m26s. The database load itself is seconds even at full scale.

Verification is by **set equality in both directions** — missing and orphaned — and by **content**, never by a count. The API has no aggregation extension (`/aggregate` 404s) and returns `numberMatched: null`, and a `/search` on a list of ids silently omits the ones that do not exist. So "I asked for N and got N back" can be true while the sets differ. Content is compared by digest over canonical JSON with `links` removed; what pgstac changes between load and serve, and why each rule exists, is stacs' [`research/pgstac_round_trip.md`](https://github.com/NewGraphEnvironment/stacs/blob/main/research/pgstac_round_trip.md).

Set equality has one blind spot, and #34 is exactly it. **Item ids do not change during a collection rename**, so a catalogue where half the items name the old collection and half the new is reported `IN SYNC` — and pgstac routes each item by its *own* `collection` field, so a stale body upserts into the old collection successfully, with no error anywhere. The property that breaks is *homogeneity*, not size, and `stacs audit` is what checks it. The monthly workflow runs it over every item it is about to publish, and `register` runs it over every body it is about to send. Neither `verify` nor a drift with nothing to send audits what is already registered, so the publish-time audit is the one that keeps a mixed catalogue out.

The asset rules are **declared** in `stacs.toml`: every item carries `dem` (`stac_utils.ASSET_DEM`) and none carries a key retired by `item_migrate.ASSET_RENAMES` — half of #34's rename, which set equality cannot see. A flag can add to those rules and never loosen them, whatever collection id it names. That replaces #42's own-bucket test, which inferred "is this our catalogue" from the id, the bucket and the item hrefs because the rules lived in code; a declaration needs no inference. Another collection on the endpoint is **registered** from its own repo's `stacs.toml` — the transport settings live only there, and this repo's asset rules would refuse its items (stac-airphoto-bc's is [stac_airphoto_bc#42](https://github.com/NewGraphEnvironment/stac_airphoto_bc/issues/42)). It can be **verified** from anywhere by flags alone, because verify needs no transport:

```bash
.venv/bin/stacs verify --api https://images.a11s.one --collection-id stac-airphoto-bc \
  --bucket-url https://stac-airphoto-bc.s3.us-west-2.amazonaws.com
```

Registration still runs from a laptop rather than from CI, because no GitHub Actions runner can reach the host today — there is no Tailscale action and no SSH deploy key in any of these repos. That decision belongs in the infrastructure repo and unblocks every catalogue repo at once.

Once registered, the collection is browsable in QGIS (STAC Data Source Manager), through the API directly, or any STAC-compatible client.

## Tests

`tests/` holds the contract suites (offline, and a hard gate in CI). Fixtures under
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
time, so they belong on one STAC item as two assets — `dem` (bare earth) and
`dsm` (digital surface model). The DEM asset was keyed `image` until #34, which
renamed it in the same break as the collection; both keys come from
`stac_utils.ASSET_DEM` / `ASSET_DSM` and appear as literals in no other code.
`stacs.toml` declares `dem` for stacs, and `tests/test_stacs_config.py` fails if
the two disagree.

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
