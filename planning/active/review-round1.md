# Review round 1 — client-side pgstac registration (#27)

Branch `27-client-side-pgstac-registration` vs `main`. Reviewed in full:
`item_register.sh`, `collection_register.sh`, `catalogue_register.sh`,
`collection_unregister.sh`, `register_manifest.py`, `collection_patch.py`,
`collection_create.py`, both new test files, plus the `update.yml`, `scripts/README.md`,
`NEWS.md` and `CLAUDE.md` deltas. Every claim below was probed against the live API or
the live `collection.json`; nothing here is inferred from reading alone.

## Findings

- **[bug] scripts/catalogue_register.sh:243-249 — the final verification omits `limit`, so
  every run registering more than 10 items fails verification after the registration
  succeeded.**
  The inline verify block posts `{"ids": chunk, "fields": {...}}` with no `limit`. The API's
  default limit is 10, so each 500-id chunk returns at most 10 features regardless of how
  many exist. Reproduced verbatim against 600 ids that are *all* currently registered:

  ```
  requested 600, serving 20      # 2 chunks x 10
  MISSING: 580 -> script would exit 1
  ```
  ```
  # with "limit": len(chunk) added:
  requested 600, serving 600, missing 0
  ```
  Also confirmed at the boundary: a 12-id search with no `limit` returns 10.
  This is the "guard fails toward abort" class from CLAUDE.md, in its most expensive form —
  a `--drift` month of ~8k items, or an `--all` recovery of 102,460, does the whole upsert
  correctly and then exits 1 with `FAIL: N id(s) registered but not served`, which reads as
  a registration failure and invites a re-run of the 45-minute fetch.
  It shipped because the only exercise on record is the 3-item upsert in `scripts/README.md`,
  and 3 < 10 — a fixture that structurally cannot reach the failure mode.
  Note `register_manifest.py:101` gets this right (`"limit": page_size`); only the inline
  block is missing it. Fix: add `"limit": len(chunk)` to the POST body.

- **[bug] scripts/collection_patch.py:124-142 + .github/workflows/update.yml:136,171 —
  `--clear-version` is never called, but four places state that the monthly run calls it.**
  Both workflow invocations are bare `collection_patch.py --path "$STAC_OUTPUT_DIR/collection.json"`.
  `grep -rn 'clear-version'` over the repo (excluding `planning/`) returns exactly one hit,
  in `NEWS.md`. The claims that are now false:
  - `collection_patch.py:127` — *"Called by the monthly run"*
  - `collection_patch.py:174-176` (`--clear-version` help) — *"The monthly path uses this"*
  - `tests/test_collection_version.py:10` — *"clearing is what the monthly path does"*
  - `NEWS.md:57` — *"The monthly run clears it"*

  Consequence is the exact failure the feature was written to prevent: once a release stamps
  `version: X` into the published `collection.json`, the monthly run fetches that file,
  patches it (version handling is deliberately outside `collection_patch()`), appends items
  and republishes it **still carrying version X** against a catalogue that has grown. A wrong
  version says "you already have this one". The live collection has no `version` yet
  (verified), so the trap is armed by the *next* release, not by anything already published.
  The 31 tests pass because they test the function, not the wiring — the
  "running a generator is not committing what it generated" shape.

- **[bug] scripts/catalogue_register.sh:121-131 — `--verify` gates on `missing` only, while
  its own header, `NEWS.md` and `scripts/README.md` all claim set equality in *both*
  directions.**
  `register_manifest.py diff` does compute `orphaned` and print it to stderr, but nothing
  downstream consumes it: the exit code and the stdout verdict are derived solely from
  `$N_TODO` (= missing). A run with orphans therefore prints `orphaned 44` on stderr and
  `IN SYNC: every published item is registered` on stdout, and exits 0 — a verdict that
  contradicts the diagnostic three lines above it.
  This matters because orphans are the *expected* drift direction here: #28 (upstream-deletion
  pruning) is open and `NEWS.md` ships the 44-item gap as known debt. Measured today the
  catalogue is exactly in sync (102,460 registered, 102,460 published, 0 duplicates), so the
  omitted half of the gate has never been exercised.
  Either gate on orphans too, or narrow the three doc claims to what is actually enforced.

- **[fragile] scripts/catalogue_register.sh:89-90, 124-127, 133-136 — an empty published set
  is reported as success in every mode.**
  There is no `N_PUBLISHED -eq 0` branch. If `collection.json` fetches with a 200 but carries
  no item links (a truncated or wrong-shaped rebuild — `curl -f` cannot detect this), then
  `--verify` prints `IN SYNC: every published item is registered` and exits 0, and `--all`
  prints `nothing to register — already in sync` and exits 0, while the API holds 102,460
  items. Both are affirmative claims of success from a check that could not run — the
  "absence of evidence reported as evidence" case. The zero-drift branch at 133 is correctly
  reasoned; the zero-*input* branch is missing. One `[ "$N_PUBLISHED" -eq 0 ] && { echo ...; exit 1; }`
  after line 90 closes it.

- **[fragile] scripts/catalogue_register.sh:208 — `2>/dev/null` on the fan-out hides xargs'
  own fatal errors, leaving the one failure mode that produces no diagnostic at all.**
  Per-URL curl failures are captured to `failed.txt` by the worker, so the redirect is not
  needed for those. What it *does* suppress is xargs aborting the entire stream before any
  worker runs — a non-executable or missing `$FETCH_SCRIPT`, or `xargs: unterminated quote`,
  which I reproduced on this machine:
  ```
  printf "he'llo\n" | xargs -I {} echo "[{}]"   ->   xargs: unterminated quote
  ```
  In that case `failed.txt` stays empty (the worker never ran to append), and the operator's
  entire output is `fetched 0 of N (0 failed after 3 attempts)` plus a `head` of an empty
  file. Not live today — I checked the character set of all 102,460 published hrefs and it is
  `%()-.0-9_a-z` with zero quotes, backslashes or raw spaces — but the suppression is what
  makes it undiagnosable if a filename ever acquires an apostrophe. Drop the redirect, or
  capture xargs' stderr to a file and print it in the shortfall branch.

- **[fragile] scripts/register_manifest.py:110-116 — two different `break`s produce identical
  silent outcomes, one of which is a truncated result set.**
  Breaking on "no `next` link" (paging complete) and breaking on "`next` link present but its
  body carries no token" (server moved to href-based paging, or a proxy stripped the body) are
  indistinguishable from the caller. The second silently returns a partial `registered` set.
  Not data loss — every write path upserts — but `--verify` would then report a false DRIFT of
  ~92k and `--drift` would spend ~45 minutes re-fetching and re-registering items that are
  already there. I probed the live next link and it does carry
  `body.token = "next:stac-dem-bc:..."`, and a full enumeration returned all 102,460 ids with
  no duplicates, so this is a change-resilience gap rather than a present defect. Raising on
  "next link present but unusable" costs one line and converts a silent truncation into a
  named failure.

- **[fragile] scripts/collection_unregister.sh:86-91 — `$EXISTS` is not validated as numeric,
  unlike `$COUNT` sixteen lines above, and it fails toward the destructive branch.**
  `COUNT` gets a proper `[ "$COUNT" -eq "$COUNT" ]` guard with an explicit refusal. `EXISTS`
  gets none: `[ "$EXISTS" -eq 0 ]` on an empty or non-numeric value raises
  `integer expression expected` and evaluates **false** — and because it sits in an `if`
  condition, `set -e` is suspended and the script continues. It then prints
  `note: collection row exists with no items` (possibly untrue) and, under `--yes`, proceeds
  to the DELETE. The delete is harmless at `COUNT = 0`, but the guard whose entire job is to
  distinguish "already absent" from "exists but empty" cannot fail safe. Give it the same
  numeric check as `COUNT`.

- **[fragile] scripts/collection_create.py:119-120 — the stamp path defeats the
  extension-preservation contract that `version_stamp` is written and tested for.**
  ```python
  version_stamp(collection.extra_fields, args.version)
  collection.stac_extensions = list(collection.extra_fields.pop("stac_extensions"))
  ```
  `version_stamp` reads `stac_extensions` from `extra_fields`, which on a freshly-built pystac
  `Collection` is `{}`, so it always returns exactly `[VERSION_EXT]` — and line 120 then
  *assigns* that over `collection.stac_extensions` rather than merging. Nothing is lost today
  because the `Collection(...)` at line 104 declares no extensions, but
  `test_version_stamp_preserves_other_extensions` asserts a property that does not hold on the
  only caller in this file. Merging (`collection.stac_extensions = list(dict.fromkeys(collection.stac_extensions + [...]))`)
  or stamping through the collection dict would make the test's guarantee real.

- **[fragile] scripts/item_register.sh:24 — the usage line contradicts line 4 of its own
  header.** `scripts/item_register.sh --dryrun < ids.txt` implies ids are acceptable input;
  the script takes item JSON *paths* (line 4: "Item JSON paths arrive on STDIN"). Feeding it
  ids raises `FileNotFoundError` from `ndjson_write` — loud, but only after the operator has
  followed the documented invocation. Rename to `< item_paths.txt`.

## Checked and correct

Recording these so the next round does not re-derive them:

- **ARG_MAX.** Item paths reach `item_register.sh` on stdin and `catalogue_register.sh:230`
  uses `find | ...`, never a glob. Sound at 102,460.
- **Whitespace / word-splitting.** Traced every path- and id-carrying flow: `cat > "$PATHS"`
  then `while IFS= read -r line || [ -n "$line" ]`; md5-hashed fetch filenames (so no
  space ever reaches a shell word); tab-separated `hrefs.tsv` with `cut -f2`; `printf '%s\n'`
  in the worker. Nothing is word-split that should not be. `ndjson_write` handles
  `a (2).json`, and there is a test for it.
- **Injection.** `$DB` and `$COLLECTION_ID` are allowlisted (`*[!A-Za-z0-9_]*` /
  `*[!A-Za-z0-9_-]*|""`) *before* any interpolation into an ssh command string or SQL, and
  `$EXPECTED` is an integer computed by a counting loop. I could not construct a break-out.
  `POSTGRES_PASSWORD` is sourced on the host and passed via `PG*` env rather than `--dsn`,
  so it stays out of `ps aux`.
- **Preview flags.** All three `--dryrun` paths exit before any write outside the mktemp
  workspace and before any ssh. `git status` after a dry run is clean.
- **`failed.txt` from 20 concurrent workers.** One short `printf '%s\n' "$url" >>` per worker,
  far under PIPE_BUF, O_APPEND — a single atomic `write()` each. No interleaving possible.
  The per-worker-file + `find` pattern for the payload itself is right.
- **`count_lines()`.** Verified both traps it documents: on a file with no trailing newline
  `wc -l` = 2 where `grep -c ''` = 3; `grep -c ''` on an empty file prints `0` and exits 1,
  which the `||` absorbs. The `local n; n=$(...)` split avoids the exit-status-masking form.
- **bash 3.2 / BSD portability.** No arrays, so the empty-array-under-`set -u` trap is not
  reachable. `mktemp -t item_register_paths.XXXXXX` and `mktemp -d -t catalogue_register.XXXXXX`
  both work here (bash 3.2.57, BSD mktemp); the remote `/tmp/stac_items.XXXXXX.ndjson` works
  on GNU mktemp because a template not ending in X implies `--suffix`. `sed`, `grep -c ''`,
  `grep -v '^[[:space:]]*$'`, `cut` are all POSIX. `md5sum`/`md5 -q` both branched.
  `xargs -P 20 -I {}` genuinely parallelizes on macOS (measured: 6 jobs with `sleep 1` in 1.4s).
- **Heredocs.** `<<'FETCHEOF'` and `<<'PYEOF'` are quoted where `$1`/`$url`/Python must survive
  verbatim; `<<SQL` is unquoted where `$COLLECTION_ID` must interpolate, and its body carries
  no backticks or `$(`, so the command-substitution trap is not live.
- **`curl > file` truncation.** The worker writes `$out.part` and `mv`s on success, and
  `rm -f`s the partial on failure, so a zero-byte file can never count as present. `-name '*.json'`
  does not match `*.json.part`.
- **Duplicate-link risk in the count guard.** `N_FETCHED` counts unique md5 filenames while
  `N_TODO` counts todo lines, so duplicate hrefs would abort a good run. Checked the live
  `collection.json`: 102,460 item links, 102,460 unique hrefs, 102,460 unique ids, 0 raw-space
  hrefs. Cannot misfire on real data.
- **Paging.** `ids_registered` enumerated all 102,460 ids against the live API with no
  duplicates and no shortfall.
