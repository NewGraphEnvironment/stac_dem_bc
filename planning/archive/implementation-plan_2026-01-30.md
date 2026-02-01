# Implementation Plan: Automated Weekly STAC DEM BC Updates

**Status:** In Progress - Phase 1-2 Modernization
**Worktree:** stac_dem_bc-phase1-2-modernization
**Started:** 2026-01-30
**Last Updated:** 2026-01-30

## Overview

Set up automated weekly updates of STAC DEM BC JSONs using VM-based cron automation with incremental change detection. This implementation adopts proven performance improvements from stac_orthophoto_bc (parallel processing, pre-validation) before building automation infrastructure.

**Architecture:** VM-based cron → Change detection → Parallel validation/processing → S3 sync → PgSTAC registration

**Expected Performance:**
- First run (full): ~1-1.5 hours (down from 5-6 hours)
- Weekly runs (incremental): 5-15 minutes for typical 10-50 new files
- Cost: $0 additional (uses existing VM)

## Implementation Phases

### Phase 1: Port stac_orthophoto_bc Performance Improvements ✅

**Goal:** Modernize stac_dem_bc with parallel processing and validation from stac_orthophoto_bc

#### 1.1 Add Pre-Validation System ✅
**File:** `stac_create_item.qmd`

**Changes:**
- Added `check_geotiff_cog()` function (from stac_orthophoto_bc:106-122)
- Implemented parallel validation using `concurrent.futures.ThreadPoolExecutor`
- Created validation cache at `data/stac_geotiff_checks.csv`
- Added results lookup dictionary for fast access
- Skip unreadable GeoTIFFs during processing

**Expected improvement:** Skip ~100-500 invalid files, save 10-30 minutes

#### 1.2 Implement Parallel Item Creation ✅
**File:** `stac_create_item.qmd`

**Changes:**
- Replaced sequential `for path_item in path_items:` loop with `ThreadPoolExecutor.map()`
- Added `process_item()` function with error handling
- Added `tqdm` progress bars for visibility
- Detect media type based on COG validation results:
  - COG: `"image/tiff; application=geotiff; profile=cloud-optimized"`
  - Non-COG: `"image/tiff; application=geotiff"`

**Expected improvement:** 4-8x speedup (from ~4 hours to ~30-45 minutes)

#### 1.3 Optimize Spatial Extent Calculation 🚧
**File:** `stac_create_collection.qmd`

**Changes:**
- Replace calculated bbox with hardcoded BC bbox: `[-140, 48, -114, 60]`
- Use: `spatial_extent = pystac.SpatialExtent([[-140, 48, -114, 60]])`
- Document coordinates source and rationale

**Trade-off:** Slightly less precise but BC boundary is stable, saves 20 minutes

**Note:** See GitHub issue for preserving bbox calculation method for other projects

#### 1.4 Add Test Mode and Dependencies ✅
**File:** `stac_create_item.qmd`

**Changes:**
- Added test mode flags: `test_only = True/False`, `test_number_items = 10`
- Added Python dependencies:
  - `tqdm` for progress bars
  - `pandas` for validation tracking
  - `concurrent.futures` (built-in)
  - `rio-cogeo` for validation

---

### Phase 2: Implement Incremental Updates 🚧

**Goal:** Process only new/changed files, not all 22,548 every time

#### 2.1 Create Change Detection Script
**New file:** `scripts/detect_changes.py`

**Logic:**
```python
# Fetch current provincial DEM directory listing
# Compare with cached urls_list.txt
# Identify new URLs, deleted URLs
# Exit with code 0 if no changes (skip update)
# Exit with code 1 if changes found
# Write new_urls to data/urls_new.txt
# Update data/urls_list.txt with current inventory
```

**Key functions:**
- `fetch_bc_dem_directory()` - Query provincial bucket
- `detect_changes(current, previous)` - Compare sets
- `save_urls(urls, filepath)` - Persist results

#### 2.2 Add Incremental Mode to Item Creation
**File:** `stac_create_item.qmd`

**Changes:**
- Add parameter: `incremental = False` (default full mode)
- When `incremental=True`: read `data/urls_new.txt` instead of `data/urls_list.txt`
- Append new items to existing collection instead of rebuilding
- Skip validation for already-validated files (check CSV cache)

**Status:** Partial implementation - flag added, needs enhancement for collection appending

#### 2.3 Handle Deletions
**Logic:**
- Track deleted URLs in `data/urls_deleted.txt`
- Log deletions for audit trail
- Optionally remove from S3 and PgSTAC (manual review first)

**Expected improvement:** Weekly runs reduced from 5-6 hours to 5-15 minutes

---

### Phase 3: VM Automation Setup 📅

**Goal:** Automated weekly execution on DigitalOcean VM (stac-prod)

**Worktree:** Create `stac_dem_bc-phase3-automation` after Phase 1-2 testing complete

**Infrastructure Note:**
- S3 buckets already managed via OpenTofu/Terraform in **awshak** repository
- Prod: `s3://stac-dem-bc` (IaC-managed)
- Test: Can easily create `s3://dev-stac-dem-bc` via awshak modules
- VM deployment: Phase 3 uses manual approach via `vm_upload_run()`
- Future: Migrate VM provisioning to awshak for full IaC reproducibility

#### 3.1 Create Master Automation Script
**New file:** `scripts/config/stac_update_weekly.sh`

**Workflow:**
1. Initialize logging to `/var/log/stac_dem_bc/update_YYYYMMDD_HHMMSS.log`
2. Run change detection (`python3 scripts/detect_changes.py`)
3. Exit if no changes (code 0)
4. Run item creation with incremental mode
5. Sync to S3 (`aws s3 sync`)
6. Unregister old collection (`stac_unregister.sh`)
7. Register updated collection (`stac_register.sh`)
8. Log benchmarks to CSV
9. Output summary

#### 3.2 Deploy to VM
Use `vm_upload_run()` function from stac_uav_bc

#### 3.3 Configure Cron on VM
```cron
# Run every Sunday at 2 AM UTC
0 2 * * 0 /home/airvine/config/stac_update_weekly.sh
```

#### 3.4 Set Up Logging Directory
```bash
mkdir -p /var/log/stac_dem_bc
chown airvine:airvine /var/log/stac_dem_bc
```

---

### Phase 4: Benchmarking & Monitoring 📅

**Goal:** Track performance and ensure reliability

#### 4.1 Create Benchmarking System
**File:** `/home/airvine/stac_dem_bc/benchmarks.csv` (on VM)

**Columns:**
```csv
date,new_files,deleted_files,validation_time_s,generation_time_s,sync_time_s,registration_time_s,total_time_s,errors
```

---

### Phase 5: Resolve Issue #3 📅

**Goal:** Ensure proper GeoTIFF validation and media type assignment

#### 5.1 Verify Implementation ✅
- `check_geotiff_cog()` function validates files using `rio cogeo validate`
- Validation results cached in CSV
- `asset_media_type` set correctly based on COG status
- Invalid GeoTIFFs skipped with logged warnings

#### 5.2 Test with Known Files
Test cases:
- Valid COG from provincial bucket
- Valid non-COG TIFF
- Corrupted/unreadable file
- Missing file (404)

---

### Phase 6: Testing & Validation 📅

**Goal:** Ensure automation works before going live

#### 6.1 Local Testing
- Test change detection
- Test incremental update with small sample
- Verify JSON output
- Test S3 sync to dev bucket (dry-run)

#### 6.2 VM Testing (Dry Run)
- Run automation script manually on VM
- Verify logs and benchmarks
- Check S3 sync completion

#### 6.3 Full Integration Test
- Complete end-to-end workflow
- Verify PgSTAC registration
- Test STAC API queries
- Verify items render via Titiler

---

## Critical Files

### Files Created
- ✅ `/planning/active/implementation-plan.md` - This plan
- ✅ `/CLAUDE.md` - Project guidelines and context
- ⏳ `/scripts/detect_changes.py` - Change detection logic
- ⏳ `/scripts/config/stac_update_weekly.sh` - Master automation script
- ⏳ `/data/stac_geotiff_checks.csv` - Validation cache
- ⏳ `/data/urls_new.txt` - New URLs detected

### Files Modified
- ✅ `/stac_create_item.qmd` - Added parallel processing, validation, incremental mode
- ⏳ `/stac_create_collection.qmd` - Use hardcoded bbox

### Reference Files (Read-Only)
- `/Users/airvine/Projects/repo/stac_orthophoto_bc/stac_create_item.qmd:58-207`
- `/Users/airvine/Projects/repo/stac_uav_bc/scripts/config/stac_register.sh`
- `/Users/airvine/Projects/repo/stac_uav_bc/scripts/functions.R:15-45`

---

## Success Criteria

1. **Automation Works:** Successful weekly updates without manual intervention for 4+ consecutive weeks
2. **Performance Improved:** <15 minutes for typical weekly update; <1.5 hours for full refresh
3. **Data Quality:** 100% of items validated before registration
4. **Reliability:** 95%+ success rate over 8 weeks
5. **Cost-Effective:** $0 additional infrastructure cost
6. **Auditable:** Complete benchmarking logs and execution history
7. **Issue #3 Resolved:** Proper GeoTIFF validation and media type assignment
8. **Maintainable:** Clear documentation, rollback procedure, troubleshooting guide

---

## Risk Mitigation

### Risk: VM Failure
**Mitigation:**
- Health check endpoint updates after each run
- Email notifications on failure (optional)
- Documented manual backup process

### Risk: BC Provincial Directory Format Changes
**Mitigation:**
- Error handling in detect_changes.py
- Alert on unexpected parse failures
- Graceful exit if directory unreadable

### Risk: PgSTAC Registration Failure
**Mitigation:**
- Chunked registration (1000 items per chunk)
- Retry logic in stac_register.sh
- Separate unregister step ensures clean slate

### Risk: Validation Cache Corruption
**Mitigation:**
- Monthly full validation run (rebuild cache)
- Version control for cache files
- Rebuild cache from scratch if discrepancies detected

---

## SRED Tracking

**Primary:** https://github.com/NewGraphEnvironment/sred-2025-2026/issues/8
**Secondary:** https://github.com/NewGraphEnvironment/sred-2025-2026/issues/3
**Related:** https://github.com/NewGraphEnvironment/stac_dem_bc/issues/3
**Milestone:** https://github.com/NewGraphEnvironment/sred-2025-2026/milestone/1

**Experimental aspects:**
- Automated STAC collection updates from provincial data sources
- Incremental change detection for large geospatial datasets
- Parallel validation and processing of 22K+ remote GeoTIFFs
- Cloud-optimized GeoTIFF detection and media type assignment

---

## Legend
- ✅ Completed
- 🚧 In Progress
- ⏳ Pending
- 📅 Future Phase
