# Findings — catalogue_register.sh audits for a 'dem' asset, so it cannot register another collection (#42)

## Issue context

**If we do this:** `catalogue_register.sh --all/--drift` registers any collection on the endpoint, not only this one. **If we never do:** another collection has to hand-assemble `collection_register.sh` + `item_register.sh` + `--verify`, as stac_airphoto_bc did for its #23 publish (2026-09-27), and gets none of the fetch/drift logic.

## Problem

`scripts/catalogue_register.sh` is documented as usable for any collection (`STAC_COLLECTION`, `STAC_BUCKET_URL`), and `--verify` works that way. But the pre-load audit reads the asset key from this repo's modules:

```bash
AUDIT_DEM=$(... stac_utils.ASSET_DEM)
AUDIT_OLD=$(... item_migrate.ASSET_RENAMES)
register_manifest.py audit-items ... --require-asset "$AUDIT_DEM" --forbid-asset "$AUDIT_OLD"
```

so with `STAC_COLLECTION=stac-airphoto-bc` every item fails `lack asset 'dem'` and nothing is loaded (safely, before any write). Measured: 10,100 of 10,100 refused.

## Fix

A `STAC_REQUIRE_ASSET` env (default `stac_utils.ASSET_DEM`) and skip `--forbid-asset` when the collection is not this repo's, or make both audit flags follow the collection. `item_register.sh`, `collection_register.sh` and `--verify` are already generic.

## Exploration (2026-09-27)

- The audit runs AFTER `collection_register.sh` (catalogue_register.sh:346 vs :366),
  so a refused run has already upserted the collection row. The issue's "before any
  write" and scripts/README.md:240's "before anything reaches the database" are both
  wrong about the collection row; items are genuinely protected.
- stac_airphoto_bc items carry `thumbnail` + `flight_log`; its collection has no
  `item_assets`. So there is no universal key to require by default for a foreign
  collection, and `item_assets` cannot serve as a require-list here either (`dsm` is
  on 95,888 of 102,460 items).
- `update.yml` runs `audit-items` twice with explicit flags, over this repo's
  collection only — unaffected.
- `tests/test_asset_key.py` scans `.py` under scripts/ for literal asset keys, not
  `.sh`; the shell still reads keys from the modules.
- No test exercises catalogue_register.sh today. `file://` hrefs pass
  `_href_to_id` and `curl -fsSL`, so an e2e test with a stubbed `ssh` is feasible.

## Errors Encountered

| Error | Resolution |
|-------|------------|
