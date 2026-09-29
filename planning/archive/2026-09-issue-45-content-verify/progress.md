# Progress — catalogue_register.sh --verify/--drift cannot see an item whose content changed (#45)

## Session 2026-09-28

- Plan-mode exploration — body roundtrip measured on 2,000 items (0 differ minus `links`),
  full-body API page timed (10k in 29 s) — phases approved by user
- Created branch `45-catalogue-register-sh-verify-drift-canno` off main
- Scaffolded PWF baseline from issue #45 with approved phases
- Next: start Phase 1

## Session 2026-09-29

- Phase 1: tests written first. 31 unit tests in `test_register_manifest.py` fail
  on missing functions; 7 of 9 e2e cases in `test_catalogue_register.py` fail
  against the current script, the other two being the in-sync controls. The
  headline case reproduces #45 verbatim — a changed body, and the old script
  prints `IN SYNC: 3 published, all registered, no orphans` and exits 0.
- e2e harness: a localhost stub STAC API (`/search` keyset paging + `fields`,
  `/collections/<id>`), and proxy env vars pointed at a dead port so the Python
  fetcher is network-proof the way `CURL_STUB` makes curl network-proof.
- Phases 2–3: `register_manifest.py` gains `body_digest`/`_canonical`, `content_diff`,
  `bodies_registered` (sharing `_search_pages` with `ids_registered`), `fetch_bodies`,
  `published_digests`, `collection_state`, and the `fetch-bodies`, `fetched-paths`
  and `collection-state` subcommands. `catalogue_register.sh` fetches with the Python
  fetcher in every mode; `--verify`/`--drift` compare content. Plan review:
  `review-plan.md`.
- Code-check: round 1 Clean. Round 2 found two latent defects: a duplicated
  identical link was caught only after the write, and a collection upsert could
  change untouched items. Round 3 found a third of the same mechanism: a body naming
  another id was caught only after the write. All three are fixed, each with a test
  and a mutation check. The loop ended by enumeration: every `published_digests`
  raise now has a check before the write, and all 102,460 + 10,100 live items
  round-trip.
- Live acceptance: IN SYNC across 102,460 items; `--drift --dryrun` has nothing to do.
