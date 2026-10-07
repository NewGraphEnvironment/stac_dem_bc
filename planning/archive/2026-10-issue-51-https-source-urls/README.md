## Outcome

Source URLs in `data/` are now stored as `https://`. They were `https:/` because
`ngr::ngr_s3_keys_get()` joined with `fs::path()`, which collapses the scheme's `//`.
That was fixed upstream in ngr PR #39 and released as **ngr v0.0.3**, and this repo
now pins that version. The pin and a byte-preserving rewrite of the four caches
landed in one commit, because either alone makes change detection read every URL as
new and deleted.

Three guards keep the old form out:
- `detect_changes.R` refuses a listing or cache not spelled `https://`, before the
  bucket walk and before any write, and exits 2.
- `stac_utils.fix_url`, which silently repaired the old form at about 20 sites, is
  replaced by `url_scheme_check`. It raises wherever URL lists are read and in key
  parsing.
- A test fails if any tracked file under `data/` holds a one-slash URL.

The plan review found a cross-repo hazard: stac_orthophoto_bc does not pin ngr, and
its item qmd breaks on `https://` input. That is filed as stac_orthophoto_bc#48.

The review also showed one of my own findings was wrong. I had claimed
`url_to_item_id` drops a character on the one-slash form. It does not; both
spellings give the same id. The #49 refusal came from a `startswith` check.

Code-check round 1 on Phase 2 found that the walk-guard test was testing a copy of
the guard. The fixture had replaced the function wholesale. The fix injects only
the key lister.

## Measurement

- **Before:** `stac_geotiff_checks.csv` had 104,765 rows but only 102,460 distinct
  URLs. 2,245 albers tiles were present in both spellings, plus 10 exact repeats, and
  every repeat was identical to its other row. The file was deduplicated to 102,460
  rows. The "4,490 albers rows" that `detect_changes.R` cited as #28 evidence was this
  spelling duplication, not a prefix rename.
- **Two-join walk:** one live walk of 575,438 keys in 151 s, with the old
  `fs::path` output rebuilt from the same raw keys. 204 keys differ beyond the
  scheme, all directory markers whose trailing `/` `fs::path` stripped. After
  repairing the scheme, the DEM (102,416), DSM (95,889) and `dsm_groups` (157)
  outputs are identical, and the listing matches the committed caches with 0 new
  and 0 deleted.
- **End to end:** the real `detect_changes.R` with ngr 0.0.3 on the migrated caches
  reports 0 new and 0 deleted, exits 0, and rewrites every cache byte-identically.
  This is the run the issue predicted would report ~102k new and ~102k deleted
  without the coupled migration.
- **No output change from retiring `fix_url`:**
  - 19 sample items (albers, URLs with spaces, paired DSM) built offline are
    byte-identical before and after.
  - `dsm_pair.py` on the real listings reproduces the committed pairs csv and
    report byte-for-byte.

## Evidence

`logs/20261006_*_51_walk_compare.log` and `logs/20261006_*_change_detection.log`
(both gitignored; the numbers above are the record). Reviews are in `review-*.md` in
this directory.

Closed by: PR (to be opened from branch `51-store-source-urls-as-https-migrate-the-c`)
