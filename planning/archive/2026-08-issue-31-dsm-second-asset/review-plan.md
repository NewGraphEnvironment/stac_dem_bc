# Plan-agent review — #31 / PR #32

Delivered by the `plan-review-31` agent as a message; it ran read-only and could
not write this file itself. Recorded here verbatim in substance so the finding
trail is in git rather than only in a transcript.

## Live blockers (all three were real; all three fixed in commit following this)

**R1. `data/stac_item_validation.csv` wiped, 98,048 rows → 6.** Commit `4afc4f9`.
A 6-item end-to-end smoke test run as `item_validate.py --items-dir <scratch>`
replaced the ledger, because a non-incremental run rewrites the file wholesale.
Two consequences beyond the lost audit trail: `scripts/urls_reconcile.py` derives
item-backed URLs from this file, so `--apply` — which `scripts/README.md` tells
the operator to run — would have truncated `urls_list.txt` to those 6 URLs; and
the surviving rows carried scratchpad paths.
*Fixed:* restored from `HEAD~3`; `item_validate.py` now refuses a full run that
would shrink an existing ledger unless `--allow-shrink` is passed.
*Note on the path leak:* the restored ledger already contained 40,021 rows with
`/private/tmp/.../scratchpad/catchup_out/` paths from the July 2026 catch-up. That
predates this work and is a separate pre-existing issue, not a regression here.

**R2. `urls_list.txt` advanced to 102,416 without the 4,420 being built.**
`detect_changes.R` rewrites the cache as a side effect of detection, so the next
run's setdiff would be empty, `urls_new.txt` would be deleted, and the
`new_urls == 'true'` gate would never fire — the tiles become invisible to change
detection permanently. This is verbatim the failure `urls_reconcile.py`'s docstring
describes, and R1 had broken its repair path, so the two locked each other in.
*Fixed:* reverted `urls_list.txt` to the 97,996 baseline. `urls_new.txt` retains
the 4,420, and the next detection run re-derives them cleanly.

**R3. `data/urls_deleted.txt` deleted from the repo.** Commit `d780f0b`.
`detect_changes.R` removed it because the current run found no deletions — but it
is an append-only audit trail, not current state, and is the standing evidence for
#28. The 43 lost entries were all under `albers10k2m_new/`, and
`stac_geotiff_checks.csv` still holds 4,490 albers rows against 2,245 live URLs:
a prefix rename, which supports #28's hypothesis over data loss.
*Fixed:* restored, and the script now appends rather than rewriting, and never
removes the file.

## Lower severity (all fixed)

- **`item_reprocess.py` had no DSM pairing.** It is the documented remediation
  path for validation failures, so a reprocessed item silently lost its `dsm`
  asset — and the pairing-changed rebuild would not restore it, because the
  pairing had not changed.
- **DSM-side conservation check missing.** The DEM side raises if a key is
  dropped; the DSM side had nothing. Added, and it double-counted on the first
  attempt because unparseable keys were also in `dsm_unmatched` — now excluded.
- **`FOOTPRINT_SAMPLE_MIN` guarded the wrong population** — it tested ~90k
  comparable tiles rather than the drawn sample, so it could never fire.
- **Four factual errors in `task_plan.md`**: the superseded ~2,175 figure;
  `082/082f/2017` named as laz-only when it holds 28 raster DSMs; the fixture
  named against that wrong group; and a match key omitting `utm`, which would put
  91,008 of 95,751 DEMs into a shared key (worst case 84 files on one key).

## Open, not actioned here

- **`data/dem_dsm_pairs.csv` is 18 MB, re-committed monthly** by `git add -A data/`;
  `data/` is now ~57 MB. It is a pure function of two already-committed files.
  Worth reconsidering whether to track it.

## Backfill design — reviewer's assessment of the S3-JSON-rewrite route

Judged sound, and **strictly better** than a rebuild, for a reason worth
recording: the 60,324 cache rows lacking spatial metadata are exactly the items
built by the `rio_stac` fallback, which emits a different properties set from
`item_create_from_cache`. A rebuild would replace 60k items produced by one code
path with output from another — a change nobody reviewed, invisible in a one-item
spot check. Cost is on the repo's own record: `scripts/README.md` measures a full
pgstac reload at ~46 min dominated by the same ~98k item-JSON GETs.

Five constraints for whoever implements it:

1. **Do not round-trip through `pystac.Item`.** `json.load` → set
   `data["assets"]["dsm"]` → `json.dump`. pystac normalises key order,
   `stac_version`, links and self-href, silently rewriting ~91k items.
2. **`collection.json` needs no rewrite.** Ids and hrefs are unchanged; all
   98,040 links stay valid. Only the Phase 7 metadata patch touches it.
3. **Build the 4,420 new tiles FIRST** via the incremental path — they get `dsm`
   at creation, and the backfill set is then stable at ~91.5k.
4. **Resumability**: done-manifest, skip on restart. Without it an interrupted run
   leaves some items with the asset and some without, and no record of which.
5. **Acceptance**: diff fetched-vs-rewritten on a sample (`catalogue_qa.py`, or
   `deepdiff` which is already a dependency), asserting the only difference is the
   added `assets.dsm`.

## On the Phase 5 threshold

The reviewer independently reproduced the sample offline (seed 31) and confirmed
the stratification is doing real work rather than passing on a base rate: the
paired population is 721 COG-True against 90,747 COG-False (0.8% True), while the
drawn sample is 246 True / 254 False (49%). Stratifying by (convention, year)
oversamples the informative stratum ~60×, because `is_cog` is essentially
year-determined — 2012–18 and 2021 are ~100% COG, 2023–25 are 0 of 90,628, and
2019/2020/2022 are genuinely mixed at 37.6 / 58.4 / 53.5%. The 0.998 figure rests
on 246 of the 721 COG-True paired tiles being measured directly.

## Remaining suggestion, not yet implemented

The tests derive mapsheet-year groups with their own `groups_of()` helper rather
than the production R derivation in `urls_listing.R`. Worth one test that reads
`data/dsm_groups.txt` and asserts each entry equals `tile_key_parse(...)["group"]`
of some DEM key, so the two derivations cannot drift apart.
