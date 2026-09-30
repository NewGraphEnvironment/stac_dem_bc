# Plan review — #45 (Plan agent, 2026-09-29, concurrent with Phases 1–3)

Findings in the agent's categories, each with its disposition. The agent is
read-only, so this file was written from its reply.

| # | Finding | Disposition |
|---|---|---|
| B1 | `N_URLS -ne N_TODO` breaks every partial `--drift` once the fetch is the whole catalogue | Built that way from the start: the check compares `N_URLS` with `N_FETCH_IDS`, the set actually fetched |
| B2 | A changed collection with no item changes dies in `audit-items` (0 paths is a FAIL) | Built that way: audit, item load and `verify-serving` run only when `N_TODO > 0`; `test_drift_upserts_a_changed_collection_with_no_item_changes` |
| G1 | The plan text disagreed with the tests (`ids_diff` 3-tuple, `ids_registered` removal, two-state collection compare) | Plan text corrected: `ids_diff` kept, `content_diff` added; `ids_registered` shares `_search_pages` with `bodies_registered`; `collection-state` has three states |
| G2 | A 404 collection has to read as "not registered", because that is what makes a first registration work | `collection_state` returns `missing` only on 404 and raises on anything else; the shell `case`s on the three words. Added `test_drift_bootstraps_a_collection_the_api_has_never_seen` |
| G3 | Nothing verifies the collection upsert afterwards | Built: `COLL_AFTER` must read `same` after every write |
| G4 | `count_lines` reads a missing file as 0 | Built: all three lists are required to exist |
| G5 | The post-register content check had no tests | Added four `verify-serving` tests (pass, a body differs, an id is not served, full bodies with a limit) |
| G6 | Feed paths from a file, never a pipe | Built: `todo_paths.txt` is redirected into both `audit-items` and `item_register.sh` |
| G7 | The e2e tests could not see the item load | The ssh stub logs `<kind> <lines>`, and `SSH_STUB_OK` lets writes succeed. `test_drift_loads_only_the_todo_bodies` asserts `[("collections",1),("items",2)]` out of 4 fetched |
| G8 | Nothing tested that the proxy guard is honoured | Added `test_the_harness_is_network_proof`; a `trust_env=False` mutation is caught. Comment corrected: the run fails after its retries, not at the first request |
| G9 | No fixture id had spaces or parentheses | Added `test_an_id_with_spaces_and_parentheses_round_trips` with hrefs spelled as `encode_url_for_gdal` spells them |
| O2 | `--all/--ids-file --dryrun` must still exit before any fetch | Kept; the existing `href_base=PATH_S3_STAC` dryrun test passes |
| O3/A1 | A 2,000-item sample does not cover items without `dsm`, the 90 space ids, or airphoto | Covered by the full live `--verify` (Phase 4), plus a `--verify` of stac-airphoto-bc |
| O4 | `--drift` now needs ssh even in a month with nothing to do | Stated in NEWS |
| A2 | `item_assets` on a collection would change how pgstac hydrates every item | Added to the CLAUDE.md hazard list |
| A3 | The timing claims need re-measuring on the real code | Measured live in Phase 4 |
| A4 | Ctrl-C would wait for about 100k queued futures | `shutdown(cancel_futures=True)` on any exception |
| A5 | Shell `sort -u` collates by locale | `LC_ALL=C` on the two sorts this change added |
| AC1 | No live positive control | A `file://` copy of the live collection.json with one edited body, then `--verify` (Phase 4) |
| AC2 | The live content check in `verify-serving` never runs | Run `verify-serving` read-only against live bodies. No production write, per the approved plan |
| AC3 | stac-airphoto-bc, the motivating case, was never measured | `--verify` it live, read-only |
| AC4 | Update the timings in the README | Phase 4 |
| AC5 | Keep the phrase "item JSON(s)" in the fetch message | Kept |
| Scope | Audit every published body during `--verify`, since they are all on disk | Not taken; not asked for |
