# Plan review — #42 (Plan agent, 2026-09-27)

Read-only agent; findings returned as reply text and recorded here.

| # | Finding | Disposition |
|---|---|---|
| Gap 1 | Rename window: keying "own" on the collection id alone lets `STAC_COLLECTION=<old id>` + this bucket pass the id check and load old-shape items with no asset audit — the pre-#42 hard-coded rule refused them | **Real, fixed.** "Own" = collection id OR bucket (`stac_utils.PATH_S3_STAC`, trailing slash normalised). Bucket default now read from stac_utils too. Test + mutation. |
| Gap 2 | Shell printed policy from raw env; `--forbid-asset ","` parses to `[]` and audit-items still printed plain OK | **Real (probed: rc=0, plain OK), fixed.** audit-items prints the rules as applied in its `checked` and `OK` lines; `(no asset checks)` when none. Test. |
| Gap 3 | `STAC_REQUIRE_ASSET` takes one key | Documented in the header ("ONE asset key"); fails safe. |
| Gap 4 | Test inherits PYTHON / .venv | Fixed: `PYTHON=sys.executable`. |
| Gap 5 | `--verify` on own collection now imports item_migrate (tqdm) | Accepted: same .venv; collection_patch already pulls rasterio. |
| Ordering 1 | Phase 1 red was from ordering, not the dem rule | Correct; mutation M1 (hard-coded dem, new order) run after the reorder isolates it — 3 foreign tests red. |
| Assumption 1 | "reuse lookup at 57–66" would have left OWN unset when STAC_COLLECTION set | Already handled in the implementation (unconditional lookup, fails loud). |
| Scope 1 | README:240 / CLAUDE.md / NEWS "before anything reaches pgstac" become true after the reorder | Agreed; README gains per-collection scope instead of a correction. |
| Scope 2 | test_asset_key.py does not scan `.sh` | Correct; plan's Verification text overstated it. Shell reads keys from modules by review, not by test. |
| Acceptance 1 | Live `--verify`/`--dryrun` never reach the audit | Done instead: audit-items over stac_airphoto_bc/data/stac — new rules OK 10,100; old rules FAIL 10,100 `lack asset 'dem'`. (Reviewer's "10,102 files" was 10,101 incl. collection.json.) |
| Acceptance 2 | Assert own header line and dryrun line | Added. |
