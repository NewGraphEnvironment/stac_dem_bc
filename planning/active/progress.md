# Progress — Store source URLs as `https://` (#51)

## Session 2026-10-06

- Plan-mode exploration — phases approved by user (dedupe + ngr merge/tag approved)
- Created branch `51-store-source-urls-as-https-migrate-the-c` off main
- Scaffolded PWF baseline from issue #51 with approved phases
- Next: start Phase 1 (ngr fix)
- Plan review (Plan agent): 1 blocker, 7 gaps, 3 ordering notes, 5 assumptions and 4 acceptance points; triage is in `review-plan.md`. Folded in: cache check before the walk (G6), CI must not skip (AC2), findings A1 corrected, and Validation changed to a two-join walk (AC1)
- Two-join walk: 575,438 keys; derived DEM, DSM and dsm_groups identical after the scheme repair; 0 pending changes against the committed caches
- ngr#38 fixed in ngr PR #39 (three code-check rounds; testthat floor raised to 3.1.7), merged, released as **v0.0.3**
- stac_orthophoto_bc#48 filed (unpinned consumer breaks on `https://`)
- Phase 2 code-check: round 1 found the walk guard was tested only through a copy in the fixture. Fixed with a `keys_get` injection parameter; round 2 clean
- Phase 2 round 3: no bugs; guarded the unguarded writer in `stac_create_collection.qmd` (fragile). Loop ended: round 3 had no finding inside a fix
- Phase 3: pinned ngr v0.0.3, migrated 4 caches (geotiff checks deduped to 102,460 rows); re-run is a no-op; real detect_changes.R on the result exits 0 with 0/0
- Phase 3 code-check: round 1 clean; the reviewer reproduced all four migrated files byte-for-byte from HEAD (the full candidate set), so the loop ended on that enumeration
