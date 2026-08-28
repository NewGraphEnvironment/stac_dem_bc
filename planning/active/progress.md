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
