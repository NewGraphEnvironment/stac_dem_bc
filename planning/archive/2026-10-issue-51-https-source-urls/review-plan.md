# Plan review (Plan agent, 2026-10-06) — triage

Read-only agent; findings relayed here by the parent session.

| id | finding | disposition |
|---|---|---|
| B1 | ngr 0.0.3 breaks stac_orthophoto_bc item qmd (non-idempotent replace, id slice) | Filed stac_orthophoto_bc#48; note in ngr PR/NEWS |
| G1 | legacy `stac_create_item.qmd:231` same replace; `stac_create_collection.qmd` writes urls_list.txt bypassing the guard | qmd:231 folded into Phase 4; collection qmd writes correct URLs once ngr is fixed — left, noted |
| G2 | `test_register_manifest.py:47` and `test_dsm_pair.py:231` assert single-slash acceptance | Phase 4: invert to raises |
| G3 | checks at three hand-picked entry points leave silent paths (item_create all modes, item_reprocess, dsm_pair key_relative/tile_key_parse) | Phase 4: one shared helper at every file-read point + in key parsing |
| G4 | `item_create.py:105` / `stac_utils.py:381` are the href sites | Phase 4 names them; byte-identity check |
| G5 | migration script would import fix_url | Not applicable — script has its own transform |
| G6 | cache guard should run before the bucket walk | Done: Step 0 in detect_changes.R, test asserts the walk is not reached |
| G7 | data-invariant test must skip binary files (`stac_result.rds`) | Phase 3 test decodes as UTF-8 and skips non-text |
| O1 | Phase 2 guard fails against committed state until Phase 3 | Accepted: workflows run only from main and the PR merges whole; noted in commit message |
| O2 | NEWS/version bumped twice | No bump in the ngr PR; `/gh-pr-merge` does it |
| O3 | merge near the cron; local R library change | Merge well clear of the 3rd; ngr 0.0.3 into a scratch library for validation |
| A1 | findings claim that url_to_item_id drops a char is wrong | Verified wrong; findings corrected |
| A2 | fs::path also collapses inner `//` and trailing `/` in keys | Validation becomes a two-version walk (AC1) |
| A3 | plan Phase 1 text stale (empty guard) | Plan text updated |
| A4 | hrefs byte-identical for https:// input; rio_stac fallback may now build previously skipped items | Noted |
| A5 | no CI runs the new tests pre-merge; ngr has no R-CMD-check workflow | Local pytest + devtools::check before merge |
| S1 | benchmark_fetch.R, collection qmd call ngr directly | Left alone (benchmark/legacy) |
| S2 | urls_check_access is continue-on-error in CI | Noted; the R guard is the gate |
| AC1 | two-version walk comparison instead of "~0" | Adopted |
| AC2 | Rscript test must not skip silently in CI | Done |
| AC3 | href byte-identity check for Phase 4 | Adopted |
