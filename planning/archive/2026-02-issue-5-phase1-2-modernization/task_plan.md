# Task Plan: Automated Weekly STAC DEM BC Updates

**Status:** Phase 1-2 ✅ COMPLETE
**Worktree:** `stac_dem_bc-phase1-2-modernization`
**Started:** 2026-01-30
**Completed:** 2026-02-01
**Target:** Reduce 5-6h processing to 1-1.5h full run, 5-15min weekly incremental

## Goal

Implement automated weekly STAC DEM BC updates using VM-based cron automation with:
- Parallel processing and validation (ported from stac_orthophoto_bc)
- Incremental change detection (process only new/changed files)
- Performance benchmarking and monitoring
- Full IaC-managed infrastructure (S3 via awshak, future VM migration)

**Architecture:** VM cron → Change detection → Parallel validation → Item creation → S3 sync → PgSTAC registration

## Phases

### Phase 1: Performance Modernization ✅ COMPLETE
**Goal:** Port stac_orthophoto_bc parallel processing patterns

- [x] **1.1** Add pre-validation system with COG detection (`check_geotiff_cog()`)
- [x] **1.2** Implement parallel item creation (ThreadPoolExecutor)
- [x] **1.3** Optimize spatial extent calculation (hardcoded BC bbox)
- [x] **1.4** Add test mode flags and update dependencies

**Files modified:** `stac_create_collection.qmd`, `stac_create_item.qmd`, `environment.yml`
**Tested:** 10 items, all features verified working
**Expected improvement:** 4-8x speedup, skip 100-500 invalid files

---

### Phase 2: Incremental Updates ✅ COMPLETE
**Goal:** Process only new/changed files, not all 58,109

- [x] **2.1** Create change detection script (`scripts/detect_changes.R`)
  - Fetch BC DEM directory listing from provincial objectstore via `ngr::ngr_s3_keys_get()`
  - Compare with cached `data/urls_list.txt`
  - Output new URLs to `data/urls_new.txt`, deleted to `data/urls_deleted.txt`
  - Exit code 0 if no changes, 1 if changes detected
  - **Result:** Discovered 35,569 new URLs, 8 deleted (158% growth from 22,548 to 58,109)

- [x] **2.2** Enhance incremental mode in item creation
  - Read from `urls_new.txt` when `incremental=True`
  - Append to existing collection (don't rebuild)
  - Skip validation for already-cached files
  - Add JSON cleanup in test mode (prevents file accumulation)
  - Add duplicate link prevention (reprocessing URLs doesn't create duplicates)
  - **Tested:** Baseline (5 items) → Incremental (+5) → Duplicate detection (0 added, 5 skipped)

- [ ] **2.3** Handle deletions
  - Track deleted URLs for audit trail (✅ implemented in 2.1)
  - Document manual S3/PgSTAC removal process

**Expected improvement:** Weekly runs 5-6h → 5-15 minutes
**Files modified:** `stac_create_item.qmd`, `scripts/detect_changes.R`

---

### Phase 3: VM Automation 📅 FUTURE
**Goal:** Deploy automated weekly execution on stac-prod VM

**Worktree:** Create `stac_dem_bc-phase3-automation` after Phase 1-2 testing complete

- [ ] **3.1** Create master automation script (`scripts/config/stac_update_weekly.sh`)
  - Logging, change detection, incremental processing, S3 sync, PgSTAC registration

- [ ] **3.2** Deploy to VM via `vm_upload_run()` from stac_uav_bc

- [ ] **3.3** Configure cron (Sunday 2 AM UTC)

- [ ] **3.4** Set up logging directory (`/var/log/stac_dem_bc/`)

**Infrastructure:** S3 buckets IaC-managed via awshak (OpenTofu). VM deployment manual (future: migrate to awshak Terraform).

---

### Phase 4: Benchmarking & Monitoring 📅 FUTURE
**Goal:** Track performance and reliability

- [ ] **4.1** Create benchmarking CSV on VM
  - Columns: date, file counts, phase timings, errors
  - Track weekly for SRED evidence

- [ ] **4.2** Add detailed metrics logging

- [ ] **4.3** Optional: Health check endpoint

---

### Phase 5: Validation & Issue Resolution 📅 FUTURE
**Goal:** Verify GeoTIFF validation and resolve stac_dem_bc#3

- [x] **5.1** Verify implementation (COG detection, media type assignment) ✅
- [ ] **5.2** Test with edge cases (valid COG, non-COG, corrupted, 404)
- [ ] **5.3** Document and close issue #3

---

### Phase 6: Testing & Integration 📅 FUTURE
**Goal:** End-to-end validation before production

- [ ] **6.1** Local testing
  - Change detection, incremental update, JSON validation, S3 dry-run

- [ ] **6.2** VM testing (dry run)
  - Manual script execution, log verification, benchmarks

- [ ] **6.3** Full integration test
  - End-to-end workflow, PgSTAC registration, STAC API queries, Titiler rendering

---

## Success Criteria

- [ ] Performance: <1.5h full run, <15min weekly incremental
- [ ] Reliability: 95%+ success rate over 8 weeks
- [ ] Data quality: 100% items validated before registration
- [ ] Automation: 4+ consecutive weeks without manual intervention
- [ ] Auditability: Complete benchmark logs and execution history
- [ ] Cost: $0 additional infrastructure
- [ ] Maintainability: Documentation, rollback procedure, troubleshooting guide

---

## Current Focus

**Active task:** Phase 2 complete - Ready for Phase 3 planning
**Next:** Phase 2.3 (handle deletions) or migrate to Phase 3 (VM automation)

---

## Critical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Parallel execution | ThreadPoolExecutor (not multiprocessing) | Avoids rasterio threading issues |
| Validation caching | Pre-validate all (frontload cost) | Faster iterations, skip invalid files |
| Spatial extent | Hardcoded BC bbox (not calculated) | BC boundary stable, saves 20min (see issue #4) |
| Infrastructure | awshak OpenTofu for S3, manual VM | S3 IaC-ready, VM migration future |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| VM failure | Health checks, manual backup process |
| Provincial directory format change | Error handling, graceful exit |
| PgSTAC registration failure | Chunked registration (1000/chunk), retry logic |
| Validation cache corruption | Monthly full rebuild, version control |

---

## SRED Tracking

- **Primary:** [NewGraphEnvironment/sred-2025-2026#8](https://github.com/NewGraphEnvironment/sred-2025-2026/issues/8) (Performance Benchmarking)
- **Milestone:** [Automated Weekly Updates & Performance Modernization](https://github.com/NewGraphEnvironment/stac_dem_bc/milestone/1)
- **Issues:**
  - [#5](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/5) - Automated Weekly Updates (tracking issue)
  - [#4](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/4) - Optimize spatial extent
  - [#6](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/6) - Structured logging and benchmarking
  - [#7](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/7) - Migrate .qmd to standalone scripts
  - [#8](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/8) - Investigate parenthesis files (90 excluded)

---

## Errors Encountered

| Error | Phase | Resolution |
|-------|-------|------------|
| _(None yet)_ | | |

---

**Last updated:** 2026-02-01 (Phase 2 complete)
