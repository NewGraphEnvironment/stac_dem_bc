# Phase 2 code-check, round 3

## Mechanism behind round 1

The fault was **a test that substitutes the code under test**: the fixture redefined the
function that carries the guard, so the guard that ran was a copy, and the shipped one
could be deleted with the suite staying green. The general form is "the fixture swaps
something wider than the network boundary". It belongs to the "guard that fails toward
pass" family, because a guard nobody exercises reads as a guard that holds.

## Where that mechanism could still be hiding

Each candidate was checked against the code, and the guards were mutated in a scratch
copy (`scratchpad/r3/`). The working tree was not touched.

| mutation (scratch copy) | result |
|---|---|
| none | 5 passed |
| delete the walk guard (`urls_listing.R:66`) | `test_single_slash_listing_is_refused` fails |
| replace Step 0 with a no-op (`detect_changes.R:47-49`) | 3 fail |
| Step 0 checks `urls_list.txt` only | dsm and deleted tests fail |
| `urls_scheme_assert` never raises | 4 fail |

Every guard in the diff turns red when it is removed. The fixture now replaces only
`keys_get` (plus `keys_min = 0`), and the default `ngr::ngr_s3_keys_get` is lazy, so the
test never touches ngr.

**Writers of the three guarded files:**
- `detect_changes.R` is guarded twice, as tested above.
- `scripts/urls_fetch.R:38-42` writes `urls_list.txt`, `urls_dsm.txt` and `dsm_groups.txt`
  from `urls_listing_fetch()`. That is the same shipped function, with the default
  `keys_get` and the walk guard at line 66, so its output is always `https://`. It has no
  test of its own, but the guard lives in the shared function rather than in the
  caller. It does not write `urls_deleted.txt`. Its `--test` mode reads the cache and
  writes nothing.
- `scripts/urls_reconcile.py:60` rewrites `urls_list.txt` by filtering the existing lines,
  so the spelling is preserved. It matches through `url_to_item_id`, which gives the same
  id for both spellings.
- `stac_create_collection.qmd:46-56` has no guard. See finding 1.
- `scripts/benchmark_fetch.R` calls ngr directly but writes only a log.
- Nothing else that is tracked writes these files. I checked every tracked file outside
  `data/`, `planning/` and `logs/` that names them.

**Readers of `data/urls_new.txt` in `update.yml`:**
- `urls_check_access.py:51` and `item_create.py --incremental` (lines 105, 219-279) send
  every URL through `fix_url`. It is idempotent on `https://` (`stac_utils.py:418-421`),
  so a migrated `urls_new.txt` reads the same as today.
- The "Count new items" step only runs `wc -l`, so spelling does not matter.
- Downstream, `dsm_pair.py` diffs on bucket-relative `dem_key` (`key_relative(fix_url(key))`,
  and `dem_dsm_pairs.csv` holds relative keys), so the scheme change cannot raise a false
  "pairing changed" for 102k items.
- The only non-idempotent repair is `stac_create_item.qmd:231`, which is already in
  Phase 4 and is not on the workflow path.

## Findings

- **[fragile]** `stac_create_collection.qmd:46-56`: the production chunk calls
  `ngr::ngr_s3_keys_get()` directly and writes `data/urls_list.txt` with no scheme guard.
  `DESCRIPTION`'s pin governs CI only. A local render against an older installed ngr
  (519c03b) writes `https:/` again. This fails loudly rather than toward pass: the next
  `detect_changes.R` refuses at Step 0 with exit 2, and Phase 3's planned data-invariant
  pytest would fail in CI. But the refusal arrives a month later, in the workflow, not
  at the write. The cheapest fix is to route this chunk through `urls_listing_fetch()`
  like `urls_fetch.R`. That would also bring in the KEYS_MIN floor, which this path has
  never had. The alternative is to call `urls_scheme_assert(keys_clean, "bucket walk")`
  before the write.
- **[fragile, planning]** `planning/active/task_plan.md` Phase 1 still has all five
  boxes unchecked. The staged `progress.md` in the same commit says ngr PR #39 is merged
  and released as v0.0.3, and both are verified: PR #39 is MERGED and tag `v0.0.3` exists
  at `25d3911`. This understates rather than overstates, but it breaks the "PWF checkboxes
  match landed work" acceptance item and the Reboot Test, because the file says Phase 1
  has not started.

## Planning-file claims checked against the code (none overstated)

- The `findings.md` A1 correction holds. `url_to_item_id` (`stac_utils.py:435`) gives the
  same id for both spellings, and `register_manifest.py:41` is the `startswith(PATH_S3)`
  refusal.
- The two-join walk numbers (575,438 keys and 151 s; 204 differing keys that are
  directory markers; DEM 102,416, DSM 95,889 and 157 groups identical; 0 new and
  0 deleted) match `logs/20261006_205751_51_walk_compare.log`. The log is gitignored by
  `logs/*.log`, as the file says.
- stac_orthophoto_bc#48 exists, is OPEN, and its title fits. Lines 136, 142 and 148 of
  its `stac_create_item.qmd` are as described.
- The Phase 2 boxes are true of the code. The restore-the-bug claim is borne out by the
  mutation table above.

## Notes (not findings)

- `urls_scheme_assert` tests only the prefix, so `https:///` passes. That is exactly what
  the non-idempotent replace at `stac_create_item.qmd:231` produces. Nothing currently
  writes that form into the three guarded files, and the qmd builds a lookup dict, not a
  cache.
- On its own, the Phase 2 commit makes `main`'s monthly run exit 2: the committed caches
  are `https:/` and the pin is still 519c03b. That is loud and intended, and it holds only
  while Phase 3 ships in the same PR as planned. Do not merge Phase 2 separately.
