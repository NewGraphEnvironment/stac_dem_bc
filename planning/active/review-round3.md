# Review round 3 — #34 collection/asset rename

Branch `34-rename-collection-to-stac-elevation-bc-a`, HEAD `be0328f`.
Files read in full on disk: `scripts/item_migrate.py`, `scripts/item_rewrite.py`,
`scripts/item_backfill.py`, `scripts/register_manifest.py`,
`scripts/catalogue_register.sh`, `scripts/item_register.sh`,
`scripts/s3_sync-ci.sh`, `scripts/collection_patch.py`, `scripts/item_validate.py`,
`.github/workflows/update.yml`, `tests/test_item_migrate.py`,
`tests/test_item_backfill.py`. Suite run: 202 passed.

Round 3 was asked for the **mechanism**, not more instances. Two mechanisms
account for every finding below.

**Mechanism A — the item is the unit of work, and `collection.json` is not.**
Every guard on the publish path is expressed in item files: the sync gate counts
item files, the audits list item files, the completeness reconciliation is over
item ids. `collection.json` is edited by a separate script, on a separate
schedule, and rides to S3 as a passenger of the item sync. So the two artifacts
can disagree, and no guard in the repo is shaped to notice — F1 and F2 are the
same mechanism pointed in opposite directions.

**Mechanism B — a gate whose denominator is the run, applied to a resumable job
whose runs shrink.** `error_tolerable(errors, processed)` is correct on a first
run and becomes near-zero-tolerance on a resumed one, because `processed` is
`todo`, not the population. The round-2 defect was the same shape (a completeness
check that any error could trip); the fix moved the exit condition but left the
denominator. F3, and F8 is the degenerate case (a gate with no tolerance at all).

---

## 1. Guard enumeration, and the pairs that interact

`abort` = returns non-zero / raises. `skip` = does less work and reports success.

### `item_rewrite.py`

| # | line | asserts | fails toward | caller does |
|---|---|---|---|---|
| G1 | 64–73 `error_tolerable` | errors ≤ 200 **and** rate ≤ 0.1% | abort | `main()` returns 1 → CI skips sync → CI discards manifest |
| G2 | 98–120 `manifest_load` header | the ledger belongs to this migration | abort (raise) | step fails, nothing written |
| G3 | 98–103 | zero-byte/absent manifest is empty, not refused | skip | proceeds with `done = {}` |
| G4 | 151–155 `published_item_ids` | an explicit `--collection` path exists | abort (raise) | never silently falls back to the network |
| G5 | 176–181 `item_fetch` | HTTP 200 | abort per item → retried | counted as `error`, feeds G1 |
| G6 | 192–199 `process_one` retry ×3 | transience is absorbed | n/a | keeps ordinary failures out of G1 |
| G7 | 208–235 edit outside the retry | a deterministic edit error is not retried | abort per item | feeds G1 |
| G8 | 222–231 tmp+`os.replace` | no truncated item on disk | abort per item | protects G12/G13 |
| G9 | 300–324 `verify_rewrite` | file == re-derived prediction, plus `expect` | abort | `main()` returns 1 |
| G10 | 327–345 `skip_already_staged` | do not clobber a fresher local build | **skip** | ids leave `todo` and are counted done by G13 |

### `item_migrate.py`

| # | line | asserts | fails toward | caller does |
|---|---|---|---|---|
| G11 | 98–99 | `asset_renames` is injective | abort (raise) | import-time-ish; unreachable with the shipped map |
| G12 | 105–110 | no item carries both keys | abort per item | feeds G1 |
| G13 | 175–199 | **every staged file is already in the migrated shape** | abort | returns 1 before any fetch |
| G14 | 169–170 | `--limit >= 0` | abort | argparse error |
| G15 | 258–265 | `--verify` sample matches prediction + intent | abort | returns 1 |
| G16 | 266–274 | `checked == 0` is **not** a failure | skip | logs, defers to G18 |
| G17 | 301–304 | manifest ids no longer published = warn only (#28) | skip | warning |
| G18 | 318–323 | no id was **never attempted** | abort | returns 1 |
| G19 | 324–330 | remaining ids are all this run's errors | **skip** | warning, then G1 decides |
| G20 | 292/335 | `--limit` suppresses completeness | skip | explicit warning |

### `register_manifest.py`

| # | line | asserts | fails toward | caller does |
|---|---|---|---|---|
| G21 | 151–157 `ids_registered` | paging advanced and had a token | abort | script fails |
| G22 | 186–188 `search_body` | `collection_id` is required | abort | script fails |
| G23 | 191–193 | explicit `limit` (API default is 10) | n/a | prevents a false "missing" |
| G24 | 255–262 `ndjson_write` `--expect-collection` | every body names the target collection | abort | `item_register.sh` fails before ssh |
| G25 | 273–308 `audit_items` | collection id, required asset, forbidden asset, readability | data | CLI turns into exit 1 |
| G26 | 430–432 | zero paths is a FAIL, not a vacuous pass | abort | exit 1 |
| G27 | 453–456 `--expect` | audited count == expected | abort | exit 1 |
| G28 | 464–466 `verify-serving` | 0 ids exits 0 saying so | skip | exit 0 |
| G29 | 376–383 `hrefs-published` | every requested id has a link | abort | SystemExit |

### `catalogue_register.sh` / `item_register.sh` / `s3_sync-ci.sh`

| # | line | asserts | fails toward | caller does |
|---|---|---|---|---|
| G30 | cat:57–66 | COLLECTION_ID resolved, non-empty | abort | exit 1 |
| G31 | cat:122–140 | `collection.json.id` == COLLECTION_ID | abort | exit 1, with two-cause guidance |
| G32 | cat:149–152 | published set is non-empty | abort | exit 1 |
| G33 | cat:197–200 | orphan list exists before verifying | abort | exit 1 |
| G34 | cat:252–256 | one id → exactly one URL | abort | exit 1 |
| G35 | cat:267–271 | host reachable, probed before the fetch | abort | exit 1 |
| G36 | cat:297–312 | per-URL retry ×3, atomic mv, failures recorded | per-URL | feeds G37 |
| G37 | cat:331–340 | `N_FETCHED == N_URLS` | abort | exit 1, nothing loaded |
| G38 | cat:358–359 | audit every fetched body — **collection id only** | abort | exit 1 |
| G39 | cat:381–382 | set equality against the live API | abort | exit 1 |
| G40 | reg:77–80 | 0 items on stdin exits 0 saying so | skip | exit 0 |
| G41 | reg:88–91 | NDJSON lines == input paths | abort | exit 1 |
| G42 | reg:123–126 | receiver counts the lines it got | abort | remote exit 1, nothing loaded |
| G43 | sync:37–48 | `STAC_OUTPUT_DIR` set, a dir, non-empty `collection.json` | abort | exit 1 |
| G44 | sync:57–59 | never `--delete`; `*.tmp` excluded | n/a | — |

### `update.yml`

| # | line | asserts | fails toward | caller does |
|---|---|---|---|---|
| G45 | 92–93 | pairing contract tests pass | abort | job fails first |
| G46 | 103–109 | backfill and rename are not both set | abort | job fails first |
| G47 | 125–129 | `detect_changes.R` exit ∈ {0,1} | abort | job fails |
| G48 | 138–141 | source URL access — **warn only** | skip | `continue-on-error` |
| G49 | 186–188 | created < expected → `::warning` | skip | annotation only |
| G50 | 247–253 | `publish.count` = item files, **excluding `collection.json`** | data | gates G51/G52/G53/G54 |
| G51 | 266–275 | item validation — **zero tolerance**, full run on rewrites | abort | job fails |
| G52 | 295–310 | monthly audit over staged items | abort | job fails |
| G53 | 319–332 | rewrite audit over staged items | abort | job fails |
| G54 | 337–340 | sync runs only when `count != '0'` | **skip** | S3 untouched |
| G55 | 352–365 | `always()` upload of manifests | — | evidence survives failure |
| G56 | 388–405 | discard rewrite manifests when `sync.outcome != success` | **destructive** | reverts `data/*_done.txt` to HEAD |

### Interacting pairs

Pairs where one guard's failure mode becomes another's input:

1. **G1 → G54 → G56** (this is the round-2 shape, still live). A tolerated-rate
   *failure* skips the sync, which triggers the manifest discard, which throws
   away the items this run did complete. Because G1's denominator is `todo`, a
   resumed run has effectively zero tolerance → **F3**.
2. **G51 → G54 → G56.** Same chain, but G51 has *no* tolerance and is
   deterministic, so a single bad item is a loop with no exit → **F8**.
3. **G10 → G13 → G18.** `skip_already_staged` removes ids from `todo`, and the
   completeness reconciliation then counts them as migrated. G13 is what makes
   that sound — and **`item_backfill` has G10 without G13** → **F5a**.
4. **G54 ↔ `collection.json`.** The sync gate is expressed in item files, so a
   run whose only pending change is `collection.json` skips the sync, and G31
   downstream keeps failing with advice that points back at the run that just
   went green → **F2**.
5. **G52 scope ↔ `collection_patch`.** The monthly audit inspects only staged
   items, so a `collection.json` renamed by the monthly path over unmigrated
   item bodies passes it → **F1**.
6. **G38 vs G52/G53.** The same audit function, invoked with a *narrower*
   argument set on the client-side path than in CI → **F6**.

Everything else in the tables is single-direction: it aborts or it warns, and
nothing consumes its output as a premise. I found no further pairs.

---

## 2. Exit-code traces

`STAC_OUTPUT_DIR` = `$GITHUB_WORKSPACE/stac_out`, fresh each run.

### (a) rename dispatch, everything succeeds

`detect` 0/1 → `Fetch collection` (`:152`) → `collection_patch --clear-version`
(`:168`, **renames the id**) → `item_migrate` (`:237`) writes ~102,460 files,
`tolerable=True`, "Complete", exit 0 → `publish.count` = 102,460 (`:251`) →
`item_validate` full (`:270`) 0 → rewrite audit (`:332`) 0 → `s3_sync-ci.sh`
(`:340`) items then `collection.json` → `sync.outcome=success` → `:388` keeps the
manifest → commit + push.
**S3 consistent. `data/migrate_done.txt` committed — correct.**

### (b) 2 of 102,460 fail after 3 attempts (inside tolerance)

`error_tolerable(2, 102460)` → True (measured). G19 warns, `main` returns 0 at
`item_migrate.py:339`. count = 102,458 → validate/audit over what is staged →
sync uploads 102,458 items **and the renamed `collection.json`** → manifest
committed with 102,458 ids.
**S3 is MIXED**: `collection.json` says `stac-elevation-bc`, 2 item bodies still
say `stac-dem-bc`/`image`. Job GREEN, no annotation — the only signal is a
`logger.warning` in the step log. Manifest committed — correct (those ids *are*
published), and required for (e).

### (c) 5,000 fail (outside tolerance)

`error_tolerable(5000, 102460)` → False. G18 passes (all missing are errored),
G19 warns *"The run still publishes what it completed"*, then `:339` returns 1.
Step fails → `:339` sync skipped → `:388` `sync.outcome != success` →
**`data/migrate_done.txt` reverted**. The 97,460 items this run wrote are
discarded with the runner and their ledger entries with them.
**S3 unchanged (consistent).** Manifest not committed — correct in outcome
(nothing was published) but the cost is a full re-fetch, and the warning at
`item_migrate.py:326` is false on this path.

### (d) job hits `timeout-minutes: 330` mid-migration

Job cancelled. `always()` steps (`:353`, `:368`) run; `sync.outcome` is `skipped`
→ manifests discarded. **S3 unchanged, consistent.** Manifest not committed —
correct.
*Caveat:* if the timeout lands **inside** the sync step, `s3 sync` may have
uploaded items while `s3 cp collection.json` (`s3_sync-ci.sh:61`) never ran.
`sync.outcome` is then `cancelled` → manifest discarded → and the catalogue is
now in **F2**'s precondition. This is the most plausible route into F2.

### (e) re-run after (b) — does it finish the remaining 2?

`checkout` gets the committed 102,458-id manifest → `todo` = 2 → nothing staged →
both succeed → count = 2 → validate/audit → sync uploads 2 items +
`collection.json` → "Complete" → manifest committed with 102,460.
**Yes, S3 ends consistent — provided both succeed.** If one of the two fails,
`error_tolerable(1, 2)` = False (measured) → exit 1 → sync skipped → manifest
reverted to 102,458 → the one item that *did* migrate is discarded. Net progress
zero, and the run repeats the coin flip. See **F3**.

### (f) re-run after a fully successful rename

`todo` = ∅ (manifest complete) → `run_rewrite` over nothing → `tolerable(0,0)` =
True → verify samples 0 → G16 logs and defers → G18/G19 clean → "Complete" →
exit 0. `publish.count` = 0 (only `collection.json` in the dir) → validate, both
audits and **the sync are skipped** → `:388` `sync.outcome=skipped` →
`data/migrate_done.txt` **reverted to HEAD** (identical content, so harmless).
**S3 unchanged, consistent.** Manifest effectively unchanged — correct.
*Note:* if run (a)'s commit step had failed (push rejected), the manifest is not
in HEAD and can never be committed thereafter, because every subsequent run has
count = 0 and hits the discard. Harmless to S3; costs a 102k re-fetch each time.

### (g) normal monthly cron with new URLs

`detect` 1, `new_urls=true` → fetch + **patch (renames `collection.json`)** →
`item_create --incremental` builds N items with `collection.id` read off the
patched file (`item_create.py:370`) and `ASSET_DEM` from code → rebuild pass →
count = N → validate `--incremental` → monthly audit (`:307`) reads the
collection id **from the same patched file**, so it passes → sync uploads N items
+ the renamed `collection.json` → exit 0, caches committed.
**S3 is MIXED and nothing reports it.** See **F1**. No rewrite manifest is
involved; the discard loop is a no-op.

### (h) pairing-only month (`changes=true`, `new_urls=false`)

`Fetch collection` (`:152`) and `Patch` (`:162`) are both **skipped**, so the
fallback inside the rebuild step (`:199–204`) fetches *and patches* — same
rename. `item_create --urls-file` rebuilds N items with the new id and `dem` →
count = N → monthly audit passes → sync uploads N items + the renamed
`collection.json`.
**Identical to (g): S3 mixed, green run.** If `urls_pairing_changed.txt` is
empty the whole branch is a no-op, `find` on a missing dir yields count = 0
through the pipeline, sync is skipped, and only the caches are committed —
consistent.

---

## 3. Can S3 end up MIXED with nothing reporting it?

**Yes — two ways, both traced above.**

- **(g)/(h) → F1.** The first monthly run after merge publishes a
  `collection.json` whose `id`, `title` and `description` describe the renamed
  collection, over ~102,460 item bodies that still name `stac-dem-bc` and carry
  `image`. Every CI guard passes because `G52` audits `$STAC_OUTPUT_DIR` — the
  items this run staged — and those are correct. The job is green with no
  annotation.
- **(b) → residual mix.** A tolerated failure rate leaves a handful of stale
  bodies under a renamed `collection.json`, green, with only a `logger.warning`
  in a step log.

**Is `catalogue_register.sh --all` a sufficient backstop?**

For **pgstac**, yes. `G38` (`catalogue_register.sh:358`) audits every fetched
body before `collection_register.sh` or `item_register.sh` is invoked, and
`G24`/`STAC_COLLECTION` re-asserts it on the NDJSON. Nothing mixed can reach the
database. In `--drift` mode the coverage is still complete for the mixed case,
because pgstac assigns an item to the collection named in its own body — so a
stale item is never registered under the new id and always appears in `missing`,
hence always gets fetched and audited.

For **S3**, no, and this is the window:

> From the sync that publishes the renamed `collection.json` until a rename run
> completes and syncs, the static catalog on S3 is internally inconsistent, CI is
> green, and the only report is a **manual** registration attempt failing —
> which happens at most monthly.

Two aggravations worth stating:

1. During that window `--drift` fails **for the whole month**, so the month's
   genuinely-new items cannot be registered either. `G31` does not save you here:
   `collection.json` already carries the new id, so the mismatch check passes and
   the failure lands at `G38`, after ~102k fetches.
2. `G38` audits the collection id but **not** the asset key (**F6**), so it is
   not a complete backstop for the mixed shape — only for the half of it that is
   reachable from this repo's writers today.

---

## 4. Round-1/2 fixes applied to one caller and not its sibling

Three, all `item_migrate` → `item_backfill`. See **F5**.

---

## 5. Tests that cannot fail / fixtures that cannot reach the failure

- `ERROR_ABS_MAX` is unenforceable and untestable at this scale — **F4**,
  verified by mutation (`ERROR_ABS_MAX = 10**9` → 202 passed).
- `tests/test_item_migrate.py:477` models a **first** run and is used to certify
  resumed-run behaviour — **F9**.
- `tests/test_item_migrate.py:491` cannot distinguish a 0.1% gate from a
  zero-error gate at its fixture size — **F9**.
- `item_migrate.py:175–199` (G13, the staged-shape gate, added in round 1/2) is
  reached by **no test**: `_run_migrate` (`tests/test_item_migrate.py:443`)
  always `mkdir`s an empty out-dir, so `staged` is always `[]`. Both its
  `return 1` path and its "counted as done" path are unexercised.
- No test touches `update.yml`, so `G50`/`G54`/`G56` — the three guards that
  produce F2 and half of F3 — have no coverage of any kind. Stated as scope, not
  as a request to add tests.

I found no test that is *vacuous* in the sense of asserting a tautology, and no
exemption list or lookup that makes an assertion unreachable.

---

## Findings

- **[bug]** `.github/workflows/update.yml:162-168` (and the fallback at `:199-204`) — the **monthly** path runs `collection_patch.py`, and `collection_patch()` puts `id` inside its idempotence contract (`scripts/collection_patch.py:201-208`). So the first cron/monthly run after this branch merges publishes `collection.json` with `id: stac-elevation-bc` over ~102,460 item bodies that still say `stac-dem-bc` and carry `image`. The monthly audit at `:295-310` reads the collection id **from that same freshly-patched file** and inspects only `$STAC_OUTPUT_DIR`, so it passes. Result: a mixed, internally inconsistent static catalogue on S3 with a **green** run and no annotation; and `catalogue_register.sh --drift` then fails at `scripts/catalogue_register.sh:358` for the entire month, blocking registration of that month's legitimately-new items too. Nothing orders the rename dispatch before the monthly path. Traced in §2(g) and §2(h).

- **[bug]** `.github/workflows/update.yml:247-253` + `:337-340` + `:388-405` — the sync gate counts item files **excluding `collection.json`**, and `item_rewrite.process_one` (`scripts/item_rewrite.py:210-211`) writes no file for an already-correct item. So a rename re-run against a catalogue whose *items* are migrated but whose `collection.json` was never uploaded produces `count=0` → validate, both audits and the sync are all skipped → `collection.json` is never republished → `catalogue_register.sh:122-140` keeps failing with guidance to "run the migration", which is exactly what just ran, green, logging `Complete: all 102,460 published items are migrated` (`scripts/item_migrate.py:331-334`). The commit step then reverts `data/migrate_done.txt` because `sync.outcome != success`, so each attempt re-fetches all 102,460 bodies and makes zero progress. **A loop with no exit**, built from two individually-correct guards (idempotent no-write; don't-sync-when-nothing-changed). Precondition is reachable when `aws s3 sync` succeeds and the following `aws s3 cp collection.json` (`scripts/s3_sync-ci.sh:61`) fails or the job is cancelled between them — see §2(d). Generalises past #34: **any** collection-metadata-only change can never reach S3 unless some item happens to change in the same run.

- **[bug]** `scripts/item_migrate.py:248-249` and `scripts/item_backfill.py:211-212` (`processed = sum(counts.values())`) with `scripts/item_rewrite.py:64-73` — the error-rate gate's denominator is the **run's `todo`**, not the population, so a resumed run's tolerance shrinks with the work left. Any single error on a run of fewer than 1,000 remaining items exceeds the 0.1% rate. Reproduced against the real `main()`: 1,000 published, 998 in the manifest, 1 of the remaining 2 succeeds and 1 fails → `error rate 0.50000 (1/2); ... EXCEEDED` → exit 1. The workflow then skips the sync and reverts `data/migrate_done.txt` (`.github/workflows/update.yml:388-405`), so the item that *did* migrate is discarded and the next run repeats the coin flip. Same shape as the round-2 defect with the rate replacing the completeness check. Additionally, `scripts/item_migrate.py:324-330` logs *"The run still publishes what it completed"* on this exact path — that branch is reached **before** `tolerable` is applied at `:339`, so the message is false whenever the rate gate then fails. At 5,000/102,460 the same discard throws away 97,460 completed writes (§2(c)).

- **[fragile]** `scripts/item_rewrite.py:53-54,73` — `ERROR_ABS_MAX = 200` can never bind at this catalogue's scale: the gate is `errors <= 200 AND rate <= 0.001`, and 0.1% of 102,460 is 102, so the absolute cap only becomes the binding constraint above 200,000 processed items. Verified by mutation — setting `ERROR_ABS_MAX = 10**9` leaves all 202 tests green. `tests/test_item_backfill.py:160` names it as the reason (`# over the absolute cap`) while actually exercising the rate (500/98,040 = 0.51%). `scripts/item_migrate.py:252-254` advertises it in the log as a live tolerance.

- **[fragile]** `scripts/item_backfill.py:169-172`, `:225`, `:229-230` — three round-1/2 fixes landed on `item_migrate` and not on its sibling, both of which consume `item_rewrite`:
  (a) `:169-172` skips staged ids and `:237` then counts them as done **without checking their shape** — precisely the failure `scripts/item_migrate.py:180-199` was added to close, and whose own comment names `item_backfill --out-dir D` then `item_migrate --out-dir D` as the example. The mirrored sequence is unguarded, so `item_migrate --out-dir D` followed by `item_backfill --out-dir D` reports the catalogue fully backfilled having backfilled nothing.
  (b) `:225` calls `verify_rewrite(sample, out_dir, edit)` with **no `expect`** — exactly the weakness `scripts/item_rewrite.py:292-295` documents ("a sample in which the edit happened to do nothing would pass while proving nothing"). `item_migrate.py:262` passes one.
  (c) `:229-230` prints `Verify passed on 0 items: the rewrite is exactly what item_edit predicts` when `checked == 0` — an affirmative success claim over an empty sample. `item_migrate.py:266-276` branches on that case explicitly.

- **[fragile]** `scripts/catalogue_register.sh:358-359` — the last checkpoint before pgstac runs `audit-items --dir --collection-id --expect` with **no** `--require-asset` / `--forbid-asset`, while both CI audits do pass them (`.github/workflows/update.yml:307-310`, `:328-332`). An item carrying the new collection id and the old `image` key would load without complaint. Not reachable from any writer in this repo today (`item_migrate` moves both fields together), but this is the one gate that runs on the client-side path the header describes as the "after #34" route, i.e. the case where the CI audits were bypassed.

- **[fragile]** `.github/workflows/update.yml:306` and `:329` — `OLD=$(... ",".join(item_migrate.ASSET_RENAMES))` collapses a multi-entry rename map into a single comma-joined string, and `register_manifest.audit_items` tests `forbid_asset in assets` (`scripts/register_manifest.py:307`). Measured: with `--forbid-asset "image,other"` an item carrying `image` returns `forbidden_asset: []` — the guard stops firing, silently, with no error. `scripts/item_migrate.py:97-99` guards the *collide* case of a multi-entry map with an explicit raise; this consumer has no equivalent, so the shipped single-entry map is the only thing holding the workflow's forbid check up.

- **[fragile]** `scripts/item_validate.py` final `return 0 if invalid == 0 else 1`, invoked full (not `--incremental`) over the whole rewritten catalogue at `.github/workflows/update.yml:266-275` — a **zero-tolerance** gate over ~102,460 items whose failure is deterministic. One item failing `pystac` validation fails the step → sync skipped → `data/migrate_done.txt` discarded → the next run re-fetches everything and fails identically: a loop with no exit, arriving through a third guard rather than the two round 2 fixed. Mitigating and worth stating: `data/stac_item_validation.csv` currently records **0 invalid of 98,048**, and the migration's edit (a collection string, an asset key) cannot change schema validity — so the trigger requires an item that is already invalid and outside those 98,048 of 102,460. Low probability, unbounded cost, no tolerance knob.

- **[fragile]** `tests/test_item_migrate.py:477-488` — the test named *"a tolerated failure rate still publishes what completed"* seeds no manifest, so `todo` is the full 3,000: it models a **first** run, a shape a resumed run never has, and therefore cannot reach the finding above. It also asserts only the exit code; "publishes what it completed" is a property of `update.yml`'s sync gate and manifest-discard, which no test touches. `:491-496` (100 ids, 10 errors) is insensitive to the threshold — 1 error at 100 items also fails — so it cannot distinguish a 0.1% gate from a zero-error one. Separately, the staged-shape gate at `scripts/item_migrate.py:175-199` is reached by no test at all: `_run_migrate` (`:443`) always creates an empty out-dir, so `staged` is always `[]` and both of that gate's branches are unexercised.
