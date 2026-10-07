# Review: ngr#38 diff, round 3 (packaging and CI)

Reviewed: staged diff in /Users/airvine/Projects/repo/ngr (R/ngr_s3_keys_get.R,
man/ngr_s3_keys_get.Rd, tests/testthat/test-ngr_s3_keys_get.R).

## Findings

- **[fragile]** tests/testthat/test-ngr_s3_keys_get.R:11 — `testthat::local_mocked_bindings()`
  first appeared in testthat 3.1.7 (installed NEWS.md, line 286), but DESCRIPTION
  declares `testthat (>= 3.0.0)`. It is the first use of the mocking API in the
  package, so nothing else already raised the floor. On a machine with testthat
  3.0.0 to 3.1.6, the three offline tests fail with "could not find function"
  instead of testing the join. Fix: `testthat (>= 3.1.7)` in Suggests.
  Low practical risk, because 3.3.2 is installed here and no workflow runs the tests.

No other findings in this diff.

## What was checked (no defect)

- **CI:** `.github/workflows/` has pkgdown, claude, claude-code-review and
  update-citation-cff. There is **no R-CMD-check workflow**, so nothing in CI runs
  these tests. pkgdown runs examples, not tests.
- **NAMESPACE:** removing `@importFrom fs path` from this file does not change
  NAMESPACE. `importFrom(fs,path)` is still declared by ngr_s3_files_to_index.R,
  ngr_s3_dl.R, ngr_fs_id_missing.R and ngr_fs_copy_if_missing.R. Re-running
  `devtools::document()` in a copy left NAMESPACE and man/ byte-identical to the
  staged tree.
- **Undeclared test deps:** the tests call only `testthat::`. `skip_if_offline()`
  needs curl, which is in Imports. Under `R CMD check`, "checking for unstated
  dependencies in 'tests'" passed.
- **Live test:** `skip_on_cran()` together with `skip_if_offline("nrs.objectstore.gov.bc.ca")`
  is correct. Under plain `R CMD check` (NOT_CRAN unset) it skips. Under
  `devtools::check()`/`devtools::test()` (NOT_CRAN=true) it ran against the live
  objectstore and passed.
- **Mocking under check:** `.package = "httr"` rebinds httr's namespace, which the
  function's `httr::GET` calls resolve through. The offline tests pass against the
  installed package under `R CMD check`, not only under `load_all`.
- **Guard proof:** in a copy, putting back `fs::path(url_bucket, all_keys)` made 4
  of 5 expectations fail (lines 23, 32, 38, 50). The tests discriminate.
- **`devtools::check(document = FALSE, args = "--no-manual")` on a copy of the
  staged tree:** 2 ERRORs and 6 NOTEs. None of them is caused by this diff:
  - examples ERROR in `ngr_s3_files_to_index` ("The input must start with 's3://'").
    This is pre-existing, and it halts the examples before `ngr_s3_keys_get`'s
    network-hitting example runs. That example is also pre-existing and unchanged
    by the diff.
  - tests ERROR in test-ngr_spk_deprecated.R:53-54 (spacehakr shim targets, 0 of
    12). This file is untouched by the diff.
  - All the test-ngr_s3_keys_get.R tests passed under check: `[ FAIL 2 | PASS 203 ]`,
    and both failures are the spk ones.
  - NOTEs are pre-existing: .claude hidden dir, CITATION.cff, no visible
    bindings, Rd line widths.

Log: /private/tmp/claude-501/-Users-airvine-Projects-repo-stac-dem-bc/cc8e4fa9-34ca-4a6f-9561-16422dda0d2c/scratchpad/ngr_check.log
