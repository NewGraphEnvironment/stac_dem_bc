# Review round 2 — the fixes for round 1 (#27)

Branch `27-client-side-pgstac-registration` vs `main`, commit `746d315` ("Fix three
real bugs found in review"). Read in full: `catalogue_register.sh`,
`register_manifest.py`, `item_register.sh`, `collection_register.sh`,
`collection_unregister.sh`, `collection_patch.py`, `collection_create.py`,
`update.yml`, both test files. Everything below was probed — against the live API,
against bash 3.2, or by reproducing the shell path — not inferred from reading.

## Verdict on the three fixes

All three are **correct**, and two of them were confirmed against the thing that
would have broken them:

- **Fix 1 (`limit`)** — `search_body` is right and `ids_serving` uses it. Probed
  live: 500 ids/limit 500 → 500 features; 600/600 → 600. No API-side ceiling is
  reached at chunk 500 (`ids_registered` already runs at limit 10,000).
  Duplicates inside a batch are harmless: 20 ids (10 unique) → limit 20 → 10
  features, and `set(wanted) - got` is empty.
- **Fix 2 (`--clear-version`)** — both call sites are wired, and I traced every
  reachable workflow path to confirm no route reaches `s3_sync-ci.sh` with an
  unpatched collection. The deletions-only + pairing-changed month (`new_urls`
  false, `changes` true) is covered by the inner `[ ! -s collection.json ]`
  branch at `update.yml:171-176`. Clearing does not disturb `collection_patch.py`'s
  link-count assertion (`version_clear` touches no links) and `--check` still
  writes nothing (it returns before the tmp write). 87 tests pass.
- **Fix 3 (`--verify` both directions)** — the `RC` accumulator is sound. Traced
  under `set -euo pipefail` on **bash 3.2.57**: `RC=1` cannot be lost (no
  subshell, no pipeline), `exit "$RC"` runs, and `orphaned.txt` is written by
  `diff` in *every* mode that reaches the verify block. Measured the live
  catalogue today — **published 102,460 / registered 102,460 / missing 0 /
  orphaned 0** (3m36s) — so the newly-armed orphan half does not cry wolf on
  known debt.

`ids_registered`'s new raise is the highest-risk change and it is **safe**.
Probed the live API at two boundaries that would have broken a full enumeration:

```
partial last page (5 items, limit 2):   page2 = 1 feat,  next = no
EXACT multiple    (5 items, limit 5):   page0 = 5 feats, next = no
```

The API omits the `next` link entirely when exhausted — it never emits one
without a token — including at an exact page-size multiple, which is the case
that would have made the raise fire on a healthy catalogue. Confirmed again at
full scale: 102,460 ids enumerated in 11 requests, no raise.

## Findings

- **[bug] scripts/catalogue_register.sh:107-116 + 240-249 — `--ids-file` with a
  duplicate id runs the entire fetch and then aborts, reporting a fetch failure
  that did not happen.**
  The `--ids-file` normalisation drops blank lines and fixes the trailing-newline
  trap but does **not** dedupe. `N_TODO` then counts *lines*, while
  `hrefs-published` filters against a **set** (`register_manifest.py:253`) and so
  emits one href per unique id. The fetch succeeds completely, and the count guard
  at 240 compares unique files against duplicated lines. Reproduced verbatim with
  the script's own normalisation:

  ```
  N_TODO (the guard's expectation) = 3
  urls actually fetched            = 2
  ABORT: fetched 2 of 3 - nothing sent to the database (failed URLs: 0)
  ```

  The diagnostic is actively misleading: `N_FAILED` is 0, `failed.txt` is empty so
  the `head -5` prints nothing, and `fetch_stderr.txt` is empty so the new stderr
  block is skipped too. The operator is told the fetch fell short and shown no
  failed URL — with the fix from round 1 in place, there is now *no* output at all
  explaining it. `--ids-file` is the hand-assembled recovery mode (README:199
  documents no generator for it), so concatenating two id lists or piping a grep
  is exactly how this arrives. `--all` and `--drift` are unaffected (published
  hrefs are unique; `ids_diff` returns sorted sets).
  Fix: `grep -v '^[[:space:]]*$' "$IDS_FILE" | sort -u > "$WORK/todo.txt"`.
  This is the same class as round-1 finding 1 — a guard failing toward abort after
  the expensive stage succeeded — surviving in the mode round 1 did not exercise.

- **[fragile] scripts/register_manifest.py:150-166 and 89-129 — the verification
  that replaced the inline block makes up to 205 unretried HTTP calls and is the
  last gate after the expensive stage, so one transient 5xx reports a successful
  registration as a failure.**
  `ids_serving` chunks at 500: `--all` is `ceil(102460/500)` = **205** POSTs,
  a typical ~8k `--drift` month is 16, and `ids_registered` adds 11. None retry —
  `resp.raise_for_status()` propagates, `catalogue_register.sh` runs under
  `set -e`, and the operator gets a traceback instead of `DONE`, after the upsert
  has already committed.
  The same commit added a 3-attempt retry loop to the fetch worker
  (`catalogue_register.sh:210-218`) for precisely this reason, citing the backfill
  that "completed 98,040 items and threw the run away over 2 transient errors" —
  the verifier did not get one. Round 1's fix moved the failing gate rather than
  removing the property.
  Mitigating, and worth stating so this is not over-weighted: the data is safe and
  recovery is cheap, not a 45-minute re-fetch — a follow-up `--drift` enumerates in
  ~3m40s, finds 0 missing and exits 0. The cost is a wrong verdict and a
  traceback, on the run whose whole job was to say whether registration worked.
  A 3-attempt retry around each `session.post` in `ids_serving`/`ids_registered`
  closes it.

- **[fragile] scripts/catalogue_register.sh:74-78 consumed at 136 — the new orphan
  gate reads its input through a helper that returns `0` for a file that does not
  exist, so the gate's escape hatch fails toward "clean".**
  Measured on bash 3.2: `count_lines` on a missing path and on a directory both
  print `0` (grep exits 2, stdout empty, `${n:-0}` absorbs it). That is correct and
  deliberate for `failed.txt`, where absence really does mean zero. For
  `orphaned.txt` it means an unwritten file reads as "no orphans" — an affirmative
  `IN SYNC` from a check that never ran.
  Not reachable today: `diff` writes `--orphaned-out` unconditionally
  (`register_manifest.py:289-290`) and `set -e` kills the script if it does not,
  and I checked every argv ordering that can land `MODE=verify` — all of them route
  through the `drift|verify` case. But this is the "a guard's escape hatches are
  where it goes to die" shape from CLAUDE.md, and the *only* thing standing between
  it and a silent pass is that one write. One line before 136 makes it structural:
  `[ -f "$WORK/orphaned.txt" ] || { echo "ERROR: orphan report missing" >&2; exit 1; }`.

- **[fragile] scripts/register_manifest.py:132 — `search_body(ids, page_size=PAGE_SIZE)`
  declares a parameter it never reads.** `limit` is `max(len(ids), 1)` regardless.
  No caller passes it today, so nothing is wrong now; the hazard is that a future
  caller asking for a smaller page gets `limit=len(ids)` with no error — the same
  silent-wrong-answer shape the parameter's own docstring is about. Either use it
  (`min(max(len(ids),1), page_size)`) or drop it.

## Carried over from round 1, still open

- **scripts/item_register.sh:24** — the usage line still reads
  `scripts/item_register.sh --dryrun < ids.txt`, contradicting line 4 ("Item JSON
  paths arrive on STDIN"). Round 1 flagged it; the fix commit did not touch it.
  Following the documented invocation raises `FileNotFoundError` from
  `ndjson_write`. Loud, but only after the operator has done what the header said.

## Checked and correct — do not re-derive

- **`count_lines` on bash 3.2.57.** `empty=0, two=2, no-trailing-newline=2,
  missing=0`, script survives `set -euo pipefail`. The `local n` / `n=$(...) || n=…`
  split is right: the `||` sees the *assignment's* status (grep's), and `n` has
  already taken grep's stdout, so the empty-file case (`grep -c ''` prints `0`,
  exits 1) yields `0` rather than the fallback. Both documented traps verified.
- **The `RC` accumulator.** No subshell, no pipeline; `RC=1` survives to
  `exit "$RC"`; `head|sed` cannot trip pipefail. Reproduced the both-arms and
  neither-arm paths on bash 3.2.
- **API paging contract.** Last page carries no `next` link, at a partial page and
  at an exact page-size multiple. The raise cannot fire on a healthy enumeration.
  Token-repeat guard also cannot fire legitimately — tokens are keyset
  (`next:stac-dem-bc:<last id>`), so two pages cannot share one.
- **`limit` ceiling and duplicates.** No 400/413 at limit 500 or 600. A batch with
  duplicate ids returns unique features and still verifies clean, because the
  comparison is `set(wanted) - got`.
- **`--clear-version` wiring.** Both `update.yml` call sites; every reachable path
  to `s3_sync-ci.sh` passes through one of them. `version_clear` before the
  link-count assertion is safe (it touches `version` and `stac_extensions` only),
  and the tmp-file+rename is unaffected. `--check` still returns before any write.
- **`collection_create.py` stamp.** Built the collection exactly as `main()` does
  and validated it: `to_dict()` carries `version: 1.1.0` and the single version
  extension, `collection.validate()` passes, and the schema URL
  `.../version/v1.2.0/schema.json` returns 200. The merge fix is real, not just
  test-shaped.
- **`collection_register.sh:78` `ITEM_LINKS`.** The published `collection.json` is
  pretty-printed (512,367 lines), so `grep -c '"rel": *"item"'` returns the true
  102,460 rather than `1`. Not the one-line-file trap it looks like.
- **Registering the 19.4 MB published collection.** Checked what the API does with
  the item links pgstac then holds: `/collections/stac-dem-bc` comes back at
  **1,546 bytes with 5 links, none of them `rel:item`** — and it already carries
  the patched `providers`/`keywords`/`description`, so the live row was loaded from
  exactly this file. stac-fastapi does not echo stored item links. No blow-up.
- **`collection_unregister.sh` `$EXISTS` guard.** Empty, non-numeric and multi-line
  values all take the refusal branch. Also probed the bash arithmetic-injection
  angle on `[ "$X" -eq "$X" ]`: `[` uses a strict integer parse, **not** arithmetic
  evaluation, so `x[$(cmd)]` is rejected rather than executed (that vector needs
  `(( ))` or `[[ ]]`). Not a security issue.
- **`xargs ... 2>"$WORK/fetch_stderr.txt"`.** 20 workers share one inherited fd, so
  writes serialize on a shared offset; curl's messages are far under `PIPE_BUF`.
  Diagnostic-only output either way.
- **87 tests pass** (`.venv/bin/python -m pytest tests/ -q`).

## One note, not a finding

Fix 2 was a **wiring** bug — `--clear-version` existed, was tested, and was never
called — and the fix added no guard that it stays called. The 31 tests that passed
over the original defect would pass over its reintroduction too. Nothing is broken;
it is the same "running a generator is not committing what it generated" gap
CLAUDE.md names, and the cheapest closure is a one-line grep assertion over
`update.yml` rather than more unit tests of `version_clear`.
