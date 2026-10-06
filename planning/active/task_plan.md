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
- [ ] `update.yml` install step: add `"stacs @ git+https://github.com/NewGraphEnvironment/stacs@v0.1.0"`; import check adds `stacs`
- [ ] `environment.yml`: same pip line; `python>=3.11`
- [ ] Install into local `.venv`; `stacs --version` reports 0.1.0
- [ ] `stacs.toml` at repo root: `[catalogue]` api/collection_id/bucket_url, `[assets]` require = `dem`, forbid = `["image"]`, `[transport]` with the values above
- [ ] `tests/test_stacs_config.py`: toml parses through `stacs.cli.read_config`; `collection_id == collection_patch.COLLECTION_ID`, `bucket_url == stac_utils.PATH_S3_STAC`, `require == ASSET_DEM`, `forbid == list(ASSET_RENAMES)`; installed `stacs.__version__ == "0.1.0"`; `stacs audit --config stacs.toml` fails an `image`-keyed fixture and a wrong-collection fixture, passes a good one (proves the config is wired, not just present)

## Phase 2: Workflow audits → `stacs audit`
- [ ] Both `audit-items` steps become `.venv/bin/stacs audit --config stacs.toml --dir "$STAC_OUTPUT_DIR" --collection-id "$COLLECTION"` (+ `--expect` on the monthly one); drop the `DEM`/`OLD` derivation and update the comments (rules now come from `stacs.toml`, pinned to the modules by the Phase 1 test)
- [ ] `test_asset_key.py` workflow scanner still green (no literal key in the workflow)

## Phase 3: Delete the old registration layer
- [ ] Delete `catalogue_register.sh`, `item_register.sh`, `collection_register.sh`
- [ ] `register_manifest.py` → `item_ids_from_urls` + `ids-from-urls` CLI only (header says the rest is stacs)
- [ ] Tests: delete `test_catalogue_register.py`; cut `test_register_manifest.py` to the `item_ids_from_urls` tests + the encoder/decoder inverse tests (decoder via `stacs.catalogue.collection_item_links`); remove `audit_items`/`ndjson_write` tests from `test_item_migrate.py` (covered by stacs and by the Phase 1 config test); fix `test_collection_identity.py`'s reference to `catalogue_register.sh`
- [ ] Comments naming the deleted scripts: `collection_unregister.sh` (recovery → `stacs register --config stacs.toml --mode all`), `collection_patch.py`, `item_migrate.py`
- [ ] Full `pytest tests/` green

## Phase 4: Docs
- [ ] `scripts/README.md` registration section: `stacs verify` / `stacs register --mode drift|all|ids`, `ids-from-urls | stacs register --mode ids` recipe, the own-bucket paragraph replaced by "rules declared in `stacs.toml`, flags add, never loosen"
- [ ] `README.Rmd` → re-knit `README.md`
- [ ] `CLAUDE.md` project section: registration commands, the #42 own-bucket hazard rewritten, "Related work: stacs" now landed
- [ ] `research/pgstac_round_trip.md` → short pointer to stacs' copy (path kept; it is cited); `research/README.md` row updated
- [ ] `NEWS.md` entry (version bump left to `/gh-pr-merge`)

## Phase 5: Live check (read-only)
- [ ] `stacs verify --config stacs.toml --out-dir <scratch>` against the live API, logged to `logs/`; expect in sync (exit 0)
- [ ] `stacs register --config stacs.toml --mode drift --dryrun` — exercises transport/config without writing
- [ ] Record timings and result in `findings.md`

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean on each commit
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
