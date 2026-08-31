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
