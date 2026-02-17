# Task Plan: Source URL Accessibility Validation (Issue #13)

**Status:** In Progress
**Branch:** `13-fix-s3-permissions`
**Started:** 2026-02-17
**Issue:** https://github.com/NewGraphEnvironment/stac_dem_bc/issues/13

## Goal

Create a validation script that checks source GeoTIFF URL accessibility on the BC objectstore, produces a CSV report shareable with GeoBC, and integrates into the build pipeline.

## Steps

### Step 1: Archive old planning files ⬜ pending
- [x] Move issue #7 planning to `planning/archive/2026-02-issue-7-qmd-to-py/`
- [x] Create fresh planning files for issue #13

### Step 2: Add `check_url_accessible()` to stac_utils.py ⬜ pending
- [ ] HTTP HEAD request helper with timeout
- [ ] Returns dict: `{url, status_code, accessible, error}`

### Step 3: Create `scripts/urls_check_access.py` ⬜ pending
- [ ] argparse CLI (`--urls-file`)
- [ ] Incremental: load cache from `data/urls_access_checks.csv`, skip known URLs
- [ ] Parallel HEAD requests (ThreadPoolExecutor)
- [ ] CSV output shareable with GeoBC
- [ ] Summary logging
- [ ] Exit code 1 if any inaccessible

### Step 4: Update `scripts/build_safe.sh` ⬜ pending
- [ ] Add accessibility check step after URL fetch
- [ ] Warning only (don't block build — GeoTIFF validation handles skipping)

### Step 5: Test and verify ⬜ pending
- [ ] Test against known-bad 092p045 URLs
- [ ] Test against known-good URLs
- [ ] Verify CSV output format

## SRED Tracking

- Relates to NewGraphEnvironment/sred-2025-2026#3

---

**Last updated:** 2026-02-17
