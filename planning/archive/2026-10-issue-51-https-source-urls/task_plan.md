# Task: Store source URLs as `https://` — migrate the caches, then retire `fix_url` at the read sites (#51)

## Problem

Source URLs reach `data/` as `https:/host/...` because of NewGraphEnvironment/ngr#38 (draft 1). The code
repairs them each time it reads them (`stac_utils.fix_url`, about 20 call sites), and the
committed caches now hold **both** forms (measured 2026-10-06):

| file | `https:/` | `https://` |
|---|---|---|
| `urls_list.txt` | 102,416 | 0 |
| `urls_dsm.txt` | 95,889 | 0 |
| `urls_deleted.txt` | 43 | 0 |
| `stac_geotiff_checks.csv` | 102,520 | 2,245 |
| `urls_access_checks.csv` | 0 | 4,420 |
| `urls_invalid_items.txt` | 0 | 2,245 |

One fact is spelled two ways. Joins between these files only work while every code path
remembers to normalise first.


## Measured 2026-10-06 (tracked files in `data/`)

| file | `https:/` | `https://` | action |
|---|---|---|---|
| `urls_list.txt` | 102,416 | 0 | rewrite |
| `urls_dsm.txt` | 95,889 | 0 | rewrite |
| `urls_deleted.txt` | 43 | 0 | rewrite |
| `stac_geotiff_checks.csv` | 102,520 | 2,245 | rewrite + dedupe → 102,460 rows (approved) |
| `urls_access_checks.csv`, `urls_invalid_items.txt` | 0 | all | none |
| `dem_dsm_pairs.csv`, `dsm_groups.txt`, `stac_result.rds`, others | 0 | — | none (bucket-relative keys / API docs) |

`stac_geotiff_checks.csv`: 104,765 rows, **102,460 distinct URLs after normalising**.
2,255 URLs appear more than once — the 2,245 albers tiles in both spellings plus 10 raw
duplicates — and **every duplicate's other columns are identical**, so dedupe is lossless.

Side finding: `detect_changes.R:112` cites "4,490 albers rows against 2,245 live URLs"
as evidence for #28's prefix-rename hypothesis. Those 4,490 are the same 2,245 URLs
spelled twice — a #51 artifact, not a rename. (The 43 `albers10k2m_new/` deletions are
still real evidence.) The comment gets corrected.

## Phase 1: ngr fix (in `~/Projects/repo/ngr`, its own branch + PR)
- [x] Branch `38-...` off ngr main
- [x] `R/ngr_s3_keys_get.R:77`: `paste(sub("/+$", "", url_bucket), all_keys, sep = "/")`, plus an empty-keys guard (`paste(x, character(0), sep = "/")` is `"x/"`)
- [x] testthat: output starts with `https://` (live walk on a small prefix, `skip_if_offline()`), and a trailing `/` on `url_bucket` does not double the separator
- [x] Open PR closing ngr#38 (no NEWS/version edit — fledge-managed; `/gh-pr-merge` bumps to 0.0.3)
- [x] Merge + tag `v0.0.3` via `/gh-pr-merge` once CI is green (approved)

## Phase 2: refusal guard (stac_dem_bc, branch `51-store-source-urls-as-https-...`)
- [x] `scripts/urls_listing.R`: `urls_scheme_assert(urls, what)` — stop() naming the count and first offender if any URL is not `^https://`
- [x] Call it in `urls_listing_fetch()` on all walked URLs (covers both callers: `detect_changes.R` and `urls_fetch.R`)
- [x] Call it in `detect_changes.R` on the cached `urls_list.txt` / `urls_dsm.txt` / `urls_deleted.txt` **before** any `setdiff` or write, inside the tryCatch → exit 2, never exit 1
- [x] pytest `tests/test_urls_scheme.py` driving Rscript (CI already installs R before pytest): guard raises on `https:/`, passes on `https://`; restore-the-bug check that it fires
- [x] Correct the `detect_changes.R` albers comment

## Phase 3: pin + migrate — ONE commit
- [x] `DESCRIPTION`: `Remotes: NewGraphEnvironment/ngr@v0.0.3`, `Imports: ngr (>= 0.0.3)` (replaces the bare-SHA pin)
- [x] `scripts/urls_scheme_migrate.py` (one-shot, kept for provenance): line-level `^https:/(?=[^/])` → `https://` on the four files; byte-preserving otherwise
- [x] Asserts before writing: per-file line count unchanged (txt); `{fix_url(old)} == {new}` per file; zero single-slash lines anywhere in `data/`; for geotiff checks, 102,460 rows out and every dropped row equal to the row it keeps
- [x] Run it, commit pin + four rewritten caches + script together
- [x] Data-invariant pytest: no tracked text file under `data/` contains `https:/[^/]`

## Phase 4: retire `fix_url` at the read sites
- [x] Replace the silent repair with a raising check where URLs enter from files a human can hand-write (`register_manifest.py` ids-from-urls, `urls_check_access.py`, `item_create.py --urls-file`), so a stray `https:/` fails loudly instead of being repaired
- [x] Drop the internal calls (`stac_utils.py` 192/275/381, `dsm_pair.py`, `dsm_verify.py`, `item_create.py` cache comparisons, `item_reprocess.py`) and the comments explaining the two forms
- [x] `stac_create_item.qmd:231` `.replace("https:/", "https://")` is not idempotent (turns `https://` into `https:///`) — drop it with the rest (found by ngr code-check round 1)
- [x] Delete `fix_url` and `test_cache_lookup_normalises_both_url_forms`; update `register_manifest.py` docstring
- [x] CLAUDE.md "Source URLs are stored as `https:/`" paragraph → one line saying they are `https://` and guarded

## Validation

- [x] `pytest tests/ -q` green; ngr `devtools::test()` green
- [x] Two-version walk (plan review AC1): list the bucket with ngr 519c03b and with 0.0.3 (scratch R library); require `fix(old) == new` over all keys, and identical DEM/DSM/dsm_groups after normalising
- [x] Phase 4 href byte-identity: items built from cached URLs before and after Phase 4 are byte-identical
- [x] Tests pass
- [x] `/code-check` clean on each commit
- [x] PWF checkboxes match landed work
- [x] `/planning-archive` on completion
