# Review round 2 — #34 collection/asset rename

Second pass, scoped to the FIXES rather than the original code. Reviewed against
the **working tree** (which moved during the review — `scripts/item_migrate.py`,
`scripts/item_rewrite.py`, `.github/workflows/update.yml`, `tests/test_asset_key.py`
and `tests/test_item_migrate.py` all changed on disk mid-pass), not against
`branch2.diff`, which is against committed HEAD.

Suite state at the time of writing: `193 passed`.

Round-1 findings verified as **fixed** in the working tree and not restated here:
staged ids are now content-checked (r1 #3), the rename/backfill mutual exclusion
moved to a pre-write step (r1 #2), `--verify` with a 0-item sample is now INFO
rather than a failure (r1 #1), `extra` is a warning rather than an abort (r1 #9),
stderr is no longer suppressed on the manifest discard (r1 #8), and the workflow
asset-key literals now have a guard (r1 #7). Round-1 #6 is still live and is
restated below as finding 5, because it is the one that survives into the monthly
path.

Two findings were confirmed by probe rather than by reading. Probes are in
`$SCRATCH/probe1.py` and `$SCRATCH/probe2.py`.

---

## Findings

### 1. **[bug]** `scripts/item_migrate.py:290-315` + `.github/workflows/update.yml:337-386` — the completeness check makes `error_tolerable` unreachable, and with the new sync gate a single transient failure now discards the whole run's progress

This is a defect *created by the interaction of two fixes*, and the CI comment
that justifies fix (a) states the opposite of what the code does.

`update.yml:363-366` reasons:

> A tolerated error rate still exits 0 and still reaches the sync, so that
> progress is still preserved.

That premise is false for `item_migrate.py`. Trace it:

- an errored id is by construction in `todo` (not in `staged`, not in the
  manifest at start), so `errors > 0` ⟹ `missing` is non-empty
- `missing` ⟹ `return 1` at line 307, **before** `return 0 if tolerable else 1`
  at line 315

So for the only mode that matters (`--limit is None`, which is what CI passes),
`error_tolerable()` is **dead code**. The tolerance, the retry policy, and the
whole "a guard must not fail toward abort" rationale in `item_rewrite.py:49-54`
are unreachable through this caller.

Measured (`probe2.py`, 5,000 items, 2 forced transient failures):

```
[INFO]    written 4998 | unchanged 0 | error 2
[WARNING] error rate 0.00040 (2/5000); tolerance 0.00100 / 200 abs -> ACCEPTED
[ERROR]   INCOMPLETE: 5000 published, 2 never migrated
error_tolerable(2, 5000) = True
EXIT CODE: 1
```

Before fix (a) that cost the publish. **After fix (a) it also costs the
progress**: exit 1 → the migrate step fails → `steps.sync.outcome` is `skipped`
→ the discard loop runs → `data/migrate_done.txt` is unstaged *and*
`git checkout`-ed back to HEAD. The 4,998 completed items are gone from both the
commit and the disk.

At 102,460 items that is not a recoverable annoyance, it is a **loop with no
exit**: every attempt is ~1 hour of fetches, and the migration can only complete
on a run in which *zero* of 102,460 fetches fail after 3 attempts. The repo's own
measured history says that is unlikely — `item_rewrite.py:13` records 34
transient failures in one ~98k run, and the incident that motivated
`error_tolerable` was 2 failures in 98,040.

The two guards are individually correct and jointly wrong. Options, roughly in
order of how little they move:

- Treat `missing ⊆ errored` with a tolerable rate as **not** a completeness
  failure — the failed ids are simply not in the manifest, so the next run picks
  up exactly those, which is what the manifest is for. Fail only when `missing`
  contains ids that were never attempted.
- Or gate the CI discard on something narrower than "sync did not run" — e.g.
  keep the manifest when the migrate step exited 1 *and* the errors log accounts
  for every missing id.

Whichever, the comment at `update.yml:363-366` needs to stop asserting a
behaviour the code does not have; it is the load-bearing premise of fix (a) and
it is currently wrong.

---

### 2. **[fragile]** `scripts/item_backfill.py:162` — fix (d) landed in one of the two callers; `--limit 0` still reads as "no limit" here

```python
if args.limit:            # item_backfill.py:162
    todo = todo[: args.limit]
```

`item_migrate.py:162,192` was corrected to `if args.limit is not None:` with a
comment explaining exactly why (`--limit 0` would "run the whole catalogue for
someone who asked for nothing"), and a negative-value rejection. The identical
line in the sibling caller was not touched.

`python scripts/item_backfill.py --limit 0` fetches and rewrites all 102,460
published items. Same shared harness, same manifest mechanics, opposite
behaviour — and the reasoning that justified the fix applies verbatim.

---

### 3. **[fragile]** `scripts/item_backfill.py` — never calls `skip_already_staged`, so the pairing rebuild's output is overwritten by the published body

`skip_already_staged` was extracted into `item_rewrite.py:319` for a hazard its
own docstring describes:

> CI rebuilds items whose DSM pairing changed before the rewrite step runs --
> then rewriting would overwrite that rebuild with an edited copy of the stale
> published version, losing the rebuild silently.

`item_migrate` adopted it (`:165`). `item_backfill` did not — `grep -n
skip_already_staged scripts/*.py` returns only `item_migrate` and the harness.

The hazard is reachable: `update.yml:194` (`Rebuild items whose DSM pairing
changed`, `if: changes == 'true'`) runs **before** `update.yml:217` (`Backfill
published items`, `if: inputs.backfill`), and rebuilt ids *are* in the published
set, so they land in the backfill's `todo`.

Mostly benign, because `item_edit` re-derives the dsm asset from the same
`dem_dsm_pairs.csv` the rebuild used. The one case that is not: a tile whose DSM
was **withdrawn**. The rebuild correctly emits an item without `dsm`; the
backfill then overwrites it with the published body, which still carries `dsm`,
and `item_edit` has no removal path. Silent, and invisible to `audit-items`.

---

### 4. **[fragile]** `.github/workflows/update.yml:319-333` — dropping `--expect` is justified by a completeness check that only one of the two gated paths has

The step comment says:

> This step asserts HOMOGENEITY only. COMPLETENESS is item_migrate's own
> reconciliation (manifest + staged == published), which exits non-zero and so
> fails the job before this runs. One statement, one owner.

True for `rename`. The step's `if:` is `(inputs.backfill || inputs.rename) && …`,
and **`item_backfill.py` has no reconciliation at all** — it ends at
`return 0 if tolerable else 1` (`:216`) with no comparison of manifest against
published. Before fix (c), `--expect` was the only thing on the backfill path
that could notice a run which processed a subset; now nothing does.

Self-healing on re-dispatch (the manifest records what was done, so the next run
picks up the remainder), which is why this is fragile rather than a bug. But the
justification as written claims coverage the backfill path does not have, and it
is the kind of claim the next person will rely on.

---

### 5. **[fragile]** `.github/workflows/update.yml:295-306` vs `:194-212` — the "standing guard" is skipped entirely on a pairing-only month (round-1 #6, unfixed)

`Audit newly created items (monthly)` is gated on
`steps.detect.outputs.new_urls == 'true'`. `Rebuild items whose DSM pairing
changed` is gated on `steps.detect.outputs.changes == 'true'` and runs *after* it.

A pairing-only month — `changes == 'true'`, `new_urls == 'false'`, which the
workflow explicitly anticipates at `:265-267` ("a pairing-only month produces
item JSONs and no new URLs at all") — produces item JSONs, passes
`steps.publish.outputs.count != '0'`, validates, skips the rewrite audit
(backfill/rename both false), and **syncs to S3 with no homogeneity audit having
run at all**.

The comment at `:173-176` calls this "the standing guard, and the reason #34
cannot silently recur". Its scope is a coincidence of which trigger populated the
directory, not of what the directory contains. Gating on
`steps.detect.outputs.changes == 'true'` and moving it after the rebuild step
would cover both populations; the `COUNT -eq 0` early-exit already handles the
empty case.

---

### 6. **[fragile]** `.github/workflows/update.yml:371-384` vs `:395` — the discard destroys the on-disk manifest before the artifact step uploads it

Verified against a real repo (`$SCRATCH/gitprobe`), tracked and untracked side by
side:

| file | state | after `reset` + `checkout` |
|---|---|---|
| `data/backfill_done.txt` | tracked, appended this run | **on-disk content reverted to HEAD** |
| `data/migrate_done.txt` | untracked (first rename run) | left on disk intact |

`Upload run logs` (`:395`) runs after `Commit refreshed caches` and lists both
manifests by name — precisely so a failed run's progress is recoverable. For the
tracked one it uploads the *pre-run* copy, and once `data/migrate_done.txt` is
committed after the first successful rename, the same applies to it.

Given finding 1 — where a failed run discards work that genuinely completed —
losing the record of *which* ids completed is more than cosmetic. Copying both
files to `$RUNNER_TEMP` before the discard, or unstaging without
`git checkout --`, keeps the evidence without weakening the gate.

---

### 7. **[fragile]** `scripts/item_migrate.py:165-190` — the new staged check runs before the `--dry-run` branch, so a dry run against a dirty directory exits 1 having reported nothing

The staged-verification block (the fix for round-1 #3) sits at `:165-190`;
`if args.dry_run:` is at `:215`. So `--dry-run` against an out-dir holding
non-migrated files now returns 1 before printing anything — confirmed by
`probe1.py`, which reaches the error and never gets to the dry-run report.

It writes nothing, so it does not violate the preview contract. But `--dry-run`
is the natural way to diagnose a dirty workspace, and it is now the one input
where it refuses to tell you anything. Moving the dry-run branch above the staged
check, or making the check report-and-continue under `--dry-run`, restores it.

---

## Lower-severity notes

- **`scripts/item_migrate.py` `expect()` (`:203-215`) raises on `assets: null`.**
  `old in published_item.get("assets", {})` is `old in None` when the key is
  present and null — a `TypeError` that `verify_rewrite` does not catch, so it
  propagates out of `main()`. `test_malformed_items_do_not_raise` shows the
  authors consider that shape reachable in the population; the edit path handles
  it (`isinstance(assets, dict)`) and the verify path does not. Use
  `(published_item.get("assets") or {})`.
- **A second `# migration:` header now degrades to a warning.** Two processes
  starting `manifest_open` against a missing file both see `fresh=True` and both
  stamp a header; `manifest_load` then reads the second header line as an id,
  which lands in `extra` — a warning since the round-1 #9 fix, where it used to
  abort. `concurrency: group: stac-update` prevents this in CI and
  `test_manifest_open_does_not_restamp_an_existing_ledger` covers only the
  sequential case, so this is narrow. Worth knowing that softening `extra`
  softened this too.
- **`process_one` leaves `<id>.json.tmp` on a hard kill** (SIGKILL / runner
  timeout, where the `except` never runs). Harmless to every consumer checked —
  `skip_already_staged`, `audit-items --dir`, and the `find -name "*.json"`
  counts all miss it — but `scripts/s3_sync-ci.sh` is worth a look if it syncs
  the directory wholesale.

---

## Checked and sound

Re-verified this pass, over the current working tree:

- **The staged content check** (`item_migrate.py:171-190`). `probe1.py` — five
  pre-#34 item bodies in the out-dir, nothing in the manifest. Before the fix:
  `Complete: all 5 published items are migrated`, exit 0, zero items migrated.
  After: `5 staged item(s) are NOT in the migrated shape`, exit 1. The fix holds
  for the input that motivated it *and* for the harder one (a stale local build
  rather than a same-run rebuild), because it reads the file instead of trusting
  its existence.
- **`git reset` / `git checkout` on an untracked path.** Probed both. `reset`
  unstages an `A`-staged untracked file and exits 0; the `git ls-files
  --error-unmatch` branch correctly declines to `checkout` it. No path where the
  discard silently fails.
- **`steps.sync.outcome` in every state.** `skipped` when the step's `if:` is
  false, `skipped` when a prior step failed, `cancelled`/empty on job
  cancellation — all `!= "success"`, all discard. Fails toward redo, which is the
  safe direction. No monthly path writes a rewrite manifest, so a legitimate
  no-sync month discards nothing that exists.
- **`item_register.sh:82-86` `NDJSON_ARGS` under `set -u` on bash 3.2.** The
  array is initialised with two elements before any conditional append, so the
  empty-array expansion trap is unreachable.
- **`catalogue_register.sh` COLLECTION_ID block (`:57-66`).** Assign-then-test,
  not tested inline; a failed substitution yields `""` and exits 1 with both
  causes named. `2>/dev/null` is on a read, not a mutation. The
  `FILE_COLLECTION_ID` reconciliation (`:120-140`) is the right check —
  collection id and bucket name are genuinely two facts now and neither is
  derived from the other.
- **`catalogue_register.sh` audit coverage.** Runs for `all`, `drift` and
  `ids-file`; `verify` and `dryrun` exit before it and register nothing.
  `--expect "$N_TODO"` is reconciled against `N_URLS` at `:252`, and `N_URLS` is
  counted from `urls.txt` — the artifact the fetch loop actually iterates — so
  count and counted share a producer.
- **`collection_patch.py:247-297` item-link invariant.** `item_links_before` is a
  list of strings snapshotted before the in-place mutation, and `expected` is the
  full ordered prediction rather than an allowlisted difference. Count mismatch
  and per-href mismatch are reported separately. Correct at 102,460 links; the
  temp-file-then-`os.replace` is right.
- **`register_manifest.audit_items` and the `audit-items` CLI.** An empty path
  list returns 1 with a message rather than a vacuous pass; `unreadable` is
  reported rather than skipped; offenders are paths, not counts. `--dir` listing
  and the `find` that produces `--expect` in the monthly step agree.
- **`manifest_load` / `manifest_open`** against all four inputs: absent → empty,
  zero-byte → empty, non-empty headerless → raises, foreign header → raises. A
  final line with no trailing newline is retained (`:120` iterates the handle, and
  there is a test for it).
- **`item_rewrite.process_one` atomic write.** Temp file + `os.replace`, with
  cleanup on the exception path. `tests/test_item_migrate.py::test_a_written_item_is_never_left_truncated`
  exercises it in both directions — failure leaves no file, and the same call
  succeeds once the writer works, so the test is about the failure rather than a
  path that never worked.
- **`tests/test_collection_identity.py`.** Both scanner directions are asserted
  (`test_the_scanner_strips_prose_but_not_data` keeps a non-docstring
  triple-quoted string in scope), the premise that the bucket still carries the
  old name is asserted rather than assumed, and workflows are in the file set —
  so the boundary is the publishing surface rather than `scripts/`.
- **`tests/test_asset_key.py`.** The scanner is proved able to find a literal in
  all three positions it looks at, and proved not to fire on media types or
  `PRODUCT_TOKENS`. `test_there_are_scripts_to_check` pins the premise. No
  exemption list — `item_backfill`'s legacy literal lives in a named constant and
  is asserted in place rather than exempted, which is the right shape.
