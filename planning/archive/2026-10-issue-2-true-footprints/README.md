## Outcome

Item geometry is now the tile's footprint, not its raster extent (#2). Tiles named on the BCGS
1:2,500 grid get their cell minus any no-data inside it. The other tiles get their valid data.
`bbox` is the bounds of that geometry, and `proj:geometry` is the same footprint in the item's CRS.
The same rewrite folds in #55: `lidarbc:delivery`, the rename of `datetime_unknown` to
`lidarbc:datetime_unknown`, and a `related` link to stac-pointcloud-bc. The `dem` asset gains
`valid_percent`, and every downloaded tile gains `file:checksum` and `file:size`.

One function, `item_fields.item_fields_apply`, serves both the monthly build and the one-time
rewrite of published items (`footprint_apply.py`), so the two cannot differ. Footprints come from
`data/footprints.csv`, which is built by `footprint_extract.py`. That script resumes, keeps disk
use bounded, and is time-boxed in CI.

The approved design was wrong and was replaced mid-run. Phase 1 found that reading only
delivery-edge tiles would miss most gaps: 19% of interior tiles and 25% of edge tiles have gaps,
and the edge test caught 8 of 29. The user then chose to read every tile.

Merge blockers resolved: crate#26 shipped `lidarbc` v1.1.0, and the full cache is committed.

Published 2026-10-13, after the merge and on the user's word. The first `footprint` dispatch
(run 38339265803) rewrote all 102,460 items with 0 errors, then its S3 sync failed on expired OIDC
credentials after 91,772 uploads (#60). The workflow discarded the rewrite manifest as designed. A
re-dispatch (run 38452466305) found those 91,772 unchanged, wrote the other 10,688, and synced them
plus `collection.json`. `stacs register --mode drift` then loaded 102,460 changed items and the
collection, and `stacs verify` reported IN SYNC.

Filed along the way:
- #56: a transient read marks a tile unreadable. Pre-existing.
- #57: the pairing list is not cumulative. Pre-existing.
- #59: the footprint cache is heading for GitHub's 100 MB limit.
- crate#26 (shipped).
- stacs#8: queryables.

Two upstream tiles declare UTM zone 14 under a `utm11` name. They were reported, not repaired.

Code review took 5 rounds, plus a plan review. Rounds 2 and 4 each found a defect inside the
previous round's fix. Round 3 ended the class by enumerating the 22 places the workflow decides
work is done, and round 4 re-walked them. Round 5 reviewed the coastline fix.

PR #58.

## Measurement

- **Throughput:** 7.8 MB/s with 16 workers on 1:2,500 tiles, and only 2.1 to 3.4 MB/s across
  8 to 32 workers on a mixed sample. Throughput is capped by the objectstore, not by worker count.
  The first estimate, 1.4 TB over 5 days, was inflated by 100 MB albers tiles in the sample. The
  real read is about 559 GB for the 1:2,500 tiles plus about 500 GB for 954 strip mapsheets.
- **Full run:** 102,416 tiles with 0 transient errors.

  | outcome | tiles |
  |---|---|
  | downloaded whole | 101,443 |
  | read at overview | 955 |
  | empty | 16 |
  | wrong UTM zone | 2 |

  74,010 footprints are exactly their cell. Median valid_percent is 95.96. The restarted pass
  read 92,172 tiles in 34.1 h, with peak scratch at 11.6 GB.
- **The stall (a wrong turn worth keeping):** the first run slowed from 0.6 tiles/s to 244 rows in
  10.5 h. `sample` showed all 24 threads in GEOS buffer. Buffering the full-resolution staircase by
  a doubling tolerance is quadratic: 72 s at 96 m and 347 s at 192 m on one coastal tile.
  Simplifying first takes 0.26 s and still covers every pixel. A synthetic coastline did not
  reproduce it, so the real mask is the test fixture.
- **What a full tile is:** a 1:2,500 raster is the UTM rectangle around its lat/lon cell plus
  about 22 m of overlap. Cells are numbered from the south, not the north: the first decode
  mirrored every cell, and a population test caught it.

## Evidence

- `research/footprints.md`: the durable verdict.
- `findings.md` in this directory: every probe, in order.
- Local, untracked logs (`logs/*.log` is gitignored here): `logs/20261010_full_footprint_extract.log`
  (the stalled run), `logs/20261011_full_footprint_extract.log` (the run that completed), and
  `logs/20261013_*_rehearsal.log`.
- `data/footprints.csv`, and `tests/fixtures/coast_mask_092b032.npz`.
