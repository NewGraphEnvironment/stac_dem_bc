# Findings — Automate monthly incremental catalog updates via GitHub Actions (#23)

## Issue context

Issue #23 (filed 2026-07-18): catalog five months stale (inventory 2026-02-18, S3 build 2026-02-11, pgstac registration 2026-02-13 with 58,019 items). Phase 3 automation from #5 never re-tracked after PR #9's "Closes #5" auto-closed the umbrella. Adopt the water-temp-bc pattern (monthly GHA cron + OIDC, decision record NewGraphEnvironment/water-temp-bc#17, reference workflow `.github/workflows/snapshot.yml`). Companion infra: rtj#184 (`modules/gha_s3_role` consumer). Registration stays manual-on-geoserv for v1; incremental pypgstac upsert is a named follow-up. Out of scope: full rebuilds (rtj#49), source-file deletions, #16 closure.

## Verified pipeline contracts (2026-07-18 exploration)

- `scripts/detect_changes.R` — self-contained: fetches fresh listing (`ngr::ngr_s3_keys_get`), diffs against `data/urls_list.txt`, writes/deletes `urls_new.txt` + `urls_deleted.txt`, overwrites the cache, sink()-logs to `logs/`. Exit 0 = no changes, 1 = changes; **R errors also exit 1** (ambiguous — fix in Phase 1) and a **deletions-only month exits 1 with no urls_new.txt** (workflow must branch on file presence). `urls_fetch.R` is redundant in CI.
- `scripts/item_create.py --incremental` — reads `data/urls_new.txt`, needs only `$OUTPUT_DIR/collection.json` locally (10.9 MB from S3; item JSONs not required), appends links with duplicate prevention, saves collection. `get_output_dir()` (`scripts/stac_utils.py:39`) hardcodes `/Users/airvine/...` — used by item_create, collection_create, item_reprocess.
- `scripts/item_validate.py` — already parameterized (`--items-dir`, `--incremental`) and already exits non-zero when any item invalid (verified `return 0 if invalid == 0 else 1`). Caveat: the count spans history + new, so one bad item blocks the whole batch and re-fails monthly — v1 fail-loud by design; remediation via `urls_invalid_items.txt` + `item_reprocess.py` (document in README triage).
- `scripts/urls_check_access.py` — **hard-exits 1 on any inaccessible URL** (even from cache). `build_safe.sh` Step 3.5 treats it warn-only; the workflow must too. `data/urls_access_checks.csv` is currently untracked — commit-back uses `git add -A data/` and the CSV gets committed (GeoBC-report purpose from #13).
- `scripts/s3_sync.R` — laptop-only (`--delete --profile airvine`, hardcoded path). Never run in CI: `--delete` from a stateless runner would wipe the 58k items. **S3 is the only catalog copy** (local prod dir verified empty; CLAUDE.md's "Local STAC output" note is stale).
- `environment.yml` — pure pip under conda → `uv pip install` works today; #16 not a blocker. Missing `jsonschema` (via `pystac[validation]` — the validate gate depends on it) and `requests`.
- **~2,098 URLs in the cache have no catalog item** (60,126 URLs vs 58,028 validated items), including the 90 parenthesized files added post-build (commit 0d5ab5c, 2026-02-18 — after the Feb 11 S3 build). Being cached, detect_changes will never re-flag them → one-time reconciliation (`urls_reconcile.py`) trims the cache to item-backed URLs so the catch-up run picks them up naturally.
- State atomicity on a runner comes free for the linear path (caches persist only via end-of-job commit; failures self-heal) — but a truncated listing would poison the cache silently, hence the <90% plausibility guard.

## Infra (rtj + water-temp-bc)

- `modules/gha_s3_role` (rtj#147): role name derives to `role_gha_stac_dem_bc`; ListBucket + GetBucketLocation on bucket, Get/PutObject on objects, **no DeleteObject by default** — sufficient for no-delete sync and the backstop against the `--delete` wipe scenario. Account OIDC provider already provisioned. ARN pattern: `arn:aws:iam::414155577829:role/role_gha_stac_dem_bc`.
- Bucket `stac-dem-bc`: public + CORS, **no versioning** (water-temp-bc has it) → optional versioning rider in Phase 2 since collection.json is overwritten in place every run.
- STAC API = stac-fastapi-pgstac + TiTiler + Caddy on **geoserv** (`images.a11s.one`); DB `stac` holds stac-dem-bc. Registration: `rtj/scripts/geoserv/stac_register-pypgstac.sh` on-host — full delete-and-reload, 46 min for 58k items (S3 download dominates; DB load 10 s).
- rtj has an in-flight branch (`172-stac-floodplains-bc-bucket`) → Phase 2 branches off rtj main. No branch protection on stac_dem_bc main; `claude.yml` has no push trigger; GITHUB_TOKEN pushes don't retrigger workflows.
- Public repo → GitHub disables cron after 60 days without repo activity; no-change months produce no commits (document in triage).
- Side finding (out of scope, flagged to user): `rtj/scripts/geoserv/stac_register-pypgstac.sh:82` still has the parallel-append interleave pattern from the conventions — harmless for KB-scale DEM items, trips on large payloads.

## Plan-review disposition (adversarial Plan-agent pass, 2026-07-18)

Absorbed into phases: warn-only access check (B1), deletions-only branch + `git add -A data/` commit semantics (B2, G4), listing plausibility guard (G5), items-before-collection sync order (G6), orphaned-URL reconciliation (G7), rebase-before-push (G8), explicit exit-code capture (G9), cron auto-disable doc (G10), oversized-batch fallback doc (A12 — a naive "process first N" cap would orphan the remainder because the cache updates eagerly), import smoke check + Python ≥3.10 pin (A13, A14), ngr SHA pin in DESCRIPTION Remotes (S18), Phase 4 count math (A21). Dropped as verified-unnecessary: "add exit code to item_validate" (already correct). Catch-up sizing note: at ~6,450 items/hour, a 35k-scale surprise busts the 330-min timeout non-convergently — log `wc -l urls_new.txt` early; fallback is a manual local run.
