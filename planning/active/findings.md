# Findings — update.yml: OIDC credentials expire an hour into the job, so a long run's S3 sync fails (#60)

## Issue context

## Problem

`update.yml` assumes the OIDC role once, right after checkout (`aws-actions/configure-aws-credentials@v4`, no `role-duration-seconds`), so the session lasts one hour from that step. Any S3 write more than an hour into the job fails with `ExpiredToken`.

Measured on the `footprint` dispatch, run 38339265803 (2026-10-13):

| step | UTC |
|---|---|
| credentials assumed | 03:01:28 |
| rewrite (102,460 written, 0 errors) | 03:03 – 03:16 |
| validation gate | 03:16 – 03:49 |
| S3 sync starts | 03:50:05 |
| first `ExpiredToken` | 04:01:29 |

91,772 items reached S3, 239 uploads failed, the rest were never attempted, and `collection.json` (uploaded last) was not written. The commit-back step correctly discarded `data/footprint_done.txt` because the sync did not succeed, so no unpublished work was recorded as done.

The same limit applies to a heavy monthly run: CLAUDE.md puts a typical ~8k-file month at 75–90 min, which puts its sync past the hour.

## Fix

Fetch the credential just before its first late use: add a second `configure-aws-credentials` step immediately before `Sync catalog to S3`. Raising `role-duration-seconds` is the alternative, but it also needs the role's maximum session duration raised in the infrastructure repo.

## Recovery for run 38339265803

Re-dispatch `footprint: true`. Items already on S3 in the new shape come back `unchanged` from the rewrite edit, so only the ~10.7k missing items are staged, validated and synced, which keeps the sync well inside the hour.


## Exploration (2026-10-13)

- Only `Sync catalog to S3` (`scripts/s3_sync-ci.sh`, `aws s3 sync`/`cp`) uses the AWS credential. Other S3 reads are unauthenticated: `curl https://stac-dem-bc.s3.amazonaws.com/collection.json`; `ngr::ngr_s3_keys_get()` is a plain `httr::GET`; `PATH_S3_STAC` is the https URL.
- `configure-aws-credentials@v4` uses web identity when `role-to-assume` is set, the id-token is available and no key inputs are given, so a second call is a fresh OIDC assume that overwrites the exported `AWS_*` env.
- Sync throughput on run 38339265803: 91,772 items in ~11 min, so one sync fits well inside a fresh hour.
- Out of scope, filed separately: `Commit refreshed caches` runs under `always()` and commits `data/urls_list.txt` even when the sync failed, so a monthly run whose sync fails strands its new URLs (same mechanism as #57).

## Errors Encountered

| Error | Resolution |
|-------|------------|
