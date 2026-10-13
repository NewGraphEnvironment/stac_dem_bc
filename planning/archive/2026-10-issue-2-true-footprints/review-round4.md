# Review round 4 — branch 2-tif-footprints-are-actually-bounding-box (HEAD d199881)

Scope:
- Re-walked round 3's 22 decision points against HEAD.
- Traced every new decision the d199881 fixes introduced, and read the d199881 diff for
  anything else that is broken.
- Probes ran in `/tmp/cc_round4_copy` (an rsync of the repo without `data/footprints.csv`,
  `.git` or `.venv`) and in the scratchpad. The running extraction was only read: one `cp` of
  `footprints.csv` to the scratchpad, for `--audit`.
- In the copy, 88 footprint, item-field, workflow and apply tests pass.

## Enumeration re-walk

| # | Decision / artifact | Equal at HEAD? |
|---|---|---|
| 1 | `footprints.csv` footprint row | Yes for rows written by HEAD code. **Conditional** for the cache that will be committed: it is written by the frozen pre-bee74e5 copy, which has no NoGeoref/OutsideBC guard. Only `--audit` covers that, and nothing gates on `--audit` (Finding 2). |
| 2 | Refusal rows | Yes. F6 moved the no-georef test before decimation. Probe: a no-georef raster read at overview level reports an identity transform, so the overview path is refused correctly too. |
| 3 | Transient → not cached | Unchanged (assumed, measured unreached). Content-Length mismatch now joins this class: it raises RuntimeError and is retried. |
| 4 | checksum/size | **Yes now.** `_download(..., length)` refuses `size != Content-Length`. On the objectstore, HEAD and GET agree: 8,357,585 bytes, measured. urllib sends no Accept-Encoding, so the body is never gzip. With no length the check is skipped, as before. |
| 5 | Append to rebuild list | Yes for CI. For local callers it now **over-lists**: every URL a local run reads is listed, the whole population on a cold cache (Finding 1). F3's order (entry before row) is safe, traced below. |
| 6 | `--prune-changed` | **Yes now.** The prune checks `item_fields_apply(staged, raw url, footprints) == []` after a successful sync. Probe: idempotent on `item_create` output (see below). |
| 7 | `pending` | Yes |
| 8 | Rebuild input `urls_pairing_changed.txt` | **Yes now.** It is included under the same condition as dsm_pair's `if:` (`changes == 'true' \|\| inputs.backfill`), and dsm_pair.py:477-478 rewrites the file unconditionally whenever it runs. On a schedule run `inputs.backfill` renders empty, so it is not "true". |
| 9 | `--new-urls` | Yes |
| 10 | `footprint_done.txt` | Yes |
| 11 | footprint_apply staged check | Yes |
| 12 | Coverage check | Yes |
| 13 | Completeness | Yes |
| 14 | `item_fields.py audit` | Yes for what it checks. The F4 hrefs are now right by construction. |
| 15 | Validation gate | **Yes now**, with one caveat. The full pass covers every staged body. But `item_validate.py:79` calls `pystac.Item.from_dict(d)` with `migrate=True`, which defaults on in pystac 1.15.2. It therefore validates a projection **v2.0.0** migration (`proj:epsg` becomes `proj:code`, measured on a published body), not the v1.1.0 body that gets published. This is pre-existing and no failing case was found, so it is not flagged. |
| 16-18 | — | Accepted (#56/#57/out of scope). F5 widens row 18's trigger: an invalid **rebuilt** body now fails the month, and the `always()` commit then records that month's new URLs in `urls_list.txt` without publishing them. |
| 19 | Round trips | Yes. The href is now set before `pystac_item_fields_apply`, and `from_dict(migrate=False)` is unchanged. |
| 20 | Asset href | **Yes now** in all three builders, dem and dsm. |
| 21 | Decimated transform | Yes |
| 22 | NoGeoref before decimation | **Yes now.** The check is on `src.transform`, before scaling. |

## New decision points

- **F2: the prune loads the cache and parses JSON in the commit step.**
  - **Risk:** an exception there aborts the step under `bash -e`. Nothing is committed after a
    successful sync, and the next run would fail the same way.
  - **Reachability, traced:**
    - Bodies staged by `item_create` have already passed this same function on this same row.
    - Bodies from item_backfill, item_migrate and footprint_apply pass `stacs audit`, which is
      a precondition of the sync. So `assets.dem` exists.
    - `bcgs_cell` is anchored at the start of the name, so `%20(2)` cannot defeat it.
    - A malformed cache row would already have failed the extract step's `cache_finalize`.
  - Not flagged.
- **F2: is the predicate idempotent on what `item_create` actually writes?**
  - Probe: process_item, then save, then `item_fields_apply(saved, raw_url, {url: row})`.
  - It returns `[]` for the cache and rio_stac branches, partial and full-cell (`""` WKT)
    footprints, and space-named URLs.
  - The rio_stac branch with a space-named *local* file fails at item_create.py:148. That line is
    pre-existing, and `%20` is invalid only for a local path; HTTP works.
- **F2: refusal rows and entries with no row.**
  - `item_footprint_apply` returns `[]`, so such URLs are pruned once rebuilt, which is correct.
  - A URL that `item_create` never stages (#56, `is_geotiff=False`) stays listed and keeps
    `pending=true` on every run. This is by design.
- **F3: entry-before-row.**
  - An entry with no row (a kill between the two writes) is rebuilt at its extent. The prune
    passes it, because no row means no change. The next run writes the row and lists it again.
    So it fails safe.
  - `args.cache == CACHE` is a string compare: `--cache ./data/footprints.csv` bypasses the
    default. Minor, not flagged.
- **F3's default reaches build_safe.sh and the README Quick Start.** This is Finding 1.
- **F4, every reader of `assets.dem.href` checked:**
  - **Lookups:**
    - The footprints, DSM pairing and validation lookups are all keyed by the raw `href_item`
      (item_create.py:109/175/189). So are `has_footprint` and footprint_extract.
    - `lidarbc_delivery` matches `/gdwuts/NNN/NNNx/YYYY/`, which is unaffected.
    - The bcgs name comes from the encoded href. That is harmless: the regex is anchored at
      the start, and a space only ever precedes `(2)`.
  - **Rewriters:**
    - footprint_apply's `dem_url()` decodes `%20` and gets the raw key back.
    - item_backfill's `encode_url_for_gdal` is idempotent, so a rebuilt body triggers no
      `href:` edit.
    - item_rewrite and collection_patch read link hrefs only.
  - **Characters needing encoding:** spaces are the only such character in `urls_list.txt`
    (90 lines; 0 lines carry any other character outside `[A-Za-z0-9/:._ ()-]`).
- **F5 runs validation twice.**
  - The first pass writes to a new `$RUNNER_TEMP` file, so it cannot meet the ledger-shrink
    refusal.
  - The second pass validates only new ids, and still exits 1 on any historic invalid ledger
    row. That is pre-existing.
  - The cost is a few minutes on a normal month.
- **Content-Length:** covered under row 4.
- **The cache snapshot:** the in-progress cache (2,611 rows, copied read-only) audits clean.

## Findings

- **[fragile] scripts/footprint_extract.py:339-340 with .github/workflows/update.yml:287-304 and
  scripts/README.md:33 / scripts/build_safe.sh:159. INSIDE the round-3 fix F3.**
  - **What happens:** any local run with the default cache now lists **every URL it reads**,
    because no local caller passes `--new-urls`.
  - **Probe:** a plain 2-URL run in the copy listed both:
    `2 already-published URLs gained a footprint this run; 2 listed for rebuild in data/urls_footprint_changed.txt`.
    For the documented first-time run (README:33, "~20 h the first time") the list is all
    ~102k URLs. It is the same for build_safe.sh on a cold cache, and for any resume of the
    full extraction with HEAD code instead of the frozen copy.
  - **How it gets committed:** the file is untracked and not gitignored, and it sits beside the
    `footprints.csv` that must be committed before merge. A `git add data/` takes it along.
  - **What then breaks (wedge):**
    1. CI rebuilds the whole list through `item_create --urls-file`. 58,030 of 102,460
       `stac_geotiff_checks.csv` rows have no cached transform and take the rio_stac
       remote-read branch. The documented full build is ~5.5 h for that many, which is past
       `timeout-minutes: 330` on its own.
    2. The sync never runs, so the prune never runs. The list survives, and every later run
       (cron and dispatch) times out the same way. New items are never published meanwhile.
    3. The footprint dispatch cannot clear it. The rebuild step runs **before**
       footprint_apply, the bulk path that would rewrite the same items in place and then let
       the prune empty the list.
  - **Fix, both cheap:**
    - Leave `urls_footprint_changed.txt` out of the rebuild on `inputs.footprint`. That run's
      footprint_apply rewrites every published item, and the post-sync prune clears the list.
    - Do not default the list for a cold or bulk run. Alternatively, have the rebuild step
      refuse a footprint list over a few thousand URLs with a `::warning` pointing at the
      footprint dispatch.
    - At minimum, keep the file out of the pre-merge cache commit.

- **[fragile] scripts/footprint_apply.py (full-run preconditions). Round-3 row 1; not inside a
  fix.**
  - **The gap:** the cache to be committed is produced by the frozen copy started at 16:09,
    before bee74e5. That copy has no `FootprintNoGeoref` and no `FootprintOutsideBC`.
    findings.md:149-154 records a `NotGeoreferencedWarning` in that run's first 16 minutes.
  - **What would publish a bad footprint:** a non-BCGS no-georef tile would get a footprint at
    pixel coordinates near the equator. footprint_apply would publish it, and none of its gates
    checks geometry location:
    - `item_fields.py audit` checks only that the bbox equals the geometry's bounds.
    - `stacs audit` checks ids and asset keys.
    - item_validate checks the schema.
  - **The only guard** is the planned manual `footprint_extract.py --audit` (findings.md:154).
    That is a human step standing in for a control.
  - **Fix:** footprint_apply already refuses a full run until coverage is complete. Have it also
    refuse when `cache_audit(rows)` is non-empty. That is offline and costs one pass over 102k
    WKTs.
  - The current snapshot audits clean, so nothing is wrong yet.
