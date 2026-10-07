# Review: Phase 2 (#51), round 2 — defects inside the round-1 fixes

## Clean

No bugs, security issues or data-loss paths found in the staged diff
(`scripts/detect_changes.R`, `scripts/urls_listing.R`, `tests/test_urls_scheme.py`).

### The four questions asked

1. **`keys_get = ngr::ngr_s3_keys_get` default, both real callers.** Identical behaviour.
   R evaluates the default lazily at the `keys_get(...)` call, which is the same point
   the old literal `ngr::ngr_s3_keys_get(...)` was evaluated, with the same named
   arguments. Neither `urls_fetch.R:38` nor `detect_changes.R:57` passes a third
   argument, positionally or by name. A missing ngr still raises at that point: inside
   detect_changes' tryCatch (exit 2), and at top level in urls_fetch.R, as before.
2. **`keys_min = 0` in the test.** It disables only the 280k floor, which no test claims
   to cover. The scheme assert runs *before* the floor (`urls_listing.R:66` vs `:68`), so
   the floor cannot pre-empt the walk guard in production either, and the test's
   `"bucket walk" in r.stdout` cannot be satisfied by the floor's own "bucket walk
   returned" message, because the floor is unreachable at 0. The DEM/DSM-nonzero and
   group-shape guards still run for real on the fixture keys.
3. **The CI skip.** GitHub Actions sets `CI=true` on every runner, so
   `os.environ.get("CI")` is truthy and the mark never skips there. In update.yml, pytest
   (line 95) runs after `setup-r` and `setup-r-dependencies`, which install fs, readr and
   ngr from DESCRIPTION, so the tests have what they need. If R were ever missing in CI,
   `subprocess.run([None, ...])` raises TypeError, which fails loudly rather than skipping.
4. **The R string built by the f-string.** Correct. `\\n` becomes `\n` in the Python
   string and so `cat("WALK REACHED\n")` in the R source. `{{`/`}}` produce the function
   braces, and the fixture URLs contain no `"` or `\`.

### Verified by running (in a copy at scratchpad/p2r2, the repo untouched)

- All 5 tests pass (2.4 s, local R).
- Mutation table. Each mutation turned at least one test red:

  | mutation | result |
  |---|---|
  | drop `deleted_file` from the Step 0 loop | deleted-trail test fails |
  | remove Step 0 entirely | DEM-cache, DSM-cache and deleted-trail tests fail (3 of 5) |
  | move Step 0 after the walk | DEM-cache test fails on `WALK REACHED` |

  The walk-guard deletion was already verified by the caller.
- The committed caches (`urls_list.txt`, `urls_dsm.txt`, `urls_deleted.txt`) have no
  empty lines and end with a newline, and every line starts with `https:/`. After
  Phase 3's rewrite, Step 0 will see no blank or NA line that could false-refuse.
  `startsWith(NA, ...)` would refuse rather than pass, so it fails safe in any case.
