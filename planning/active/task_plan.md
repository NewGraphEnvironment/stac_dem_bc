# Task: Automate monthly incremental catalog updates via GitHub Actions (#23)

The catalog is five months stale: the source URL inventory was last refreshed 2026-02-18, the S3 catalog last built 2026-02-11, and pgstac last registered 2026-02-13. Automation was scoped as Phases 3–4 of #5 but never re-tracked after that issue auto-closed, and its standing-VM premise no longer matches infrastructure. Adopt the water-temp-bc pattern: monthly GitHub Actions cron + `workflow_dispatch`, OIDC role provisioned in rtj (rtj#184), incremental pipeline run on the runner, catalog synced to S3, refreshed caches committed back to `main`.

## Phase 1: CI-safe pipeline fixes

- [ ] `scripts/stac_utils.py`: `get_output_dir()` honors `STAC_OUTPUT_DIR` env override before mode defaults
- [ ] `scripts/detect_changes.R`: tryCatch wrapper — R errors exit 2 (0 = no changes, 1 = changes, 2 = error); plausibility guard: fresh listing < 90% of cached → exit 2 without touching cache or outputs
- [ ] `environment.yml`: add `pystac[validation]` and `requests` so the CI dep list and conda env stay consistent
- [ ] New `scripts/s3_sync-ci.sh`: no-delete sync on default credential chain; items first (`--exclude "collection.json"`), `aws s3 cp` collection.json last; `--dryrun` passthrough; guards (`STAC_OUTPUT_DIR` set, dir non-empty, collection.json present)
- [ ] New `scripts/urls_reconcile.py` (one-off helper): compute item-backed URLs from the validation CSV via the url→id mapping, rewrite `urls_list.txt` to that subset (`--dry-run` default) so detect_changes re-flags the ~2,098 never-built URLs on the catch-up run
- [ ] Cold-path rehearsal (local): temp `STAC_OUTPUT_DIR` with only S3-fetched collection.json, synthetic 2–3-URL urls_new.txt → item_create → item_validate → `s3_sync-ci.sh --dryrun`; assert zero deletions planned, items-before-collection order, only new files uploaded

## Phase 2: Companion infra — rtj#184 (in ~/Projects/repo/rtj, fresh branch off rtj main)

- [ ] Add `module "stac_dem_bc_update"` + ARN output to `env/prod/main.tf` (block as written in rtj#184)
- [ ] Optional rider (user call): enable versioning on `stac-dem-bc` in `var.s3_buckets` — no versioning today and collection.json is overwritten in place every run
- [ ] `tofu plan` / `apply` in env/prod (apply is collaborative — user credentials)
- [ ] Post role ARN to #23; commit in rtj closes rtj#184

## Phase 3: Workflow

- [ ] Minimal `DESCRIPTION` for R CI deps (ngr pinned to SHA via Remotes, readr, fs), mirroring water-temp-bc
- [ ] `.github/workflows/update.yml`: monthly cron (`23 9 3 * *`) + `workflow_dispatch`; `permissions: id-token: write, contents: write`; concurrency group (no cancel); `timeout-minutes: 330`; Python ≥3.10 pin; steps: checkout → setup R + deps → setup uv + `uv pip install` (incl. `pystac[validation]`, requests) → import smoke check (`python -c "import rasterio, rio_stac"`) → `configure-aws-credentials` (role_gha_stac_dem_bc) → detect changes with explicit exit-code capture (0 → early-exit success; 2 → fail; 1 → continue) → log `wc -l urls_new.txt` → branch: item steps only if urls_new.txt exists & non-empty (deletions-only month still reaches commit-back) → `urls_check_access.py --urls-file data/urls_new.txt` as warn-only (`continue-on-error`, CSV surfaced via artifact) → fetch collection.json from S3 → `item_create.py --incremental` → `item_validate.py --incremental --items-dir` (hard gate) → `s3_sync-ci.sh` → commit-back: `git add -A data/` (covers modifications, deletions, new access CSV), `git pull --rebase origin main`, push as bot → upload `logs/` artifact (always)
- [ ] `scripts/README.md` Automation section: schedule, manual dispatch, failure triage (invalid-item-blocks-batch → `urls_invalid_items.txt` + `item_reprocess.py`; oversized-batch fallback = manual local run; public-repo 60-day cron auto-disable), registration step
- [ ] CLAUDE.md: fix stale "Local STAC output" path note (S3 is the sole catalog copy)

## Phase 4: Catch-up run + verification (post-merge)

- [ ] PR via `/gh-pr-push` (body: Relates to #23, why urls_fetch.R is bypassed in CI), merge
- [ ] Seed catch-up: run `urls_reconcile.py` for real, commit trimmed `urls_list.txt` to main — next run re-detects the ~2,098 never-built URLs plus 5 months of growth
- [ ] `workflow_dispatch` from main; watch timing; verify cache commit lands and count math holds: S3 objects ≈ valid-item count + collection.json; residual URL-vs-item gap fully explained by known-invalid entries in `stac_geotiff_checks.csv`
- [ ] Register on geoserv (`stac_register-pypgstac.sh stac-dem-bc ...`); verify pgstac count + API query at images.a11s.one returns a new item
- [ ] Confirm cron live; close #23 via docs commit ("monthly automation live; Fixes #23")

## Validation

- [ ] Cold-path rehearsal passed (dry-run: zero deletions, correct order)
- [ ] `/code-check` clean on each commit
- [ ] Catch-up run: new items on S3 + caches committed by bot + API returns a new item + count math documented in run log
- [ ] PWF checkboxes match landed work; `/planning-archive` on completion
