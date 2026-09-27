# Code review, round 1 (#42)

Reviewer: general-purpose subagent, 2026-09-27. Diff: staged changes to
`scripts/catalogue_register.sh`, `scripts/register_manifest.py` and
`tests/test_catalogue_register.py`. `tests/test_catalogue_register.py` passes
(15 tests, 21.9 s).

## Findings

- **[fragile] scripts/catalogue_register.sh:107**: the "this repo's bucket" half of
  the ownership test compares URL strings (`${BUCKET_URL%/}` = `${OWN_BUCKET_URL%/}`),
  so any other spelling of the same bucket counts as foreign. That reopens the
  rename-window hole the bucket clause was added to close (plan review, Gap 1).
  I probed it live (HEAD requests only). Each of these returns 200 for this
  bucket's `collection.json`, and none of them equals `PATH_S3_STAC`:
  - `https://stac-dem-bc.s3.us-west-2.amazonaws.com` (the regional form, which
    `scripts/README.md:246` uses for the airphoto bucket, so an operator is likely
    to copy it)
  - `http://stac-dem-bc.s3.amazonaws.com`
  - `https://s3.us-west-2.amazonaws.com/stac-dem-bc` (path-style)
  - `https://STAC-DEM-BC.s3.amazonaws.com` (the hostname is case-insensitive)

  I ran it in a scratch copy with a curl stub that refuses network calls, and
  `STAC_COLLECTION=stac-dem-bc`:
  - with `STAC_BUCKET_URL=https://stac-dem-bc.s3.amazonaws.com` it prints
    `asset audit: require=dem forbid=image`
  - with the regional or `http://` form it prints `asset audit: none (not this
    repo's catalogue ...)`

  In the next rename window the published file still carries the old id, so
  `STAC_COLLECTION=<old id>` plus an alias URL passes the id reconciliation.
  The old-shape items would then load with no asset check, and the run would
  also accept `STAC_REQUIRE_ASSET` / `STAC_FORBID_ASSET` for this repo's
  catalogue. Before #42 that combination was refused, because the dem/image
  check was unconditional.

  The test at `tests/test_catalogue_register.py:272` only parametrizes the
  literal URL and the literal URL plus a trailing slash, so it cannot reach this
  case.

  Possible fixes:
  - Normalise to the bucket name before comparing: parse virtual-hosted and
    path-style forms, lowercase them, and ignore the scheme and region.
  - Or decide ownership from something that cannot be aliased, such as the
    bucket name.
  - Or decide it after the fetch as well. Every published item href starts
    with `PATH_S3_STAC` (see CLAUDE.md), so a collection.json whose item links
    point into this bucket is this repo's, however the operator spelled the
    URL.

- **[fragile, low] scripts/catalogue_register.sh:77**: `OWN_BUCKET_URL` is now read
  at startup, in every mode and for every collection, by importing `stac_utils`.
  `stac_utils` imports rasterio, rio_cogeo, pystac, shapely and requests
  (`stac_utils.py:17-22`), and the import runs with `2>/dev/null`.
  - Before this change, `--verify` never imported `stac_utils`.
  - Now `--verify` fails at startup whenever `PY` falls back to a `python3`
    without that stack. The message, "could not read PATH_S3_STAC from
    scripts/stac_utils.py", hides the real `ModuleNotFoundError`.
  - The failure is loud, not silent, and it is close to the accepted "same
    .venv" tradeoff. But that tradeoff was stated for `item_migrate` on this
    repo's collection, and this applies to foreign collections and every mode.
  - A fix would be to drop `2>/dev/null` on this lookup so the import error
    shows.

## Checked and clean

- Bash 3.2 constructs: `${!var:-}`, `arr+=()` and the
  `${AUDIT_ASSET_ARGS[@]+"${AUDIT_ASSET_ARGS[@]}"}` expansion of an empty array
  under `set -u`.
- Moving the audit ahead of `collection_register.sh` introduces no dependency:
  both read `$WORK/collection.json` and `$FETCH_DIR` unchanged.
- Both constants are now required non-empty in this repo's branch (the old
  code checked only `AUDIT_DEM`).
- In `register_manifest.py`, `rules` is assigned before both of the lines that
  print it. The forbid list is parsed once and printed as it was applied.
- No consumer anywhere parses the old exact `OK:` line: the workflow and the
  tests only match its prefix.
- Test harness: the env is scrubbed before the fixture values are set; the curl
  stub refuses http(s) URLs; `PYTHON` is pinned. The rename-window test goes red
  if the bucket clause is removed, because neither asserted string appears on
  the foreign path.

---
## Triage (parent session)

1. Bucket aliases — **real, inside the plan-review Gap 1 fix.** Fixed: `register_manifest.s3_bucket_name()` + `same-bucket` CLI (answers on stdout, so an exception can't read as "different"); shell refuses anything but same/different. Tests: 6 aliases through the e2e rename-window test, 19 unit cases. Mutation (string compare restored) → the 4 non-trivial aliases red.
2. stac_utils startup import — **premise wrong**: `collection_patch.py:45` already imports stac_utils, so every mode imported it before #42. Kept the cheap part: dropped `2>/dev/null` on the new PATH_S3_STAC lookup.
