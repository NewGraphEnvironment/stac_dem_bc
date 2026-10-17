## Outcome

Prepared the rename of the repo `stac_dem_bc` → `stac_elevation_bc` so it matches the collection
it publishes (`stac-elevation-bc`, #34). The catalogue itself is unchanged. Across five repos:
this repo's links, NEWS, DESCRIPTION, `.Rproj`, logos and CLAUDE.md; the infrastructure PR moves
`role_gha_stac_dem_bc`'s OIDC trust to the new name, as an in-place update; the org website (#60)
gets a redirect page at the old Pages path, because GitHub Pages does not redirect a renamed
project site and the URL is already in proposals and emails; flooded#88 and
restoration_wedzin_kwa_2024#164 fix inbound links.

What was learned:
- The infrastructure's prod state lives on one machine only. A plan from any other machine shows phantom creates (`2 to add`).
- The confirming `update.yml` dispatch must not be cancelled: `Commit refreshed caches` runs
  under `always()` after detection has rewritten `urls_list.txt`, which also makes
  `scripts/README.md`'s "a failed run persists nothing" false (#66).
- Review kept finding the same defect: one cutover sequence restated by hand across a dozen
  documents, each true at one point of it. A hand-picked enumeration missed four instances.
  Only an enumeration over every added line ended the loop.

**Phase 3 (the cutover) and the post-merge clone move are a handoff run by the user after the PR
opens, so their boxes are open here by design.** The live checklist is in the PR body.

Closed by: PR for #40 (branch `40-rename-the-repo-to-stac-elevation-bc-and`)
