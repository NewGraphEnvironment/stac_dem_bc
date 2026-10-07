# Code-check — Phase 4 (#51), round 2: semantics and tests

Reviewed the staged index (exported with `git checkout-index` to a scratch copy, made a
git repo there so `test_urls_scheme.py`'s `git ls-files` works). Nothing in the repo was
touched except this file.

## Clean

No bugs, security issues or data-loss paths found in the focus areas.

### Evidence

1. **`load_validation_cache` equivalence: measured, not reasoned.** Ran HEAD's
   `item_create.py`/`stac_utils.py` and the staged ones side by side against the real
   `data/stac_geotiff_checks.csv` (102,460 rows, all `str`, 0 NaN, 0 not `https://`,
   0 `https:///`, 0 with surrounding whitespace). `geotiff_extract_metadata` was mocked.
   The input was 5,000 random `urls_list` lines, 500 `urls_invalid_items` lines, 50
   duplicates, one URL not in the cache and one `""`. The extraction-call set, the
   returned lookup (102,462 entries) and the rewritten CSV bytes were all identical (same
   sha256 for each). Both versions take the 58,019-row `needs_upgrade` branch, because
   `is_geotiff & transform.isna()` holds there, so the drop and `isin` path was exercised
   too.
   - dtype note, not a bug: `fix_url(NaN)` raised `AttributeError`. The new
     `set(df["url"])` and `isin` accept a NaN url, and it never matches anything. That is
     more tolerant and harmless. The cache has no NaN today.
   - Already true at HEAD and unchanged: an internal blank line in a urls file reaches
     `geotiff_extract_metadata("")` and appends a `url=""` row to the cache, which reads
     back as NaN. HEAD would then crash on that NaN in the next run. The staged code
     ignores it.
2. **`url_scheme_check` edge cases** (probed):
   - `""`, a bare key, `" https:/x"`, `http:/x`, `HTTPS:/x` and `https:///…` all pass
     through.
   - `https:/` raises. NaN raises `AttributeError`.
   - `https:///…` is let through, and `tile_key_parse` would turn it into a garbage
     group. Nothing produces it now. Its only producer was the qmd's
     `.replace("https:/", "https://")`, which wrote in-memory keys only and is removed in
     this diff. No `data/` file holds it, and ngr 0.0.3 writes `https://`.
3. **`splitlines()` + list comprehension** (`item_create.py:332`,
   `item_reprocess.py:183`):
   - Blank lines behave as before: `url_scheme_check("") == ""`, so they flow
     downstream as they did with `fix_url`.
   - `--test --test-count` still slices the same list. The one difference is that a
     one-slash line *beyond* the first N now raises in test mode. That is the intended
     loud failure.
4. **The tests reach the guard.** `pytest tests/ -q` gives 209 passed. Mutation table:
   | mutation | result |
   |---|---|
   | `url_scheme_check` repairs instead of raising | 3 new tests fail |
   | `url_scheme_check` is a no-op | the same 3 fail |
   | guard removed from `item_ids_from_urls` only | `test_item_ids_from_urls_refuses_a_one_slash_line` fails |
   | guard removed from `keys_load` only | `test_keys_load_refuses_a_one_slash_listing` fails |
   The register_manifest test cannot pass on the foreign-host branch: that message
   (`URL is not on the objectstore …`) does not contain `one-slash`, so `match=` tells
   the two apart.
5. **`stac_create_item.qmd`**: all 3 python chunks parse (`ast.parse`). No reference to
   the removed variables, `fix_url` or `replace("https:/"` remains in the qmd or in
   `scripts/`.
6. **The CLAUDE.md paragraph matches the code**:
   - `detect_changes.R` exits 2 (`quit(status = 2)`; tests assert `returncode == 2`).
   - `stac_utils.url_scheme_check` exists, and the Python loaders call it.
   - `tests/test_urls_scheme.py::test_no_tracked_data_file_holds_a_one_slash_url` exists
     and scans tracked text files under `data/`.
   - "every file under `data/`" also holds for the one binary file the test skips:
     `stac_result.rds` has 0 one-slash and 387 `https://` strings, read with R.

### Outside this round's focus (for the boundary reviewer, not a finding here)
- `stac_create_item.qmd:133` reads `urls_file` raw, with no `url_scheme_check`. A stale
  one-slash list would miss every lookup and print "Skipping unreadable GeoTIFF" for
  every item rather than raise. The qmd is legacy and is not used by `update.yml`.
- `dsm_verify.py:151` builds its lookup from the cache without a check. A one-slash cache
  would shrink `comparable` and end in `return 1`, never a pass, so it fails toward
  error.
