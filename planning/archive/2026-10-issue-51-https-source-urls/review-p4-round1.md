# Code-check review — #51 Phase 4, round 1

**Verdict: Clean.** No bug, security issue or data-loss path found in the staged diff.

## Verification done (in a copy of the staged index, not the working tree)

- `pytest tests -q` on the staged index: 209 passed.
- `dsm_pair.py` run end to end on the real `data/` listings: the output
  `dem_dsm_pairs.csv` and `dsm_pairing_report.md` are byte-identical (`cmp`) to the
  committed ones. A second run against the first run's csv queues 0 rebuilds, so
  `urls_pairing_changed.txt` stays empty.
- Join probe on the real caches: `stac_geotiff_checks.csv` has 102,460 rows,
  102,460 unique urls and no non-string urls. Every line of `urls_list.txt` and
  `urls_invalid_items.txt` is in the cache under its raw spelling. Every paired
  `dem_key`, prefixed with `PATH_S3`, is in `urls_list.txt`. No line in any listing
  has leading or trailing whitespace or is blank. `urls_access_checks.csv` has 0
  one-slash urls.
- No other local repo imports this repo's `scripts/`, so deleting `fix_url` breaks
  no external caller.

## Removing `fix_url` changes nothing for an `https://` input

`fix_url` returned its input unchanged unless the input started with `https:/` and
not with `https://`. Every site where it was removed (the `item_create` cache sets,
dict keys and `.isin`, the `process_item` hrefs in both scripts, `geotiff_extract_metadata`,
`item_create_from_cache`, the `item_reprocess` lookup, the `dsm_verify` lookup) is
therefore the identity for `https://`, for `https:///` and for bare keys. The only
other difference is for non-string values: a NaN url used to raise `AttributeError`
in `fix_url` and is now kept as a key. That is harmless, and the cache holds none.

## Input boundary: every place a URL enters, and what a one-slash URL there now does

(a) raises · (b) cannot happen, or does no harm · (c) silently gives a wrong result

| entry point | source | outcome |
|---|---|---|
| `item_create.main` | `--urls-file` / `urls_new` / `urls_invalid_items` / `urls_list` | (a) at read, before anything is written |
| `item_reprocess.main` | `urls_invalid_items.txt` | (a) at read, before anything is written |
| `dsm_pair.keys_load` | `urls_list`, `urls_dsm`, `dsm_groups` (and the CLI overrides) | (a). `dsm_groups` holds bare paths, so the check is a no-op there |
| `dsm_pair.key_relative`, `stac_utils.tile_key_parse` | keys from `keys_load` | (a). Already checked once at load |
| `register_manifest.item_ids_from_urls` | `--urls-file` | (a). It builds the full list before printing, so a failure emits no partial id set |
| `urls_check_access.main` | `--urls-file` | (a) before the cache write |
| `item_create.load_validation_cache` | `stac_geotiff_checks.csv` | (b). The only Python writer is `geotiff_extract_metadata`, called on inputs `main()` has already checked. `test_no_tracked_data_file_holds_a_one_slash_url` runs at the start of every CI job |
| `item_reprocess` cache read | `stac_geotiff_checks.csv` | (b), same as the row above |
| `dsm_verify` | `stac_geotiff_checks.csv`, pairs csv | (b). The pairs csv holds only relative keys written by `key_relative` (which raises); DSM urls are rebuilt from `PATH_S3` |
| `item_create.dsm_lookup_load` | `dem_dsm_pairs.csv` | (b). Relative keys written by `key_relative`; urls rebuilt from `PATH_S3` |
| `item_backfill` | pairs csv; hrefs in published item bodies | (b). Relative keys; never called `fix_url`; its behaviour is unchanged |
| `item_migrate` / `item_rewrite` | collection.json and item bodies fetched from S3; item ids | (b). No source urls are parsed |
| `urls_reconcile` | `urls_list.txt` | (b). `url_to_item_id` slices `len(PATH_S3)` and then `lstrip("/")`, so it yields the same id for either form. Lines are written back unchanged, and `detect_changes.R` would then refuse them |
| `item_extract_invalid` | item ids → `PATH_S3` + path | (b). It only ever writes `https://` |
| `collection_create`, `stac_create_collection.qmd` (Python chunk) | `urls_list.txt` | (b). Used only for date extraction, which does not depend on the scheme |
| `detect_changes.R` / `urls_listing.R` / `urls_fetch.R` / `stac_create_collection.qmd` (R chunk) | ngr bucket walk; the cache files | (a) `urls_scheme_assert`, exit 2 |
| `stac_create_item.qmd` | url files plus the cache | (c) in principle: a one-slash line is now skipped as "unreadable" instead of being repaired. Not a finding: the notebook is banner-marked superseded / do-not-run, still targets the pre-#34 collection id and asset key, and its inputs are guarded by the data test. Dropping the non-idempotent `.replace` fixes a real `https:///` defect |

### `update.yml` steps

`urls_check_access --urls-file data/urls_new.txt`, `dsm_pair.py` (default listings),
`item_create.py --incremental` (`urls_new.txt`) and
`item_create.py --urls-file data/urls_pairing_changed.txt` all read files with an
`https://` provenance. `urls_new` comes from the listing that `urls_scheme_assert`
checks. `urls_pairing_changed` is `f"{PATH_S3}/{dem_key}"`. The listings are checked
by `detect_changes.R` step 0 and again by `keys_load`. A `ValueError` raised in
`dsm_pair` exits 1 rather than 2, and either code fails the job, so the failure is
still loud.

## Note (not a finding)

The CLAUDE.md wording "the Python loaders raise on a one-slash line" holds for the
url-list readers. The three readers of `stac_geotiff_checks.csv`
(`load_validation_cache`, `item_reprocess`, `dsm_verify`) do not raise. A one-slash
row there would be re-extracted and appended under a second spelling, or silently
dropped from the `dsm_verify` comparable set. They are protected by the tracked-data
test, not by a raise. Nothing currently writes such a row, so this is accurate
enough. Worth one clause if the paragraph is edited again.
