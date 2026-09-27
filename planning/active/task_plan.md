# Task: catalogue_register.sh audits for a 'dem' asset, so it cannot register another collection (#42)

## Problem

`scripts/catalogue_register.sh` is documented as usable for any collection (`STAC_COLLECTION`, `STAC_BUCKET_URL`), and `--verify` works that way. But the pre-load audit reads the asset key from this repo's modules:

```bash
AUDIT_DEM=$(... stac_utils.ASSET_DEM)
AUDIT_OLD=$(... item_migrate.ASSET_RENAMES)
register_manifest.py audit-items ... --require-asset "$AUDIT_DEM" --forbid-asset "$AUDIT_OLD"
```

so with `STAC_COLLECTION=stac-airphoto-bc` every item fails `lack asset 'dem'` and nothing is loaded (safely, before any write). Measured: 10,100 of 10,100 refused.

## Context

`scripts/catalogue_register.sh` takes `STAC_COLLECTION` / `STAC_BUCKET_URL` and is
documented as usable for any collection, but its pre-load audit (lines 362–368)
always passes `--require-asset stac_utils.ASSET_DEM --forbid-asset item_migrate.ASSET_RENAMES`.
With `STAC_COLLECTION=stac-airphoto-bc` every item fails `lack asset 'dem'`
(10,100 of 10,100). stac_airphoto_bc's CLAUDE.md currently hand-assembles
`collection_register.sh` + `item_register.sh` + `--verify` because of this.
Airphoto items carry `thumbnail` + `flight_log`, no `item_assets` on the collection.

**Found in exploration, not in the issue:** the issue says the refusal happens
"before any write", but `collection_register.sh` (line 346) runs **before** the
audit (line 366) — so a refused run has already upserted the collection row.
`scripts/README.md:240` makes the same "before anything reaches the database"
claim. Moving the audit above `collection_register.sh` makes it true at zero cost
(the audit only reads the fetched files).

## Design (recommended; alternatives rejected below)

Policy resolved in the shell, keyed on whether the collection is this repo's
(`COLLECTION_ID == collection_patch.COLLECTION_ID`):

| collection | `--require-asset` | `--forbid-asset` |
|---|---|---|
| this repo's (`stac-elevation-bc`) | `stac_utils.ASSET_DEM` — unchanged | `item_migrate.ASSET_RENAMES` — unchanged |
| any other | `STAC_REQUIRE_ASSET` if set, else omitted | `STAC_FORBID_ASSET` if set, else omitted |

- Collection-id homogeneity (`--collection-id`) and `--expect` always run — the
  audit is never skipped, only its asset half is scoped.
- Setting `STAC_REQUIRE_ASSET`/`STAC_FORBID_ASSET` **for this repo's collection
  is an error**, not an override: the elevation rules come from the modules, and
  an env escape hatch on the guard that caught #34's half-rename is the thing
  `code-check.md` ("A guard's scope, escape hatches") warns about.
- The resolved policy is printed (`asset audit: require=… forbid=…` / `asset
  audit: none (foreign collection; set STAC_REQUIRE_ASSET to enable)`), so a
  foreign run says out loud that asset checks are off rather than reading as a pass.
- Rejected: deriving required keys from `item_assets` (airphoto has none, and in
  this repo `dsm` is on 95,888/102,460 items, so `item_assets` is not a
  require-list); a Python `--asset-rules auto` flag (moves module imports into
  `register_manifest.py` for ~10 lines of shell; the e2e test below covers the shell anyway).
- `update.yml`'s two audit steps are this-repo-only and stay as they are.

## Phase 1: Tests first (failing)

- [x] `tests/test_catalogue_register.py` — first end-to-end test of the shell
  script, no network: fixture `collection.json` whose item links are `file://`
  hrefs into `tmp_path`; `STAC_BUCKET_URL=file://…`; a PATH-prepended `ssh` stub
  that answers the `true` probe, logs every other invocation to a file and exits
  1; `STAC_HOST=nobody@stub.invalid` so a stub that failed to shadow still
  reaches nothing; run under `timeout`
- [x] Case: foreign collection, items without `dem` (airphoto shape:
  `thumbnail`, `flight_log`), `--all` → audit prints OK, proceeds to the
  collection upsert (stub log shows one write attempt). Fails today with `lack asset 'dem'`
- [x] Case: foreign collection with `STAC_REQUIRE_ASSET=thumbnail` and one item
  lacking it → audit FAIL, **stub log shows zero write attempts** (pins the reorder)
- [x] Case: this repo's collection, item lacking `dem` / carrying `image` → FAIL,
  zero writes (the #34 guard still fires — restore-the-bug check)
- [x] Case: this repo's collection with `STAC_REQUIRE_ASSET` set → refuses with a
  message naming the variable, before fetching
- [x] Case: foreign collection, one item naming a different `collection` → FAIL
  (homogeneity still enforced when asset checks are off)

## Phase 2: Implementation

- [ ] `catalogue_register.sh`: resolve `OWN_COLLECTION_ID` from `collection_patch`
  (reuse the existing lookup at lines 57–66 rather than a second one) and the
  asset policy per the table; refuse env overrides on the own collection; print
  the resolved policy beside `collection :` / `mode :` at startup, so `--verify`
  and `--dryrun` (which exit before the audit) still show it
- [ ] Build audit args as an array; pass `--require-asset`/`--forbid-asset` only
  when non-empty (bash 3.2 `set -u` empty-array safe: `${ARR[@]+"${ARR[@]}"}`)
- [ ] Move the audit block above `./scripts/collection_register.sh`; update its
  comment so "before any write" is true and says why the order matters
- [ ] Header `Env:` block documents `STAC_REQUIRE_ASSET` / `STAC_FORBID_ASSET`
- [ ] Full `pytest` green; restore the old hard-coded audit line and confirm the
  foreign-collection case goes red, then revert

## Phase 3: Docs and cross-repo

- [ ] `scripts/README.md`: registration section — any collection works; what the
  audit enforces per collection; fix the line-240 ordering claim
- [ ] `NEWS.md` entry (unreleased section; version bump left to `/gh-pr-merge`)
- [ ] Grep for other "before any write / before anything reaches" claims about
  this path (README.md, CLAUDE.md) and correct them
- [ ] File an issue in stac_airphoto_bc to replace its hand-assembled
  registration block with `catalogue_register.sh --drift` once this merges
  (its CLAUDE.md cites stac_dem_bc#42 as the reason it cannot)

## Verification

- `pytest tests/` — new e2e cases plus existing suites (`test_asset_key.py`
  scans `.py` only; new shell code must still name no asset literal, keys come from modules)
- Live, read-only: `STAC_COLLECTION=stac-airphoto-bc STAC_BUCKET_URL=https://stac-airphoto-bc.s3.us-west-2.amazonaws.com scripts/catalogue_register.sh --verify`
  and `--drift --dryrun` (no writes) to confirm the resolved path end to end
- A real `--drift` against airphoto (a write to production pgstac, idempotent
  upsert) is **not** run by me — offered to you after merge

## Validation

- [ ] Tests pass
- [ ] `/code-check` clean on each commit
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
