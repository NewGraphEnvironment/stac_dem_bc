# Progress — catalogue_register.sh audits for a 'dem' asset, so it cannot register another collection (#42)

## Session 2026-09-27

- Plan-mode exploration — phases approved by user ("go all phases to pr")
- Created branch `42-catalogue-register-sh-audits-for-a-dem-a` off main
- Scaffolded PWF baseline from issue #42 with approved phases
- Next: start Phase 1
- Phase 1: `tests/test_catalogue_register.py` — 11 e2e cases, offline (file:// hrefs,
  PATH ssh stub counting write attempts). All 11 red against main, for the right
  reason: the collection upsert runs before the audit, so even the control case dies
  at the stub before the audit prints anything.
- Phase 2: per-catalogue asset rules; audit moved above the collection upsert.
  Plan review + /code-check rounds 1–3 (4 reviewer agents). Each of the plan
  review, round 1 and round 2 found a hole in the previous fix of one class —
  "whose catalogue is this" inferred from an address (id → bucket string →
  bucket name → item hrefs). Ended by enumeration: 102,460/102,460 live item
  hrefs parse to `stac-dem-bc`. Round 3: no defect inside round 2's fix; one
  latent fail-closed finding recorded for #35.
- Mutations run, each red on its target tests: hard-coded dem (3), old order (8),
  env override honoured (2), bucket string compare (4 aliases), href check off (2).
- Live read-only: audit-items over stac_airphoto_bc/data/stac — new rules OK 10,100,
  old rules FAIL 10,100 `lack asset 'dem'`.
- Tests: 265 passed under /bin/bash 3.2.
- Phase 3: scripts/README.md (landed in the Phase 2 commit via `git add scripts/`),
  NEWS.md Unreleased entry. The "before anything reaches the database" claims in
  scripts/README.md, CLAUDE.md and NEWS v2.0.0 are now true after the reorder; the
  v2.0.0 overstatement is corrected in the new entry rather than rewritten.
- #35 body: registration constraint under option 2 (round 3's latent finding).
- Filed stac_airphoto_bc#33 to switch its registration to `--drift` after merge.
