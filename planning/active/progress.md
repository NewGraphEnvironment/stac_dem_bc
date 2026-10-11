# Progress — tif "footprints" are actually bounding boxes (#2)

## Session 2026-10-10

- Plan-mode exploration — phases approved by user (gate: stage-only publish; proj:geometry = footprint; fold #55; grid cell + read edges)
- Created branch `2-tif-footprints-are-actually-bounding-box` off main
- Scaffolded PWF baseline from issue #2 with approved phases
- Next: start Phase 1

## Session 2026-10-10 (continued)

- Phase 1 measured; edge heuristic falsified (19% interior / 25% edge tiles have gaps); user chose full read + checksum + valid_percent; datetime_unknown → lidarbc:datetime_unknown (crate#23 body edited); queryables → stacs#8 (filed); #55 body updated
- Plan review 1 returned (4 blockers, 11 gaps); dispositions in review-1.md; plan revised (`058ff4e`)
- Phase 2 `dbb4163`: scripts/footprint.py + tests; mutation table all RED but a redundant guard; two tiles declare UTM zone 14
- Phase 3 `2c62f2b`: footprint_extract.py; rehearsal 50/50; CRS guard corrected to "intersects" (2017 rasters cropped to data)
- Full extraction started 2026-10-10T23:09:15Z from a frozen copy of scripts/, 24 workers, `logs/20261010_full_footprint_extract.log`; ~1.3 tiles/s → ~21 h
- Phase 4: item_fields.py (one apply path), item_create + item_reprocess wired, update.yml footprint step (time-boxed) + combined rebuild, build_safe.sh step 4b

## Session 2026-10-10/11 — review and PR

- Phases 5–7 committed (`2aed7f0`, `5c4b38a`, `eae07d7`); georef/BC guards `bee74e5`
- crate lidarbc v1.0.0 shipped without lidarbc:datetime_unknown → crate#26 (v1.1.0), LIDARBC_EXT pinned to v1.1.0
- `/code-check branch`: 4 rounds (`607d197`, `802f7d2`, `d199881`, `61ba107`), plus large-tile decimation `72c0444`. Rounds 2 and 4 each found a defect inside the previous round's fix; ended by enumeration (round 3's 22-row table, re-walked in round 4). Filed #56, #57 (pre-existing).
- Full extraction: 2,729 rows at 00:33Z (0.53 tiles/s); the 954 ~520 MB mapsheet tiles dominate; ETA ~2 days
- Next: draft PR; when extraction completes — `--audit`, commit data/footprints.csv, NEWS counts, delete data/footprint_errors.txt, archive, mark ready
