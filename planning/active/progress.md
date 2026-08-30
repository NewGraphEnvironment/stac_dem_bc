# Progress — Client-side pgstac registration (#27)

## Session 2026-08-30

- Archived and closed #31 (v1.0.0 shipped); reconciled 6 stale checkboxes
- Plan-mode exploration: 3 Explore agents (stac_uav_bc reference, this repo's
  surface, rtj's host + server-side script) plus direct probes of the live API
  and host
- Corrected the issue's premise: #27 does **not** unblock #34 — a rename to a new
  collection id is already zero-downtime. The real value is the monthly lag and
  making same-id re-registration safe
- Plan approved by user; scope set to registration + version stamp, with `--all`
  and drift both owned here, plus `collection_unregister.sh` for #34
- Plan agent review returned 4 blockers folded in pre-baseline: FK ordering
  (collection before items), stdin already spoken for, remote-side count guard,
  and version-is-not-an-item-check
- Next: Phase 1 (`register_manifest.py`) — lands first because the shell cannot
  be correct without it
