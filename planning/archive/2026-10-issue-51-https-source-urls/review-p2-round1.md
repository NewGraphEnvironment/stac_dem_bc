# Review: #51 Phase 2 (refusal guard), round 1

Reviewed: staged diff (scripts/detect_changes.R, scripts/urls_listing.R,
tests/test_urls_scheme.py, planning/active/task_plan.md), scripts/urls_fetch.R,
.github/workflows/update.yml, DESCRIPTION, issue #51, ngr branch
`38-ngr-s3-keys-get-returns-https-host`. All mutation work done in a scratch copy;
the repo tree was not modified.

## Findings

- **[fragile]** tests/test_urls_scheme.py:50-56 (with scripts/urls_listing.R:63) — the
  real walk guard `urls_scheme_assert(all_urls, "bucket walk")` in
  `urls_listing_fetch()` is not tested. The fixture replaces `urls_listing_fetch`
  wholesale and calls `urls_scheme_assert` itself (line 53), so
  `test_single_slash_listing_is_refused` asserts the fake's own call site.
  Measured in a copy: deleting line 63 of the real `urls_listing.R` leaves all
  5 tests green. That is the guard covering both callers (`detect_changes.R` and
  `urls_fetch.R`) and the one that stops an old ngr from coming back after the
  migration. The helper docstring's claim that the replacement "runs the shipped
  guard on its listing exactly where the real walk does" is the false part: the
  function is shipped, the call site is a copy. One way to fix it: have the
  fixture call the real function with only the network replaced. For example,
  give `urls_listing_fetch` a `keys_get = ngr::ngr_s3_keys_get` argument and
  have the override be
  `function(url_bucket = URL_BUCKET, keys_min = KEYS_MIN) .real(url_bucket, keys_min = 0, keys_get = function(...) <fixed vector>)`.
  The fixture keys (`093/093l/2019/dsm/...`) already meet the `dsm_groups`
  shape regex and the non-zero DEM/DSM asserts.

## Checked and fine

- Mutation results for the other guards (in a copy): removing the cache
  assert (detect_changes.R:62) fails test 2. Neutering the dsm/deleted
  loop (detect_changes.R:72-74) fails tests 4 and 5. Those fixtures reach the
  failure mode: without the guards each would exit 1 or 0.
- All refusals run before the first write (line 104), inside the tryCatch, so
  they give exit 2 and leave every file untouched. `source()` of urls_listing.R
  is inside the tryCatch too.
- `startsWith(NA, ...)` is NA, so `urls[!NA]` puts an NA into `bad` and the guard
  fails closed (it prints `'NA'`). `readr::read_lines` uses `na = character()`, so
  it never produces NA. An empty or missing file gives `character(0)` and passes,
  which matches the previous behaviour.
- Real caches: none of urls_list.txt, urls_dsm.txt or urls_deleted.txt has a
  blank line, a CR, a BOM, a line not starting with `https:/`, or a
  `https:///` line, and each ends in `\n`. So once the scheme migration lands,
  the guard will not fire on these files. (urls_list.txt has 90 lines
  containing spaces. ngr's fix joins with `paste()` and does not encode, so
  their spelling does not change.)
- The scheme is the only spelling difference `fs::path()` introduces that
  matters here, measured rather than assumed. `fs::path()` also collapses an
  interior `//` and strips a trailing `/`, and neither the guard nor
  `urls_scheme_migrate.py` would catch that. A full bucket walk today
  (575,438 keys, 221 s) found **0** keys containing `//` or starting with `/`.
  Trailing-slash keys are not `.tif`, so they never enter the DEM/DSM sets.
  `dsm_groups` is derived through `sub()` and comes out the same either way.
- Gap between commits: with the current ngr pin (519c03b, pre-fix), the walk
  guard makes `detect_changes.R` exit 2 and `urls_fetch.R` stop, until Phase 3.
  update.yml has only `schedule` and `workflow_dispatch` triggers, which run
  from the default branch only, so this branch is never run by the workflow.
  This is not a problem as long as Phase 3 lands in the same PR.
- R string interpolation in the test helper is safe for the fixed constant
  URLs (no quotes or backslashes), and `r_vec(...)[2:-1]` yields a correct
  argument list.
- CI: pytest runs after setup-r and setup-r-dependencies (DESCRIPTION
  imports fs, readr). The tests need no ngr and no network.
