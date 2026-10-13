# Plan review 1 — Plan agent, 2026-10-10 (returned after `fdf0f66`)

Written by the parent session from the agent's reply (Plan agents cannot write files). Condensed;
each finding keeps the reviewer's id, with its disposition.

## Blockers
- **B1** Edge heuristic falsified (also found by Phase 1); the cell alone is ~7.5% smaller than the
  box and ~4% smaller than the data, so it buys little except where tiles have gaps (19–25%), plus
  the 2,448 no-quadrant and 2,245 albers tiles. → **User re-decided 2026-10-10: read every tile;
  footprint = cell ∩ data.**
- **B2** Method keyed on name/COG status; overview presence is what matters (954 non-COG
  no-quadrant tiles up to ~340 MB, some with overviews). → Method chosen per file from
  `src.overviews(1)`.
- **B3** Queryables are pgstac's table, not collection JSON; `collection_patch.py` cannot set them.
  → Filed NewGraphEnvironment/stacs#8; #55 body updated; queryables wait on it.
- **B4** Merging new writers makes the monthly cron publish new-format items beside 102k old ones;
  the crate schema URL 404s (verified: 301 → 404 at newgraphenvironment.com). → PR states merge is
  followed by the `footprint` dispatch before the next cron; crate URL must answer before merge.

## Gaps
- **G1** ~58k items built by rio_stac (no cache epsg/bounds); their geometry is a reprojected quad.
  → Rewrite reads native CRS from the item; never regenerates geometry without a footprint row.
- **G2** 160 items `proj:epsg: null` with `proj:wkt2`. → proj:geometry falls back to wkt2; tested.
- **G3** Missed paths: monthly pairing rebuild (rio_stac path), `build_safe.sh`, README Quick Start,
  `stac_create_item.qmd`; `catalogue_qa.py` will report geometry mismatches. → Extract is keyed on
  `urls_list − cache`, covering every path; build_safe/README updated; qmd noted as legacy.
- **G4** A CI rewrite needs wiring at every `backfill || rename` site, incl. the manifest-discard
  loop. → `footprint` input wired at each; test asserts every condition names it.
- **G5** `item_validate --incremental` passes vacuously on a rewrite. → Full validation to scratch,
  as for backfill/rename.
- **G6** No homogeneity guard for new fields. → In-repo field audit step on every staged set.
- **G7** Monthly runtime/backlog; runner throughput unmeasured; fallback items never revisited.
  → `--max-minutes` budget; `data/urls_footprint_changed.txt` drives a rebuild like pairing.
- **G8** Geometry hygiene: specks, holes, vertex cap, outward bias, CCW, validity, WKT precision,
  CSV size. → All specified in Phase 2; cell rows store no WKT.
- **G9** bbox from the rounded geometry; round proj:geometry; integral-degree corners exercise the
  pgstac int round trip. → Done in the helper; noted for `stacs verify`.
- **G10** 44 published items have no source URL. → Rewrite edits only delivery/rename on them;
  counted separately, not errors.
- **G11** proj:geometry ⊆ proj:bbox not guaranteed. → Intersect with raster extent.

## Assumptions
- **A1** Id forms: concatenated (1,659), no-quadrant not a cell, `bcts_` likely clipped. Make
  "cell ⊂ cached bounds" a full-population test. → Adopted.
- **A2** 484 tiles share cell+year; 1,058 cells recur across years. → Moot under full read.
- **A3** Deletions break edge monotonicity. → Moot under full read.
- **A4** `datetime_unknown: true` on albers items is an unprefixed custom field. → User: rename to
  `lidarbc:datetime_unknown`; added to crate#23's body.
- **A5** QGIS box shrinks where bbox shrinks. → Docs corrected.
- **A6** Mask wording: invalid = nodata OR outside −100..5000. → Adopted.
- **A7** Datum for proj:geometry. → Footprint computed in native CRS from pixels; cell transformed
  from NAD83 lon/lat (EPSG:4269); documented.
- **A8** `/vsicurl/` retries need `CPL_VSIL_CURL_NON_CACHED`. → Adopted for overview reads.

## Ordering / scope / acceptance
- **O1** Re-plan before Phase 2 → done (this revision). **O2** Rebase + re-extract before the
  rewrite. **O3** Collection link + queryables in the same registration window. **O4** Crate check
  follows redirects.
- **S1** `valid_percent` via `raster:bands[].statistics` → User: yes. **S2** DSM coverage assumed
  equal to DEM → stated in docs. **S3** qmd legacy → noted.
- **AC1–AC4** Numeric thresholds, geometry acceptance checks, full-population counts, #55 counts
  re-derived (100,171 / 2,245 of 102,416). → In Phases 2, 3 and 6.
