# Plan review — #40 (Plan agent, 2026-10-17)

Read-only reviewer; findings returned as reply text and recorded here with dispositions.

| id | finding | verified | disposition |
|---|---|---|---|
| B1 | rtj `env/prod` state is on m4 only; a plan/apply from m1 proposes ~79 creates | yes, rtj/CLAUDE.md:369; m1 plan showed `2 to add` for the module | rtj#400 body: plan/apply on m4, saved plan, `role-assume.sh` inside the m4 shell |
| G1 | org website links the old Pages URL (layouts/index.html:359, open_source_software/index.md:154), carries a hex copy and a repo row | yes | newgraphenvironment.github.io#60 |
| G2 | sent proposals/emails carry the old Pages URL; redirect page on the user site (owns www.newgraphenvironment.com) | CNAME confirmed `www.newgraphenvironment.com`, build_type workflow | adopted: `static/stac_dem_bc/index.html` in #60; merge after rename; NEWS reworded. Rule: never recreate a repo named `stac_dem_bc` |
| G3 | moving the clone breaks `.venv` (hardcoded shebangs/VIRTUAL_ENV) | not probed; plausible for any venv | handoff: recreate the venv after the `mv` |
| G4 | `gh repo rename` needs `--yes` non-interactively; check `origin`; m4/geobuild clones | — | cutover steps + handoff |
| G5 | editing geobuild cloud-init replaces the droplet | consistent with the decision already taken | cloud-init untouched |
| G6 | wedzin's live outputs are committed renders (docs/) | yes, docs/ exists | PR #164 says the book picks it up on next build; the redirect covers the rendered copies meanwhile |
| G7 | no rollback, no path for an immutable `sub` | — | cutover: stop if prefix ≠ `repo:NewGraphEnvironment/stac_elevation_bc`; rollback in rtj#400 |
| G8 | Pages may need a manual build after rename | build_type legacy | cutover: `gh api -X POST …/pages/builds` if new URL ≠ 200 |
| G9 | rtj docs/dem-sources.md:71, geoserv README:144 | redirecting repo links | left (redirect) |
| G10 | cancel the confirming dispatch once `AWS credentials` is green | overturned by code-check round 1 | **rejected**: let it finish. `Commit refreshed caches` runs under `always()` after detection rewrote `urls_list.txt` (#66) |
| O1 | no in-flight update.yml run at apply time | update.yml re-assumes before sync (#60) | rtj#400 step 1 |
| O2–O6 | gate dispatch on sub prefix; redirect after rename; `gh` from clone after origin rewrite; rtj#395 may land first; merge this PR soon after rename | — | folded into cutover order |
| A1 | tofu change is in place | yes (module main.tf) | — |
| A2/A3 | issue body cites `update.yml:85` (actual :135/:495); findings said rtj docs carries the Pages URL (it carries the repo name) | yes | findings corrected; issue body fixed at merge |
| A6 | Zenodo webhook on releases; never fired (tags only) | — | noted in the PR; nothing to change |
| S1 | keep the test assertion on `stac_dem_bc` | — | already kept |
| S2 | worktree line deletion is unrelated cleanup | — | stated in progress.md |
| S3 | re-render pulls unrelated index.html changes; README.html stale | render regenerated README.html too (no old-name hits remain) | stated in the PR |
