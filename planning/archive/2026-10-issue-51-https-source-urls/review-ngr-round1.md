# Review: ngr#38 diff (ngr_s3_keys_get join), round 1

## Findings

- **[severity: bug, cross-repo]** `stac_orthophoto_bc/stac_create_item.qmd:136,142,148`. This is not a defect in the diff's own lines. It is a caller that breaks when it gets the corrected output, and nothing pins it to the old ngr. `stac_orthophoto_bc` has no DESCRIPTION, and `environment.yaml` does not mention ngr, so it runs whatever ngr is installed on the machine. After this fix is installed, re-running its `stac_create_collection.qmd` writes `https://` URLs into `data/urls_list.txt`. `stac_create_item.qmd` then does two wrong things with them:
  - `path_item.replace("https:/", "https://")` (lines 136 and 142) changes a correct `https://` URL into `https:///nrs.objectstore...`. That value is written as the asset `href`, and the host is empty.
  - `item_id = path_item[len(path_s3):]` (line 148) was written to cut an `https:/` URL, which is one character shorter. On an `https://` URL it leaves the leading `/` in place, so every id gains a leading `-`: `-082-082e-2018-orthophoto-...` instead of `082-082e-2018-orthophoto-...`.

  Measured with python3 on one URL in each form. A rebuild would mint a second, differently-named copy of every orthophoto item with broken hrefs, alongside the published ones. This needs a guard or fix in stac_orthophoto_bc before the ngr fix reaches any machine that runs that repo. The idempotent `fix_url` plus `.lstrip("/")` that stac_dem_bc uses is the shape to copy.

- **[severity: fragile, for #51's own scope; not ngr]** `stac_dem_bc/stac_create_item.qmd:231,247` has the same non-idempotent `replace("https:/", "https://")`. Line 247 passes count=1, and that still turns `https://` into `https:///`. Line 257 does `.lstrip("/")`, so ids are safe there. The `.py` path (`stac_utils.fix_url`, `item_id_from_url`, `dsm_pair.key_relative`) is idempotent and safe. The `.qmd` is not referenced by `update.yml`. It still breaks if anyone renders it after the cache migration. stac_dem_bc pins ngr by SHA in `DESCRIPTION:16`, so the new ngr reaches it only when that pin is bumped, and the bump has to land in the same commit as the cache rewrite (#51).

## Checked and fine

- **The fs_path to character change.** No caller relies on the `fs_path` class. Every caller only `as.character()`s, `str_detect()`s, `unique()`s, `write_lines()`s or takes `length()` of the result: `urls_listing.R`, `benchmark_fetch.R`, `stac_create_collection.qmd` in both repos.
- **The empty-length guard is needed.** `paste(x, character(0), sep = "/")` returns `"x/"`. The old `fs::path(x, character(0))` returned length 0, so the guard keeps the old behaviour.
- **NAMESPACE is unchanged and correct** after the `@importFrom fs path` line was removed. `importFrom(fs,path)` is still generated from `ngr_s3_dl.R`, `ngr_s3_files_to_index.R` and `ngr_fs_id_missing.R`, and `fs` is still used, so `DESCRIPTION` Imports stays valid.
- **Mocking is valid.** The function calls `httr::GET`, `httr::status_code` and `httr::content`, all namespaced, so mocking the httr namespace with `.package = "httr"` intercepts them. `.env = parent.frame()` is a supplied argument, so it is evaluated in the helper's frame and resolves to the test_that environment, which scopes the mocks correctly. Ran in a copy: the 3 mocked tests pass. With `NOT_CRAN=true` the live test also passes. Under a bare `test_file()` it skips as "On CRAN", which is expected, and `devtools::test()` sets `NOT_CRAN`.
- **Mocking cannot leak.** The mocks are scoped to each test_that block, and the live test runs unmocked in its own block.

## Verdict on the ngr diff itself

Clean. The single real issue is the unpinned downstream caller in stac_orthophoto_bc, above.
