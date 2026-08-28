# Progress — Add `dsm` as a second asset on each item (#31)

## Session 2026-08-28

- Plan-mode exploration: read `item_create.py`, `stac_utils.py`, `collection_create.py`,
  `urls_fetch.R`, `detect_changes.R`, `update.yml`, `s3_sync-ci.sh`; probed the live
  objectstore, the S3 collection, and the STAC API
- Established that the issue's 60,126 figure is the pgstac registration count, not the
  catalog (98,040 item links on S3) — scope of the "catch up" bullet reduced accordingly
- Three decisions taken with the user: reconcile the ~2.2k real DEM gap only (pgstac
  stays with #27); inherit DSM media type from the paired DEM with sample verification;
  add `dsm` while leaving `image` and the `-dem-` item id alone
- Created branch `31-add-dsm-as-a-second-asset-on-each-item` off main
- Scaffolded PWF baseline with the approved phases
- Next: Phase 1 — reconcile the DEM listing gap

### Phases 1-4, 7 (partial) — 2026-08-28

- Reconciled the DEM gap: an independent full bucket walk (575,411 keys) reproduces
  #29 exactly — 100,171 DEM `.tif`, 95,889 DSM `.tif`, 264 CHM, 15,752 orthophoto.
  **The listing filter was not at fault**: `pattern = c("dem", "*.tif")` yields
  102,416 = 100,171 under `dem/` + 2,245 `albers10k2m/_completed_dem/`, exactly
  accounted for. The shortfall is 4,420 genuinely new DEM tiles arrived since the
  2026-08-03 run. `detect_changes.R` confirmed: 4,420 new, 0 deleted.
- Restructured listing into `scripts/urls_listing.R`: ONE bucket walk yields DEM
  keys, DSM keys and `dsm/` directory membership. The third artifact is what makes
  a `.laz`-only delivery a declared gap rather than indistinguishable from "no DSM".
- `source()` moved inside `detect_changes.R`'s tryCatch — at top level a broken
  helper would abort with R's default status 1, which the workflow reads as
  "changes detected" rather than "error".
- Pairing reconciles with #29 to the tile:

  | outcome | tiles | #29 |
  |---|---|---|
  | paired, convention `suffix` | 95,768 | 95,768 |
  | paired, convention `identical` | 117 | 117 |
  | paired, convention `unknown` | 2 | — (new) |
  | `no_raster_dsm` (11 groups) | 1,211 | 1,211 |
  | `unparseable` (all albers10k2m) | 2,245 | — |
  | `no_dsm_dir` | 2,900 | — |
  | `unpaired` | 173 | — |
  | **total** | **102,416** | |

- Two bugs the whole-bucket run surfaced, both now fixed and regression-tested:
  1. **Tile codes come in two widths.** `092h001212` (6 digits after the letter,
     1,659 tiles) was rejected by a `\d{3}[a-z]\d{3}` parser, which put 901 of the
     1,211 `.laz`-stranded tiles into `unparseable` instead of the coverage gap.
  2. **083d/2019 ships a re-issued DEM beside its original** (`..._2019.tif` and
     `..._2019_1.tif`) sharing one DSM. Both legitimately carry the asset, but the
     sharing is now reported — the identical shape would appear if the match key
     were too coarse, and those two cases must not look the same.
- The `unknown` convention count of 2 is that same 083d pair. The assertion design
  worked exactly as the issue intended: an unfamiliar name still paired on tile id
  and date, and surfaced in the report rather than dropping a DSM.
- Guards mutation-tested: collapsing `no_raster_dsm`, treating an empty listing as
  no-DSM, and dropping unpaired DEMs each fail the suite (1, 1 and 4 tests).
