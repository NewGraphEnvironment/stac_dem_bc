# Task Plan: Migrate .qmd to Standalone Scripts (Issue #7)

**Status:** Phase 2 - In Progress
**Branch:** `7-migrate-qmd-to-py`
**Started:** 2026-02-17
**Issue:** https://github.com/NewGraphEnvironment/stac_dem_bc/issues/7

## Goal

Migrate `stac_create_collection.qmd` and `stac_create_item.qmd` to standalone scripts for production automation. Keep .qmd files as archive/reference. Enable clean `python script.py` and `Rscript script.R` execution for Phase 3 VM cron jobs.

## Naming Convention

Scripts use `noun_verb.py` pattern for alphabetical grouping:

| Old Name | New Name |
|----------|----------|
| `validate_stac_items.py` | `item_validate.py` |
| `reprocess_invalid_items.py` | `item_reprocess.py` |
| `extract_invalid_urls.py` | `item_extract_invalid.py` |
| `qa_update_catalogue.py` | `catalogue_qa.py` |
| _(new)_ | `item_create.py` |
| _(new)_ | `collection_create.py` |

## Current State

**Existing .py scripts (renamed):**
- `scripts/item_validate.py` (244 LOC)
- `scripts/item_reprocess.py` (196 LOC) — updated to use stac_utils
- `scripts/item_extract_invalid.py` (89 LOC)
- `scripts/catalogue_qa.py` (243 LOC)
- `scripts/stac_utils.py` (NEW — shared utilities)
- `scripts/build_safe.sh` (218 LOC)

**Still in .qmd (need migration):**
- `stac_create_collection.qmd` — R chunk (S3 key fetch via `ngr`) + Python chunks (collection creation)
- `stac_create_item.qmd` — R chunk (conda env) + Python chunks (validation, item creation)

**R-only scripts (keep as-is):**
- `scripts/detect_changes.R` — Uses `ngr::ngr_s3_keys_get()`, pure R is correct
- `scripts/s3_sync.R` — AWS CLI wrapper, fine as R
- `scripts/functions.R` — `vm_upload_run()` utility
- `scripts/benchmark_fetch.R` — Dev tool
- `scripts/footprint_visualize.R` — Exploratory (Issue #2)

## Phases

### Phase 1: Extract shared utilities ✅ COMPLETE
**Goal:** Create shared Python module to eliminate duplication

- [x] **1.1** Create `scripts/stac_utils.py` with shared functions
- [x] **1.2** Update `item_reprocess.py` to import from `stac_utils.py`
- [x] **1.3** Verify imports and syntax
- [x] **1.4** Rename all scripts to `noun_verb.py` convention
- [x] **1.5** Update all cross-references (scripts, CLAUDE.md, .qmd)

---

### Phase 2: Migrate stac_create_item.qmd → scripts/item_create.py ⬜ pending
**Goal:** Standalone Python script for item creation

- [ ] **2.1** Create `scripts/item_create.py` with:
  - argparse CLI (`--test`, `--test-count N`, `--incremental`, `--reprocess-invalid`)
  - Python logging module (not print statements)
  - Import shared utils from `stac_utils.py`
  - All functionality from stac_create_item.qmd Python chunks
- [ ] **2.2** Test equivalence: run both .qmd and .py, compare output
- [ ] **2.3** Update `scripts/build_safe.sh` to call .py instead of `quarto render`

**Verify:** Create 10 test items with .py script, diff against .qmd output

---

### Phase 3: Migrate stac_create_collection.qmd → split R/Python ⬜ pending
**Goal:** Standalone scripts for collection creation

The collection .qmd has two distinct parts:
1. **R chunk:** Fetches S3 keys via `ngr::ngr_s3_keys_get()` → `data/urls_list.txt`
2. **Python chunks:** Creates collection JSON from urls_list.txt

Migration approach:
- [ ] **3.1** Create `scripts/urls_fetch.R` — Standalone R script for S3 key fetching
  - Takes `--test` flag, outputs to `data/urls_list.txt`
  - Replaces the R chunk in collection.qmd
- [ ] **3.2** Create `scripts/collection_create.py` — Standalone Python script
  - Reads `data/urls_list.txt` (produced by urls_fetch.R or detect_changes.R)
  - argparse CLI (`--test`, `--test-count N`)
  - Temporal extent calculation, spatial extent (hardcoded BC bbox)
  - Collection creation and validation
- [ ] **3.3** Test equivalence: compare collection.json from both approaches

**Verify:** `Rscript scripts/urls_fetch.R && python scripts/collection_create.py` produces identical collection.json

---

### Phase 4: Update build_safe.sh and documentation ⬜ pending
**Goal:** Wire everything together for production

- [ ] **4.1** Update `scripts/build_safe.sh` to use new scripts:
  - `Rscript scripts/urls_fetch.R` (or `Rscript scripts/detect_changes.R`)
  - `python scripts/collection_create.py`
  - `python scripts/item_create.py`
  - `python scripts/item_validate.py`
- [ ] **4.2** Archive .qmd files (move to `archive/` or add deprecation header)
- [ ] **4.3** Update CLAUDE.md with new script paths and workflow
- [ ] **4.4** Update README.md usage examples

**Verify:** Full `build_safe.sh` run in test mode produces valid catalog

---

## Critical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Naming convention | `noun_verb.py` | Groups related scripts alphabetically (item_*, collection_*) |
| Keep R for S3 key fetch | Yes | `ngr::ngr_s3_keys_get()` has no Python equivalent, already works |
| Shared Python module | `stac_utils.py` | Eliminates 3x duplication of date functions |
| CLI interface | argparse | Standard, scriptable, supports `--test` flags |
| Logging | Python `logging` module | Proper log levels, file output, captures in cron |
| Archive .qmd | Keep in repo (header note) | Reference for literate programming approach |

## Risks

| Risk | Mitigation |
|------|-----------|
| Breaking production pipeline | Branch-based development, .qmd still works on main |
| ngr R dependency hard to replace | Keep R script for S3 fetch, don't force all-Python |
| Subtle behavior differences | Side-by-side output comparison before merging |

## SRED Tracking

- Primary: NewGraphEnvironment/sred-2025-2026#8
- Secondary: NewGraphEnvironment/sred-2025-2026#3

---

## Errors Encountered

| Error | Phase | Resolution |
|-------|-------|------------|
| PROJ env conflict | 1.3 | Local homebrew/conda conflict — not our bug, works on VM |

---

**Last updated:** 2026-02-17
