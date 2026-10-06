# Task: Adopt stacs for registration and verification (#49)

## Problem

This repo's registration and verification layer is now the `stacs` package
(NewGraphEnvironment/stacs#1), extracted from `scripts/register_manifest.py`,
`scripts/catalogue_register.sh`, `scripts/item_register.sh` and
`scripts/collection_register.sh`. Until this repo adopts it there are two live copies, and
they will drift -- the duplication stacs#1 exists to remove.

Parity was measured before anything switches (2026-10-06): `stacs verify` and
`register_manifest.py diff` report the same missing / changed / orphaned sets and
collection state for `stac-elevation-bc` (102,460 items) and `stac-airphoto-bc` (10,100),
the RFC 8785 digest agrees with the old one on every item's verdict, and both tools report
exactly the one item edited in a positive-control copy.

## Phase 1: Pin stacs and declare the catalogue
- [x] `update.yml` install step: add `"stacs @ git+https://github.com/NewGraphEnvironment/stacs@v0.1.0"`; import check adds `stacs`
- [x] `environment.yml`: same pip line; `python>=3.11`
- [x] Install into local `.venv`; `stacs --version` reports 0.1.0
- [x] `stacs.toml` at repo root: `[catalogue]` api/collection_id/bucket_url, `[assets]` require = `dem`, forbid = `["image"]`, `[transport]` with the values above
- [x] `tests/test_stacs_config.py`: toml parses through `stacs.cli.read_config`; `collection_id == collection_patch.COLLECTION_ID`, `bucket_url == stac_utils.PATH_S3_STAC`, `require == ASSET_DEM`, `forbid == list(ASSET_RENAMES)`; installed `stacs.__version__ == "0.1.0"`; `stacs audit --config stacs.toml` fails an `image`-keyed fixture and a wrong-collection fixture, passes a good one (proves the config is wired, not just present)

## Phase 2: Workflow audits → `stacs audit`
- [x] Both `audit-items` steps become `.venv/bin/stacs audit --config stacs.toml --dir "$STAC_OUTPUT_DIR"` (+ `--expect` on the monthly one); drop the `DEM`/`OLD` derivation and update the comments (rules now come from `stacs.toml`, pinned to the modules by the Phase 1 test). `--collection-id` dropped rather than passed (review-plan S2): items are checked against the declared id
- [x] `test_asset_key.py` workflow scanner still green (no literal key in the workflow)

## Phase 3: Delete the old registration layer
- [x] Delete `catalogue_register.sh`, `item_register.sh`, `collection_register.sh`
- [x] `register_manifest.py` → `item_ids_from_urls` + `ids-from-urls` CLI only (header says the rest is stacs)
- [x] Tests: delete `test_catalogue_register.py`; cut `test_register_manifest.py` to the `item_ids_from_urls` tests + the encoder/decoder inverse tests (decoder via `stacs.catalogue.collection_item_links`); remove `audit_items`/`ndjson_write` tests from `test_item_migrate.py` (covered by stacs and by the Phase 1 config test); fix `test_collection_identity.py`'s reference to `catalogue_register.sh`
- [x] Comments naming the deleted scripts: `collection_unregister.sh` (recovery → `stacs register --config stacs.toml --mode all`), `collection_patch.py`, `item_migrate.py`
- [x] Full `pytest tests/` green

## Phase 4: Docs
- [x] `scripts/README.md` registration section: `stacs verify` / `stacs register --mode drift|all|ids`, `ids-from-urls | stacs register --mode ids` recipe, the own-bucket paragraph replaced by "rules declared in `stacs.toml`, flags add, never loosen"
- [x] `README.Rmd` → `README.md`, `README.html`, `index.html` (the Pages landing page) hand-edited in step, as prior README commits do
- [x] `CLAUDE.md` project section: registration commands, the #42 own-bucket hazard rewritten, "Related work: stacs" now landed
- [x] `research/pgstac_round_trip.md` → continuation header pointing at stacs' copy, original body kept (stacs' research README cites it as the original measurement — review-plan S3); `research/README.md` row updated
- [x] `NEWS.md` entry (version bump left to `/gh-pr-merge`)

## Phase 5: Live check (read-only)
- [x] `stacs verify --config stacs.toml --out-dir <scratch>` against the live API, logged to `logs/`; expect in sync (exit 0)
- [x] Only if verify is IN SYNC: `stacs register --config stacs.toml --mode drift` (no `--dryrun`: a drift dryrun skips the API/ssh probe entirely, though it still fetches and compares — review-plan A1). Probes API + ssh, then "nothing to register": no write
- [x] Record timings and result in `findings.md`

## Validation

- [x] Tests pass
- [x] `/code-check` clean on each commit
- [x] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
