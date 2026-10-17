# Code-check round 1 — #40 repo rename (stac_dem_bc, the infrastructure PR, flooded#88, restoration_wedzin_kwa_2024#164)

Reviewed 2026-10-17 against: the diff in scratchpad/diff.txt, the full current files on each
branch, index.html / README.html and the regenerated logo in the working tree, and the open PR
bodies (the infrastructure PR, flooded#88, restoration_wedzin_kwa_2024#164).

## Findings

- **[fragile]** the infrastructure PR PR body, "Order" step 4 — "Dispatch `update.yml` there and confirm
  the `AWS credentials` step passes. You can cancel the run once that step is green."
  Cancelling is not safe at every point after that step. `Commit refreshed caches`
  (`.github/workflows/update.yml:536-597`) is gated on
  `always() && (steps.detect.outputs.changes == 'true' || ...)`, and `always()` runs on a
  cancelled run. `scripts/detect_changes.R:141` has already rewritten `data/urls_list.txt` to
  the fresh listing by the time `detect` exits 1. So a cancel that lands after `Detect changes`
  has finished on a month with new files commits the new URLs to `urls_list.txt` with no items
  built or synced. The next run's change detection treats them as already seen, and they are
  never built. That is the stranding class `scripts/urls_reconcile.py` was written to repair.
  The commit step's discard block covers only the rewrite manifests
  (`migrate_done`/`backfill_done`/`footprint_done`), not `urls_list.txt`.
  A cancel *during* `Detect changes` is safe, because `changes` is never set. Fix the
  prose: either say "let the run finish" (the plan's Phase 3 already budgets 6–90 min for it),
  or "cancel only while `Detect changes` is still running, never after it".
  Background, not in this diff: `scripts/README.md:206` ("a failed run therefore persists
  nothing and the next run re-detects cleanly") and CLAUDE.md's "a failed month self-heals,
  because nothing was committed" are false since the commit step became `always()`. A run
  that fails after detect with changes also commits `urls_list.txt`. That is worth its own
  issue, and it is why the cancel advice above is hazardous.

## Checked and clean

- **OIDC trust is updated in place.** `modules/gha_s3_role/main.tf` uses `var.repo` only in
  `local.subs`, so in `aws_iam_role.this.assume_role_policy`. `name` derives from the bucket,
  and `aws_iam_role_policy.s3` keys on the unchanged role name. Changing `repo` is therefore one
  in-place update. Expecting `0 to add, 1 to change, 0 to destroy` is correct, provided the
  plan reads the state host's state.
- The module label, output and role name are unchanged. `update.yml:135` and `:495` still
  name `role_gha_stac_dem_bc`, which is correct.
- update.yml has no `environment:` key, so the sub is `repo:<org>/<repo>:ref:refs/heads/main`,
  which matches the module's form. It has no `github.repository` guard and no hard-coded repo
  URL. `git push origin HEAD:main` uses the checkout's remote.
- Pages comes from `main` at `/`, legacy build. Neither index.html nor README.html contains
  `stac_dem_bc`. The landing-page DT table rendered (4 columns × 64 rows), and the "Please see"
  fallback appears only in README.md and README.html, as intended.
- The published `collection.json` carries no repo or Pages URL (root/self/related links only),
  so the catalogue needs no change.
- No test reads `Package:` or the `.Rproj` name. pytest on a `git archive HEAD` copy: 314 passed,
  1 failed. The failure is `test_urls_scheme`, which calls `git ls-files` and fails only because
  the copy is not a git repo. The claimed 315 is consistent with that.
- Logo: `stac_elevation_bc` fits inside the hex.
- Org-wide functional references: code search finds `stac_dem_bc` under `.github` or in
  toml/yml only as comments in sibling repos (stac_pointcloud_bc, stac_uav_bc, stac_airphoto_bc,
  stac_floodplains_bc), plus rtj's cloud-init. `gh repo view NewGraphEnvironment/awshak`
  resolves to rtj, so old-name `gh` calls keep working after a rename, and leaving cloud-init
  alone is sound.
- Inbound Pages links: the 23 code-search hits are flooded, wedzin, this repo, and history
  (compost drafts, proposals, planning archives). spoke is private and has no Pages, so its
  deck links are not live.
- flooded has no README.Rmd, so editing the `.md` directly is correct. wedzin's `docs/` is
  stale until the next book build, and the PR body says so. Both edited CLAUDE.md lines sit
  above the soul marker.
- NEWS.md's Unreleased entry and CLAUDE.md's names table and path line are accurate once
  Phase 3 and the post-merge `mv` happen. The PR merges after the rename.
- No local tooling (soul scripts, rtj, sibling repos, settings, LaunchAgents) depends on the
  path `~/Projects/repo/stac_dem_bc`. The only references are logs.

Not a finding: rtj's `test_gh-auth.sh` now probes the new name. The old name would have kept
resolving after the rename. The new name fails only in the minutes between merging the infrastructure PR
and the rename, and that window is meant to be a single sitting.
