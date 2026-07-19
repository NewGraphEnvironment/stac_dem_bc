# Progress — Automate monthly incremental catalog updates via GitHub Actions (#23)

## Session 2026-07-18

- Investigated why #5 closed (PR #9 "Closes #5" auto-close with Phases 3–4 unbuilt); filed #23 + companion rtj#184
- Plan-mode exploration of pipeline contracts, water-temp-bc reference workflow, rtj infra (subagent survey)
- Adversarial Plan-agent review: 2 blockers + 8 gaps, all absorbed (see findings.md disposition)
- Phases approved by user via plan mode
- Pushed main (CLAUDE.md sync commit), created branch `23-automate-monthly-incremental-catalog-upd`
- Archived issue #13 PWF → `planning/archive/2026-02-issue-13-url-accessibility/` (commit c72ecf0)
- Scaffolded PWF baseline from issue #23 with approved phases
- Phase 1 complete: STAC_OUTPUT_DIR override, detect_changes exit contract + plausibility guard, environment.yml deps, s3_sync-ci.sh, urls_reconcile.py; cold-path rehearsal passed (zero deletes, correct order, dedupe verified)
- Live ngr fetch: objectstore at 98,039 URLs (+37,900 since Feb) → catch-up moved to local run, dispatch verifies steady state (task_plan Phase 4 amended; findings.md has the numbers)
- Phase 3 complete: DESCRIPTION (ngr@519c03b), update.yml (16 steps, ASCII-clean, YAML-validated, 2 code-check rounds — zero-created gate, artifact overwrite, shortfall warning), README Automation section, CLAUDE.md path fix
- Phase 2 code done: rtj branch `184-gha-s3-role-stac-dem-bc` pushed (984d00b role module; c81bcf4 optional versioning rider as droppable commit); rtj checkout returned to main
- Phase 2 complete: user applied on M1; merged as rtj PR #189, rtj#184 closed, versioning live (no-expiry documented), ARN posted to #23
- PR #24 opened and merged (dca0298, merge commit); SRED xref corrected to NewGraphEnvironment/sred#8 (CLAUDE.md updated d4724ab)
- Seed committed to main (308a441); LOCAL catch-up build launched 18:03 PT under caffeinate (log: logs/20260718_180343_catchup_build.log) — detect → build ~40k → validate → sync → cache commit, est ~5-6 h
- Next: on build completion verify count math, run workflow_dispatch steady-state check, register on geoserv, close #23

## Session 2026-07-19 (close-out)

- Registration on geoserv: register script died at NDJSON concat (ARG_MAX, 98k files — rtj#196) after a complete download; rescued manually via find -exec concat. Count guard then caught 90 empty fetches = the parenthesized space-URL items (root cause filed as #25); refetched %20-encoded, 98,040 lines loaded via pypgstac upsert
- API verified: new 2024 item, 2017 original, and a parenthesized item all resolve; catalog 58,019 → 98,040
- Also filed rtj#193 (m1 lacks geoserv ssh access — diagnosis ran through m4)
- #23 closed via docs commit; archiving PWF
