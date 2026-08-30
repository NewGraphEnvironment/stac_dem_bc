# Issue #31 — `dsm` as a second asset on each item

**Closed:** 2026-08-29 · **Release:** `v1.0.0` (`1a292dc`) · **PRs:** #32, #33, #36

## Outcome

Every item in `stac-dem-bc` now carries a `dsm` asset beside the bare-earth `image`
where a surface model was delivered — **95,888 of 102,460 items**. Pairing is done on
parsed semantics (tile id + acquisition date + utm zone + mapsheet-year) with the
naming convention recorded *afterwards* as an assertion on an already-matched pair, so
an unfamiliar convention surfaces as `convention=unknown` in `data/dsm_pairing_report.md`
rather than dropping a DSM silently. `/dem/` → `/dsm/` path swapping resolves in only
4 of 157 mapsheet-years and is what had previously produced the false finding that BC
publishes no surface models.

The work grew well past "attach an asset". Phase 9 was added mid-flight to cover
publishing, which the original plan never addressed:

| | before | after |
|---|---|---|
| items on S3 | 98,040 | 102,460 |
| items in pgstac / API | 60,126 | 102,460 |
| items with `dsm` | 0 | 95,888 |
| raw-space (unusable) hrefs | 90 | 0 |
| `providers` / `keywords` | absent | live |

The API had been ~38,000 items behind S3 for a month. The 90 broken hrefs were #25's
tail — carrying literal spaces, they could not be formed into an HTTP request at all;
one now returns 206 where it previously returned `000`.

## What the doing taught, that the planning did not

- **Rewrite, do not rebuild.** 60,324 of 100,345 cache rows predate spatial-metadata
  caching, so a rebuild would have silently swapped ~60k items from the `rio_stac`
  path to the `item_create_from_cache` path — invisible in a spot check.
  `scripts/item_backfill.py` fetches, edits one field, writes back.
- **A guard must not fail toward "abort" either.** The first backfill completed all
  98,040 items and then exited non-zero on **2** transient failures (0.002%), skipping
  the publish and discarding a 16m37s run. Fixed in `9a970dc` — retry in-process, gate
  on a rate against a stated tolerance.
- **pgstac registration caused a real outage.** rtj's `stac_register-pypgstac.sh`
  DELETEs the collection before loading it and died in between on `cat "$DIR"/*.json`
  (ARG_MAX, ~6 MB of argv). `images.a11s.one` served zero items until repaired by hand.
  A *recurrence* — rtj#196 hit it in 2026-07, wrote the convention entry, and never
  repaired the script. Both fixed (`soul@95eddb2`, rtj#238). **This is what #27's
  upsert work exists to make impossible.**
- **A progress bar on stderr eats your log lines.** Per-item `logger.warning` output
  interleaved with tqdm was unrecoverable; the failing ids had to be reconstructed by
  differencing a manifest against the input set.

Three bug classes appended to `soul/conventions/code-check.md` (`497b20c`):
fail-toward-abort, progress-bars-eat-log-lines, fix-the-writer-reconcile-the-data.

## Spun out

- **#34** — rename to `stac-elevation-bc`, carrying the two breaking changes #31 deferred
- **#35** — 175,172 point cloud `.laz`, 15,752 orthophoto `.tif`, 264 CHM `.tif`, none
  indexed; point cloud is what would actually close the 1,211-tile `no_raster_dsm` gap
- **#27** — client-side pgstac registration via `--method upsert`, so re-registration
  stops being an outage

## Note on the checkboxes

Five boxes were reconciled at archive time against what `progress.md` records as
landed and verified. `/code-check` clean on each commit is marked `[~]` — there is no
record of it in the log either way, and checking it would have been a claim rather
than a fact.
