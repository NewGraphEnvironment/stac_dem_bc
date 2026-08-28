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

### Review findings folded in, verification passed — 2026-08-28

Concurrent code review returned six findings; all six were reproduced before
acting, and all six were real:

1. **A DSM-only month exited 0**, skipping the pairing step *and* the cache
   commit — the refreshed listings would have died with the runner. The exit
   contract now counts a DSM listing change as a change.
2. **No ratchet on the DSM listing.** DEM had a 90%-of-cached guard; DSM had only
   `== 0`. A DSM-heavy truncation passed both guards and would have stripped the
   asset off every item whose DSM fell out. Same ratchet added.
3. **utm zone padding.** The bucket spells it both ways — 26,042 `utm09` against
   8,049 `utm9` — and `092/092l/2023` carries a `utm09` DEM whose only DSM is
   spelled `utm9`. Confirmed reported as `unpaired`. Zone now normalised.
4. **`YYMMDD` dates were not recognised**, so `bc_082l024_2_3_3_..._170529` and
   `..._170607` — the same tile flown twice — collapsed to one match key. A
   bucket-wide sweep found exactly 40 DEM match-key collisions; 34 are the known
   parenthesized duplicates (#8), 2 are these flights, 2 the 083d re-issues.
5. **`sub("^.*/gdwuts/")` returns its input unchanged on no match**, so a URL
   shape change would silently emit full URLs as group names and flip every
   `.laz`-only tile to `no_dsm_dir`. Now asserts the derived shape.
6. **The conservation invariant was a bare `assert`**, stripped by `python -O`.
   Now an explicit raise.

Normalising utm exposed 121 tiles carrying *both* spellings as separate DSM
files. An arbitrary pick recorded them as `convention=unknown`, burying the 3
real unknowns in noise; the tie-break now prefers a DSM whose name relates to
the DEM's by a recognised convention. Final: 95,768 `suffix` (matching #29
exactly), 117 `identical`, 3 `unknown`.

**Sample verification passed** — 500 tiles stratified across convention and year:
COG-status agreement 0.9980 (threshold 0.99), footprint agreement 1.0000 over the
115 tiles whose DEM footprint is cached (threshold 1.00). The footprint rate is
computed over a smaller population on purpose: 60,324 of 100,345 cache rows
predate spatial-metadata caching and carry NaN, and scoring "never recorded" as
"differs" reported a false 15% agreement on the first run.

**A pre-existing bug surfaced by the end-to-end test**: cache lookups compared raw
URL strings, but `urls_list.txt` carries fs::path's single-slash `https:/` form
while every other source carries `https://`. A 6-URL build re-read all six over
the network and appended 6 duplicate rows to `stac_geotiff_checks.csv`. Both
sides now normalise; re-running the same build extracts 0 and leaves the cache
byte-identical.

End-to-end proof: 6 items built, both `image` and `dsm` assets present, 6/6 valid
under `item_validate.py`, collection carries providers/keywords with all 98,040
item links preserved.
