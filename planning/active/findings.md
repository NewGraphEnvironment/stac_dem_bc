# Findings — Rename the repo to stac_elevation_bc (#40)

## Issue context

## Problem

The collection is now `stac-elevation-bc` (#34, v2.0.0). The repo is still
`stac_dem_bc`, so the name describes one of several products the catalogue
carries — the same mismatch #34 existed to remove, moved one level out.

#34 deliberately scoped this out. It has a **different blast radius**: nothing a
data consumer sees changes, and GitHub permanently redirects old URLs, both web
and git remote. What it does break is CI.

## What actually breaks: AWS OIDC

`rtj/modules/gha_s3_role/main.tf` builds the trust condition from the repo name:

```hcl
subs = [for b in var.branches : "repo:${var.repo}:ref:refs/heads/${b}"]
```

fed by `rtj/env/prod/main.tf`:

```hcl
repo = "NewGraphEnvironment/stac_dem_bc"
```

Rename the repo and the monthly workflow can no longer assume its role — the
`sub` claim in the OIDC token stops matching. The S3 sync fails, and it fails on
the 3rd of a month rather than at the moment of the rename, which is the awkward
part.

**The role ARN is safe.** `role_gha_*` derives from the **bucket**, not the repo:

```hcl
role_name = "role_gha_${replace(var.bucket, "-", "_")}"
```

so `role_gha_stac_dem_bc` stays correct and `update.yml:85` needs no edit. Only
the trust condition moves. (A *bucket* rename would be the opposite, and is a
third and separate decision — see #34 for why the bucket stays.)

## Order matters

Apply the rtj change **first**, or in the same sitting. The trust policy can name
a repo that does not exist yet, so:

1. rtj: `repo = "NewGraphEnvironment/stac_elevation_bc"`, `tofu apply`
2. rename the repo on GitHub
3. dispatch `update.yml` to confirm the OIDC assume-role still works — do not
   wait for the cron to find out

## Also carries the old name

- local clones at `~/Projects/repo/stac_dem_bc` (git remotes redirect, paths do not)
- `stac_dem_bc.Rproj`, `DESCRIPTION`'s `Title:`
- the pkgdown/Pages URL `newgraphenvironment.com/stac_dem_bc`, which is in
  `README.Rmd` and in `rtj/docs/stac-endpoints.md`'s repo→collection table
- `CLAUDE.md` paths, and this repo's own `planning/archive/`

Underscores, not hyphens — a different string from either the collection or the
bucket, which is worth stating because all three now differ:

| thing | name |
|---|---|
| repo | `stac_dem_bc` → `stac_elevation_bc` |
| collection | `stac-elevation-bc` |
| S3 bucket | `stac-dem-bc` (unchanged) |

## What happens if we never do

Nothing breaks. This is tidiness, and the cost of leaving it is that the repo
name is one more thing to explain to whoever picks the catalogue up next.

Relates to #34, NewGraphEnvironment/rtj#255



## Measured at plan time (2026-10-17)

- `gh api repos/NewGraphEnvironment/stac_dem_bc/actions/oidc/customization/sub` →
  `use_immutable_subject: false`, prefix `repo:NewGraphEnvironment/stac_dem_bc`. The rename
  therefore changes the token's `sub`, and `rtj/env/prod/main.tf:114` must move with it.
- rtj#395 (open, not landed): repos created after ~2026-10 get immutable subjects
  (`repo:Org@id/repo@id`), and `gha_s3_role` cannot express them yet. Not this issue's scope;
  the repo-string change is sufficient for a mutable-subject repo.
- Pages: `html_url` https://www.newgraphenvironment.com/stac_dem_bc/, no CNAME of its own;
  repo `homepage` carries the same URL.
- Org-wide: 393 code-search hits on `stac_dem_bc`; functional ones outside this repo are only in
  rtj (trust, geobuild cloud-init, gh-auth test). Live inbound Pages links: flooded README,
  restoration_wedzin_kwa_2024 exec summary, and the org website (home page pipeline, open-source
  page). rtj docs/stac-endpoints.md carries the repo *name*, not the Pages URL (corrected after the
  plan review).
- `update.yml` has no dry-run input; a plain dispatch is a real incremental run.

## Plan review and code-check round 1 (2026-10-17)

- `review-plan.md`: rtj prod state is on m4 only; the org website links the old Pages URL and can
  host a redirect at `/stac_dem_bc/` (it owns `www.newgraphenvironment.com`). Adopted.
- `review-round1.md`: don't cancel the confirming dispatch. `Commit refreshed caches` runs under
  `always()` after `detect_changes.R` has rewritten `urls_list.txt`, so a cancel after detection
  strands that month's new files. The wider docs error is #66.

## Errors Encountered

| Error | Resolution |
|-------|------------|
| `tofu plan` (rtj env/prod, targeted) showed `2 to add` for the role | env/prod's `backend.tf` is empty (local state) and no `terraform.tfstate` is on this machine; the plan read no state. Not a real diff. Plan and apply must run where that state lives. |

## Code-check round 2 and the enumeration (2026-10-17)

Round 2 (`review-round2.md`) found three prose defects, two inside round 1's fixes (a Phase 1 box
left describing the pre-redirect NEWS; this PR and rtj#400 giving different merge orders), plus a
rename date in rtj docs that preceded the rename. Mechanism: the cutover order, the old Pages URL's
fate and "already renamed" claims are each restated by hand across documents. Enumerated all nine
(rtj#400 body, this PR body, flooded#88, wedzin#164, website#60 bodies, task_plan, findings, NEWS,
CLAUDE.md) for each claim class. The enumeration found one more instance (flooded/wedzin bodies
said the old link "would stop working", written before the redirect); fixed. All nine now agree:
plan and apply on m4 from the rtj branch, merge rtj#400 after the apply, never cancel the
confirming dispatch, the old path redirects via the org site.

## Code-check round 3 and the terminal enumeration (2026-10-17)

Round 3 (`review-round3.md`) named the mechanism: one cutover sequence restated by hand in ~a dozen
places, each statement true at only one point of it and none saying which. Round 2's enumeration
had checked a hand-picked list of nine documents; round 3's four findings sat in files it missed
(CLAUDE.md clone path, the rollback's scope in three places, review-plan.md G10, archive timing),
plus a misquoted condition in #66. All fixed.

Terminal enumeration: every added line in all five branch diffs plus all five PR bodies and #66
(569 lines), grepped for the two claim classes (instructions: cancel/rollback/revert/merge/archive/
apply/mv; sequence-state: renamed/is now/redirect/404/stop/gone/dates), each hit read. All agree;
the only early statements are rtj#400's docs/comment, merged minutes before the rename (accepted).
