# Issue #34 — rename the collection to `stac-elevation-bc`, asset `image` → `dem`

**Closed:** 2026-09-01 · **Release:** `v2.0.0` (`f9de569`) · **PR:** #39 ·
**Downstream:** rtj#252, fly#46, stac_floodplains_bc#25

## Outcome

`images.a11s.one/collections/stac-elevation-bc` serves **102,460 items**, each
carrying `dem` and, where paired, `dsm`. `stac-dem-bc` is dropped and 404s. Item
ids and the S3 bucket are unchanged, so every asset href and download link is
byte-identical to v1.1.0 — the rewrite touched exactly two fields per item.

There was **no downtime**. The new collection was registered, verified by id set
equality in both directions, and confirmed with a real client query before the
old one was touched. That ordering is the whole design: the inverse — delete then
load — is what took this API to zero items on 2026-08-29.

## Measurement

| | |
|---|---|
| items rewritten in place | 102,460 — 0 unchanged, **0 errors** |
| rewrite duration | 12m35s (budget was ~60 min, from the #31 precedent) |
| full CI run | 48m51s |
| registration | 102,460 fetched, 0 failed |
| homogeneity in pgstac | 102,460 `dem`, **0** `image`, 95,888 `dsm` |
| set equality, ×3 | `IN SYNC: 102460 published, all registered, no orphans` |
| client query | 5 tiles; download `HTTP 200`, 195 MB |
| tests | 94 → 223 |
| review findings | 30 raised, 29 real, all fixed |

## What the doing taught, that the planning did not

- **A half-done rename is invisible to every check the repo had.** Item ids do
  not change, so set equality reports `IN SYNC` over a fully mixed catalogue;
  `item_register.sh` routes by each body's *own* `collection` field, so a stale
  item upserts into the old collection successfully; both keys are legal STAC;
  and a count of assets cannot tell `{image,dsm}` from `{dem,dsm}`. The property
  is **homogeneity, not size** — `audit-items`, run over every fetched body
  before anything reaches the database.

- **A progress manifest is a claim about a step that may not have run.**
  `run_rewrite` writes it on the local write; the CI cache commit is `always()`;
  the sync is skipped on failure. So a failed run committed a manifest asserting
  items were published that never reached S3 — and `todo = published - manifest`
  meant every later run skipped them, permanently, with the completeness check
  and the audit both passing. Found reviewing my own diff.

- **Two guards, each correct alone, interacting into a loop with no exit.** The
  completeness check made the error-tolerance gate unreachable: an errored id is
  always `missing`, so any error exited 1, which skipped the sync, which
  discarded the manifest. At 102,460 items against a measured history of 34 and
  2 transient failures, the migration could only ever finish on a run with zero
  failures. Fixed by splitting `missing` by **cause**.

- **A gate whose denominator is the run, on a resumable job whose runs shrink.**
  1 failure in 2 remaining items is a 50% rate. Same loop. The tolerance is now
  measured against the population.

- **A guard that fires correctly and then misdirects.** Four instances: the
  collection-id mismatch blaming the bucket; a deterministic both-keys failure
  advertised as re-runnable; a message promising a publish the run was about to
  discard. The diagnostic is part of the guard.

- **The monthly cron would have performed half a migration by itself.** The id
  lives inside `collection_patch`'s idempotence contract and the monthly run
  calls it, so the first cron after merge would have published a renamed
  `collection.json` over unmigrated bodies — and the monthly audit reads the id
  from that very file, so it would have passed green. Moving the id now requires
  `--allow-id-change`.

- **Five sibling gaps** between `item_migrate` and `item_backfill`, which share a
  harness and kept not sharing its fixes. That count is the signal, not the
  instances.

- **Fixes need the restore-the-bug check as much as code does.** Twice a fix
  shipped green in both directions — the shrinking denominator and a misleading
  message — and only restoring the defect revealed there was no test.

## Evidence

- CI run `33434241580` (rewrite + sync), artifact `run-logs`
- `planning/archive/.../review-round{1,2,3}.md` — the three subagent passes
- PR #39 conversation — four GitHub auto-review passes

## Note on the reviews

Six passes; five found a defect in the previous pass's fix. The sixth found none,
which is what convergence looked like — not a fixed number of rounds. Two
mechanisms account for most of it, and both are candidates for
`soul/conventions/code-check.md`: **guards that fire correctly then point at the
wrong fix**, and **fixes landing in one of two callers that share a harness**.

Closed by: PR #39 (`e8ea5b8`), release `f9de569`, tag `v2.0.0`
