# Task: Rename the repo to stac_elevation_bc, and move the OIDC trust condition with it (#40)


The collection is now `stac-elevation-bc` (#34, v2.0.0). The repo is still
`stac_dem_bc`, so the name describes one of several products the catalogue
carries — the same mismatch #34 existed to remove, moved one level out.

#34 deliberately scoped this out. It has a **different blast radius**: nothing a
data consumer sees changes, and GitHub permanently redirects old URLs, both web
and git remote. What it does break is CI.

## Phase 1: Rename inside this repo (branch `40-rename-the-repo-to-stac-elevation-bc-and`)
- [x] `README.Rmd`: Pages URL (line 130) and the `github.com/.../stac_dem_bc/issues/N` links → `stac_elevation_bc`; re-render `README.md` with the two-pass `rmarkdown::render` shown in its header
- [x] `NEWS.md` line 1 heading → `# stac_elevation_bc`; add an Unreleased line saying the repo was renamed, the old Pages URL is gone and github.com URLs redirect
- [x] `DESCRIPTION` `Title:` → `stac_elevation_bc` (`Package: stac.dem.bc` → `stac.elevation.bc`; it's a `Type: Project` manifest, so nothing installs it by name)
- [x] `git mv stac_dem_bc.Rproj stac_elevation_bc.Rproj`
- [x] `data-raw/make_hexsticker.R` `package_name`, then regenerate `man/figures/logo.png` (and the small logo if the script writes one)
- [x] `scripts/README.md:213` Actions link; `CLAUDE.md` names table (line 38 → done, #40), File Locations paths, and the three-names prose
- [x] `tests/test_collection_identity.py:46`: assertion kept (it guards the `~/Projects/gis/stac_dem_bc` path string, which stays); comment updated
- [x] Repo-wide grep: every remaining `stac_dem_bc` outside `planning/`, `logs/`, `data/`, NEWS history and `~/Projects/gis` paths is either justified or fixed
- [x] `pytest` passes

## Phase 2: rtj PR (trust plus references)
- [x] `env/prod/main.tf`: `repo = "NewGraphEnvironment/stac_elevation_bc"` (the module label `stac_dem_bc_update` and the output name stay: renaming them would make tofu destroy and recreate the role)
- [x] `docs/stac-endpoints.md:28` repo→collection row; `modules/gha_s3_role/README.md:49`
- [x] `scripts/geobuild/tests/test_gh-auth.sh:16-19` → new name. `cloud-init.yaml` left as is: `gh repo clone` follows the redirect, and editing `user_data` would replace a droplet
- [x] Open the rtj PR, link it to rtj#255: rtj#400. `tofu plan` **not verified**: env/prod keeps local state, which isn't on this machine (a plan here reads no state and shows `2 to add`). Moved to Phase 3, run where the state lives

## Phase 3: Cutover (one sitting, in this order)
- [ ] **You:** merge the rtj PR and run `tofu apply` (I'll hand you the bare command; first output to check is the plan's `1 to change, 0 to add, 0 to destroy`)
- [ ] Rename on GitHub: `gh repo rename stac_elevation_bc` (I run it after you confirm the apply)
- [ ] `gh repo edit --homepage https://www.newgraphenvironment.com/stac_elevation_bc/`; check the new Pages URL returns 200 and record what the old one returns
- [ ] Check `gh api .../actions/oidc/customization/sub` shows `repo:NewGraphEnvironment/stac_elevation_bc`
- [ ] Dispatch `update.yml` on `main` and confirm `AWS credentials` passes. **Note:** there's no dry-run input, so this is a real incremental monthly run (the same job the Nov 3 cron would do), ~6–90 min

## Phase 4: Inbound links in other repos (dead Pages URL)
- [x] `flooded/README.md:46` (and its source, if it's rendered from one) → PR
- [x] `restoration_wedzin_kwa_2024/0050-executive-summary.Rmd:84` (+ CLAUDE.md table row) → PR restoration_wedzin_kwa_2024#164; flooded#88. Both merge after the rename
- [x] Leave proposals, compost drafts and planning archives as they are (history, or already submitted)

## Phase 5: Close out
- [ ] `/code-check branch`, `/planning-archive`, `/gh-pr-push` (the PR body links the rtj and other-repo PRs)
- [ ] After merge (you, outside this session, since it moves the session's cwd): `mv ~/Projects/repo/stac_dem_bc ~/Projects/repo/stac_elevation_bc`, and move `~/.claude/projects/-Users-airvine-Projects-repo-stac-dem-bc/memory/` to the new path's project folder. I'll give you both commands

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean (each commit, or once over the branch with `/code-check branch`)
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
