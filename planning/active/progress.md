# Progress — Rename collection to stac-elevation-bc, asset image to dem (#34)

## Session 2026-08-31

- Plan-mode exploration: three Explore agents (collection-id inventory, asset-key
  inventory, registration + test path) and two Plan agents (cutover sequence,
  tooling + test strategy). Every load-bearing claim re-probed directly before it
  entered the plan — see `findings.md`.
- Five claims verified rather than taken on report: the 98,040-vs-102,460
  manifest trap, `item_backfill.verify()`'s missing removal allowlist, the
  unscoped `search_body`, the root-link title as a fourth spelling of the id, and
  the workflow being active.
- User decisions: repo rename is a **separate issue**; cutover **finishes before
  the Sep 3 cron**; `stac-dem-bc` dropped **immediately after verify**.
- Created branch `34-rename-collection-to-stac-elevation-bc-a` off main
- Scaffolded PWF baseline with approved phases
- Scope for this branch: **Phases 0–5** (everything that lands as code), then PR.
  Phases 6–8 are the operational cutover and are triggered by hand.
- Next: Phase 0

### Phases 0-5 landed (all the code; 6-8 are the operational cutover)

| commit | what |
|---|---|
| `767f5aa` | Phase 0 — scope /search to one collection, compare asset keys not counts |
| `1f12a6b` | Phase 1 — extract `item_rewrite.py`; `test_item_backfill.py` green with zero edits |
| `8ecaf10` | Phase 1 — test the real error gate, not a local mirror |
| `ece7d45` | Phase 2 — asset key `image` -> `dem`, one definition, AST invariant |
| `f407438` | Phase 3 — collection id -> `stac-elevation-bc`, one definition |
| `13ce43f` | Phase 4 — `item_migrate.py` + the homogeneity gate |
| `f9b199b` | Phase 5 — CI wiring, and the standing monthly guard |
| `ff847ed` | self-review fix — manifest stranding, completeness suppression, `--limit 0` |
| `f3bd315` | docs — `scripts/README.md` |
| `52a3fbe` | self-review fix — atomic item writes |
| `c7ab3fa` | self-review fix — the mismatch error named the wrong cause |

Baseline captured before anything moved: `--verify` reported
**IN SYNC: 102,460 published, all registered, no orphans**, and the registered
id set is saved at `~/stac-dem-bc-baseline-ids.txt` (102,460 lines).

### Verified against the live catalogue, not fixtures alone

- 200 real published items migrated into scratch; one deepdiffed against its
  published original — **exactly two changes**, href byte-identical, `/dem/`
  intact, collection link still on the bucket.
- A structurally valid stale item dropped in among them: caught by `audit-items`
  and named by path, and refused by `ndjson_write` before it could reach pgstac.
- The CI audit step's own shell run verbatim: clean at 200/200, FAIL when the
  collection listed 202.
- Completeness with staged skips: 3 staged + 9 migrated reconciles to 12; an
  unfetchable item reports INCOMPLETE and names it.
- Every new guard restored-to-red before being trusted (11 restorations).

### Found by reviewing my own diff, before any reviewer reported

The most serious defect on the branch was mine, and it was the exact failure the
branch exists to prevent, arriving through its own progress file — see
`findings.md`, "Errors Encountered".

### Next

Phases 6-8 are operational and are triggered by hand: dispatch the rename,
register, verify, update downstream consumers, drop the old collection, release.
Target dispatch **Sep 1**, ahead of the Sep 3 cron.
