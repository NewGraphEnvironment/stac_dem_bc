# Code-check round 2 — #40 repo rename (stac_dem_bc, the infrastructure PR, flooded#88, restoration_wedzin_kwa_2024#164, newgraphenvironment.github.io#60)

Reviewed 2026-10-17 against scratchpad/diff2.txt, the full files on each branch
(`origin/stac-elevation-bc-repo-rename` for the four other repos; HEAD for this repo), the live
GitHub API (OIDC sub customization, both Pages configs), and the checklist sections that touch
this diff (CI monitoring, infra state/plan, credential timing, tracked-artifact links).

## Findings

None that break anything. Two prose claims are wrong or will become wrong. Both are low severity:

- **[fragile]** rtj `docs/stac-endpoints.md:28`: "(renamed from `stac_dem_bc` 2026-10-17)" gives the
  rename date ahead of time. The rename is step 3 of a cutover that waits on a `tofu apply` on the state host.
  If that slips past today, the table states a false date and nothing will correct it. Either
  drop the date or fill it in during the cutover.
- **[fragile]** `planning/active/task_plan.md:487` (Phase 1, checked): "add an Unreleased line saying
  ... the old Pages URL is gone". The NEWS entry that landed says the old path is now a redirect
  page (NEWS.md:18-20), and that is correct once #60 merges. The checked box describes text that
  isn't in the tree, which fails the "PWF checkboxes match landed work" validation item. Reword
  the box.

Inconsistency, not a defect: this PR body's cutover step 2 says "merge the infrastructure PR, `tofu plan`, then
apply", while the infrastructure PR's own Order step 2 says "pull this branch, then `tofu plan`". Merging before
the plan is verified would leave rtj main describing an unapplied trust if the plan shows a
surprise. rtj has no workflows, so nothing auto-applies on merge. Use one order in both bodies,
preferably plan and apply from the branch, then merge.

## Checked and clean

- **Redirect page is deployed verbatim at `/stac_dem_bc/index.html`.**
  - `hugo.yaml` runs `hugo --gc --minify` and uploads `./public`.
  - `config.toml` has no `[module]`/mounts, no `staticDir`, `publishDir` or `uglyURLs`, so the
    default `static/` mount applies, and Hugo copies static files untouched (`--minify` touches
    rendered output only).
  - The theme (hugo-apero) is vendored, not a submodule.
  - No content, layout, data or alias produces `/stac_dem_bc/`, so nothing collides.
  - `.gitignore` does not ignore `static/stac_dem_bc`, and the file is in the branch tree.
- **Serving order.** The user site is `build_type: workflow`, cname `www.newgraphenvironment.com`.
  The project site is legacy, `main` at `/`, no cname of its own, so it inherits the org domain.
  While a Pages-enabled repo named `stac_dem_bc` exists, its project site shadows the user
  site's `/stac_dem_bc/`. Once the repo is renamed, the path falls through to the user site.
  So merging #60 after the rename (step 7) is right, and merging it earlier would only be
  shadowed, not harmful.
  - "Check the old URL redirects" needs the Hugo deploy run to finish first, because a deploy
    commit is not yet a served page. That is implicit in "merge, then check".
  - Old deep links (for example `/stac_dem_bc/README.html`) will 404; only the directory index
    redirects. The PR text does not claim otherwise.
- **The meta refresh and canonical** both target the absolute new URL. A no-slash
  `/stac_dem_bc` gets GitHub's directory redirect to `/stac_dem_bc/`.
- **Hex image.**
  - `static/img/hex/stac_elevation_bc.png` is byte-identical to this branch's
    `man/figures/logo.png` (blob 6b4949b).
  - The deleted `stac_dem_bc.png` is referenced nowhere else on the branch outside
    `planning/archive`, and an org code search for `stac_dem_bc.png` returns 0 hits.
- **OIDC.** `gh api …/actions/oidc/customization/sub` returns
  `{"use_default":true,"use_immutable_subject":false,"sub_claim_prefix":"repo:NewGraphEnvironment/stac_dem_bc"}`.
  Step 4's check (read `sub_claim_prefix`, stop if it isn't the new name) is well-formed against
  that response.
- **Confirming run.**
  - `update.yml` has `concurrency: stac-update`, `cancel-in-progress: false`, and every dispatch
    input defaults to `false`, so a plain `gh workflow run update.yml --ref main` is an ordinary
    incremental run.
  - The early `AWS credentials` step (:135) assumes the role with the same trust, so a green
    step confirms the move even on a no-change month that never reaches the second assume
    (:495).
  - The "let it finish" fix is present in both PR bodies and in task_plan Phase 3.
  - Nothing in the workflows references the repo name (no `github.repository` guard).
- **rtj.**
  - The only functional change is `repo =` in `env/prod/main.tf`. The module label, output and
    bucket are unchanged, so the change is an in-place trust update.
  - Remaining `stac_dem_bc` strings are bucket names, history and logs, redirecting repo links,
    and the deliberately untouched cloud-init.
  - The rollback and the "never recreate `stac_dem_bc`" rule are both stated.
- **This repo.**
  - `git grep stac_dem_bc` outside `planning`/`logs`/`data` leaves only the role ARN, the
    `~/Projects/gis` / `stac/prod/stac_dem_bc` data paths (`scripts/README.md:133-134`,
    `scripts/build_safe.sh:28`), CLAUDE.md history and table, NEWS's own rename text, and the
    accepted test assertion.
  - `index.html` and `README.html` hold 0 hits.
  - Both logos are regenerated (`logo.png` and `logo_small.png` both changed).
- **flooded and wedzin branches** carry no remaining `newgraphenvironment.com/stac_dem_bc`
  outside the `docs/` and `planning/` folders.
- **NEWS.md Unreleased claims.** Repo, issue and remote URLs redirect. The collection, bucket,
  ids and hrefs are unchanged. The landing page moves. The old path becomes a redirect page on
  the org site. The trust moved and the role kept its name. All of these hold once the cutover
  steps run, and the PR merges at step 7, after them.
