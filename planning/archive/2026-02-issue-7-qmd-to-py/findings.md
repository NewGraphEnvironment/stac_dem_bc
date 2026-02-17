# Findings: Issue #7 - .qmd to .py Migration

## Source Analysis

### stac_create_item.qmd (~350 LOC)
- **R dependency:** Only the conda env activation (reticulate) — not needed in standalone .py
- **Python chunks:** imports, date extraction, config, pre-validation, parallel item creation
- **Modes:** test_only, incremental, reprocess_invalid (3 boolean flags)
- **Workers:** 32 threads (ThreadPoolExecutor)
- **Dangling R chunk:** `json-clean` at bottom (eval=FALSE, manual delete utility) — don't migrate

### stac_create_collection.qmd (~270 LOC)
- **R dependency:** `ngr::ngr_s3_keys_get()` for S3 key fetch — REAL dependency, keep in R
- **Python chunks:** imports, bbox functions, config, temporal extent, collection creation, validation
- **R→Python bridge:** `test_only` and `test_number_items` passed via `r.test_only`
- **Unused code:** `bbox_combined()` function defined but commented out (hardcoded bbox used instead)

### Duplicated Functions (3 copies each)
1. `date_extract_from_path()` — in item.qmd, collection.qmd, item_reprocess.py
2. `datetime_parse_item()` — in item.qmd, collection.qmd, item_reprocess.py
3. `check_geotiff_cog()` — only in item.qmd (but could be shared)

### Config Constants (hardcoded in multiple files)
- `path_local` (dev/prod) — in item.qmd, collection.qmd, item_reprocess.py, catalogue_qa.py
- `path_s3_stac` — in item.qmd, item_reprocess.py
- `path_s3` — in item.qmd, item_reprocess.py

## Key Design Decisions

- **fetch_urls.R stays in R:** No Python equivalent for `ngr::ngr_s3_keys_get()`. The R→Python handoff point is `data/urls_list.txt` (a text file), which is a clean interface.
- **argparse for CLI:** All .py scripts should accept `--test`, `--test-count`, mode flags via CLI args instead of editing source code.
- **logging module:** Replace `print()` statements with `logging.info()` etc. for proper log capture in cron.

---

**Last updated:** 2026-02-17
