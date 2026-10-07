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
- `url_to_item_id` slices at `len(PATH_S3)`, so a single-slash URL loses a character —
  the mechanism behind #49's `ids-from-urls` refusing every line.
- DESCRIPTION pinned ngr at bare SHA `519c03b` (ancestor of v0.0.2). Only other org
  caller outside this repo: `stac_orthophoto_bc/stac_create_collection.qmd`.
- CI (`update.yml`) installs R before running pytest, so a pytest can drive Rscript.
- User approved merging + tagging ngr v0.0.3 within this run.

## Errors Encountered

| Error | Resolution |
|-------|------------|
