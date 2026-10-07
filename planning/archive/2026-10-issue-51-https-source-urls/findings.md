# Findings — Store source URLs as `https://` (#51)

## Issue context

**If we do it:** one spelling of each URL across `data/`, and joins between the caches stop depending on every reader normalising first. **If we never do:** about 20 `fix_url` call sites stay load-bearing, and the first `ngr` release that fixes the root cause makes change detection report ~102k new files and ~102k deletions.

## Problem

Source URLs reach `data/` as `https:/host/...` because of NewGraphEnvironment/ngr#38 (draft 1). The code
repairs them each time it reads them (`stac_utils.fix_url`, about 20 call sites), and the
committed caches now hold **both** forms (measured 2026-10-06):

| file | `https:/` | `https://` |
|---|---|---|
| `urls_list.txt` | 102,416 | 0 |
| `urls_dsm.txt` | 95,889 | 0 |
| `urls_deleted.txt` | 43 | 0 |
| `stac_geotiff_checks.csv` | 102,520 | 2,245 |
| `urls_access_checks.csv` | 0 | 4,420 |
| `urls_invalid_items.txt` | 0 | 2,245 |

One fact is spelled two ways. Joins between these files only work while every code path
remembers to normalise first.

## Work

- [ ] Land the ngr fix and pin the new version in `DESCRIPTION`
- [ ] In the same commit, rewrite every cache above to `https://`. Assert the row counts
      are unchanged and that no URL remains in the single-slash form. Without this, the
      first run after the ngr bump reports ~102k new files and ~102k deletions
- [ ] Have `detect_changes.R` refuse a listing whose URLs are not `https://`, so the old
      form cannot come back
- [ ] Then decide about `fix_url`. It can stay as a harmless guard, or go once a test
      proves nothing in `data/` needs it. Item hrefs already published to S3 are
      unaffected: `item_create` wrote them through `fix_url`

Found in #49.

## Exploration 2026-10-06

- `stac_geotiff_checks.csv`: 104,765 rows, 102,460 distinct URLs after normalising.
  2,255 URLs repeat (2,245 albers tiles in both spellings + 10 raw duplicates); every
  repeat's other columns are identical. User approved dedupe at the plan gate.
- `detect_changes.R` cites "4,490 albers rows against 2,245 live URLs" as evidence for
  #28's prefix rename. They are the same 2,245 URLs spelled twice — a #51 artifact.
  The 43 `albers10k2m_new/` deletions remain real evidence.
- The 90 % plausibility guard in `detect_changes.R` cannot see the ngr-bump failure:
  fresh and cached counts are equal, only the spelling differs.
- ~~`url_to_item_id` slices at `len(PATH_S3)`, so a single-slash URL loses a character.~~
  Wrong (plan review A1, verified): the over-slice eats the `/` and `lstrip("/")` covers
  the other form, so both spellings give the same id. #49's refusal was
  `register_manifest.py`'s `startswith(PATH_S3)` check. Removing `fix_url` cannot change
  an item id.
- `fs::path()` also collapses an inner `//` and strips a trailing `/` from keys (plan
  review A2), so the scheme may not be the only difference between an old-ngr and a
  new-ngr walk. Validated by walking with both versions (Validation).
- DESCRIPTION pinned ngr at bare SHA `519c03b` (ancestor of v0.0.2). Only other org
  caller outside this repo: `stac_orthophoto_bc/stac_create_collection.qmd`.
- CI (`update.yml`) installs R before running pytest, so a pytest can drive Rscript.
- User approved merging + tagging ngr v0.0.3 within this run.

## Two-join walk (AC1), measured 2026-10-06

One live walk with the fixed ngr (575,438 keys, 151 s); the old output was rebuilt as
`fs::path(bucket, keys)` from the same raw keys, so there is no time gap between the
two. Log: `logs/20261006_205751_51_walk_compare.log` (gitignored; the numbers above are the record).

- Every new URL starts with `https://`.
- 204 keys differ beyond the scheme. All are directory markers (`.../082/082e/2017/dem/`),
  where `fs::path` stripped the trailing `/`. That is plan review A2, confirmed.
- **No derived artifact moves.** After repairing the scheme, the DEM set (102,416), the
  DSM set (95,889) and `dsm_groups` (157) are identical between the two joins. The
  `.tif` filters exclude the markers, and a `.../dsm/` marker maps to a group that
  already exists.
- The old-form listing against the committed cache gives 0 new and 0 deleted for both
  DEM and DSM, so the migration rewrites spelling only and carries no pending changes.

## End-to-end: the real `detect_changes.R` on the migrated caches (2026-10-06)

ngr v0.0.3 was installed into a scratch R library, and the run used a scratch copy of
`scripts/` + `data/`:

- 102,416 fresh, 102,416 cached, 0 new, 0 deleted; DSM listing unchanged; exit 0.
- `urls_list.txt`, `urls_dsm.txt`, `urls_deleted.txt` and `dsm_groups.txt` were
  rewritten byte-identical to the migrated versions.

So the first scheduled run after merge should report no changes.

## Cross-repo consumer (ngr code-check round 1, verified 2026-10-06)

`stac_orthophoto_bc` does not pin ngr, and its `stac_create_item.qmd` breaks on
`https://` URLs: lines 136 and 142 run `.replace("https:/", "https://")`, which is not
idempotent and gives `https:///...`, and line 148 slices the id at `len(path_s3)`, which
gives ids with a leading `-`. It is latent until that repo regenerates
`data/urls_list.txt` with ngr >= 0.0.3. Filed as stac_orthophoto_bc#48. This repo's
legacy `stac_create_item.qmd:231` has the same non-idempotent replace; it is folded into
Phase 4.

## Errors Encountered

| Error | Resolution |
|-------|------------|
| `cp` to `$TMPDIR/k.R` failed: TMPDIR unset, so the mutation check deleted ngr's empty-keys guard and never restored it | Caught by `git diff`. Restored the guard and re-ran the check with the session scratchpad path |
