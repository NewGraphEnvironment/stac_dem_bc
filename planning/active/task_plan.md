# Task: Automate monthly incremental catalog updates via GitHub Actions (#23)

The catalog is five months stale: the source URL inventory was last refreshed 2026-02-18, the S3 catalog last built 2026-02-11, and pgstac last registered 2026-02-13. Automation was scoped as Phases 3–4 of #5 but never re-tracked after that issue auto-closed, and its standing-VM premise no longer matches infrastructure. Adopt the water-temp-bc pattern: monthly GitHub Actions cron + `workflow_dispatch`, OIDC role provisioned in rtj (rtj#184), incremental pipeline run on the runner, catalog synced to S3, refreshed caches committed back to `main`.

## Phase 1: CI-safe pipeline fixes

- [x] `scripts/stac_utils.py`: `get_output_dir()` honors `STAC_OUTPUT_DIR` env override before mode defaults
- [x] `scripts/detect_changes.R`: tryCatch wrapper — R errors exit 2 (0 = no changes, 1 = changes, 2 = error); plausibility guard: fresh listing < 90% of cached → exit 2 without touching cache or outputs
- [x] `environment.yml`: add `pystac[validation]` and `requests` so the CI dep list and conda env stay consistent
- [x] New `scripts/s3_sync-ci.sh`: no-delete sync on default credential chain; items first (`--exclude "collection.json"`), `aws s3 cp` collection.json last; `--dryrun` passthrough; guards (`STAC_OUTPUT_DIR` set, dir non-empty, collection.json present)
- [x] New `scripts/urls_reconcile.py` (one-off helper): compute item-backed URLs from the validation CSV via the url→id mapping, rewrite `urls_list.txt` to that subset (dry-run default, `--apply` to rewrite) — re-flags the 2,107 never-built URLs on the catch-up run
- [x] Cold-path rehearsal (local): temp `STAC_OUTPUT_DIR` with only S3-fetched collection.json, synthetic 3-URL urls_new.txt → item_create → item_validate → `s3_sync-ci.sh --dryrun`; verified zero deletions planned, items-before-collection order, only new files uploaded (see findings.md 2026-07-18 rehearsal)

## Phase 2: Companion infra — rtj#184 (in ~/Projects/repo/rtj, fresh branch off rtj main)

- [x] Add `module "stac_dem_bc_update"` + ARN output to `env/prod/main.tf` (rtj branch `184-gha-s3-role-stac-dem-bc`, commit 984d00b, pushed)
- [x] Optional rider (user call): enable versioning on `stac-dem-bc` — committed separately (c81bcf4) so it can be dropped before apply if unwanted
- [x] `tofu plan` / `apply` in env/prod — user applied; merged as rtj PR #189 (with review commit 15e717f documenting the deliberate no-expiry-on-versioning decision); rtj#184 closed
- [x] Post role ARN to #23 (issuecomment-5013597947) — matches the ARN already in update.yml

## Phase 3: Workflow

- [x] Minimal `DESCRIPTION` for R CI deps (ngr pinned to SHA via Remotes, readr, fs), mirroring water-temp-bc — pinned 519c03b, the locally verified build
- [x] `.github/workflows/update.yml`: monthly cron (`23 9 3 * *`) + `workflow_dispatch`; `permissions: id-token: write, contents: write`; concurrency group (no cancel); `timeout-minutes: 330`; Python ≥3.10 pin; steps: checkout → setup R + deps → setup uv + `uv pip install` (incl. `pystac[validation]`, requests) → import smoke check (`python -c "import rasterio, rio_stac"`) → `configure-aws-credentials` (role_gha_stac_dem_bc) → detect changes with explicit exit-code capture (0 → early-exit success; 2 → fail; 1 → continue) → log `wc -l urls_new.txt` → branch: item steps only if urls_new.txt exists & non-empty (deletions-only month still reaches commit-back) → `urls_check_access.py --urls-file data/urls_new.txt` as warn-only (`continue-on-error`, CSV surfaced via artifact) → fetch collection.json from S3 → `item_create.py --incremental` → `item_validate.py --incremental --items-dir` (hard gate) → `s3_sync-ci.sh` → commit-back: `git add -A data/` (covers modifications, deletions, new access CSV), `git pull --rebase origin main`, push as bot → upload `logs/` artifact (always)
- [x] `scripts/README.md` Automation section: schedule, manual dispatch, failure triage (invalid-item-blocks-batch → `urls_invalid_items.txt` + `item_reprocess.py`; oversized-batch fallback = manual local run; public-repo 60-day cron auto-disable), registration step
- [x] CLAUDE.md: fix stale "Local STAC output" path note (S3 is the sole catalog copy)

## Phase 4: Catch-up run + verification (post-merge)

- [x] PR via `/gh-pr-push` (PR #24), merged 2026-07-18 as dca0298 (merge commit, branch deleted); SRED xref corrected to sred#8 per user
- [x] Seed catch-up: `urls_reconcile.py --apply` committed to main (308a441) — urls_list.txt trimmed to 58,019 item-backed URLs
- [x] Catch-up build ran LOCALLY 2026-07-18 18:03–21:39 PT (3.6 h): 40,021/40,021 items created (zero shortfall — parenthesized files included), validated, synced (items first, collection.json 17.7 MB last), caches committed (815d8f6). Count math exact: 58,019 prior + 40,021 new = 98,040 item links in the live collection.json
- [x] `workflow_dispatch` from main verified the steady-state path end to end (run 29673672894, success): R + uv setup, OIDC auth, fresh listing 98,039 vs cache 98,039, New 0 / Deleted 0, clean early exit with item steps skipped. Full count reconciliation: 58,019 − 1 upstream-deleted + 40,021 new = 98,039 live files; collection holds 98,040 links (retains the item for the deleted source; urls_deleted.txt audit = 1 line)
- [ ] Register on geoserv (`stac_register-pypgstac.sh stac-dem-bc ...`); verify pgstac count + API query at images.a11s.one returns a new item
- [ ] Confirm cron live; close #23 via docs commit ("monthly automation live; Fixes #23")

## Validation

- [ ] Cold-path rehearsal passed (dry-run: zero deletions, correct order)
- [ ] `/code-check` clean on each commit
- [ ] Catch-up run: new items on S3 + caches committed by bot + API returns a new item + count math documented in run log
- [ ] PWF checkboxes match landed work; `/planning-archive` on completion
