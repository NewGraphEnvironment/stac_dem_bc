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
