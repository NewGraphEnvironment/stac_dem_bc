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
