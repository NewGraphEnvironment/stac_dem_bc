# Findings — Adopt stacs for registration and verification (#49)

## Issue context

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

## Work

- [ ] Pin `stacs` at `v0.1.0` once that tag exists (this repo has no `pyproject.toml`; add
      it to `.venv` / `environment.yml` as `stacs @ git+https://github.com/NewGraphEnvironment/stacs@v0.1.0`)
- [ ] Commit a `stacs.toml` declaring this catalogue: `api`, `collection_id`, `bucket_url`;
      `[assets] require = <ASSET_DEM>`, `forbid = <ASSET_RENAMES keys>`; `[transport]` with
      today's host, `db`, `env_file`, `workdir`, `path_prepend`, `pg_user`,
      `password_env = "POSTGRES_PASSWORD"`, `pypgstac = ["uv", "run", "pypgstac"]`
- [ ] Replace `catalogue_register.sh --verify|--drift|--all|--ids-file` with
      `stacs verify` / `stacs register --mode drift|all|ids`, and delete
      `catalogue_register.sh`, `item_register.sh`, `collection_register.sh` and the
      registration half of `register_manifest.py`
- [ ] `.github/workflows/update.yml` (the two `register_manifest.py audit-items` calls,
      ~lines 338 and 363) → `stacs audit --config stacs.toml --dir ... --expect ...`.
      Note: stacs refuses an empty `--forbid-asset`, where the old CLI read it as "no
      check"
- [ ] Keep what is source-specific: `item_ids_from_urls` / `ids-from-urls`,
      `collection_unregister.sh` (a delete path; stacs is upsert-only), item creation,
      and `item_validate.py`'s CSV tracking (`stacs validate` covers the pystac check)
- [ ] `research/pgstac_round_trip.md` points at stacs' copy, which continues it

## Differences to expect

The own-bucket test from #42 is gone: the catalogue declares its rules in `stacs.toml`
and a flag can add to them, never loosen them, whatever collection id it names. A remote
load must now confirm itself on stdout; on bash 3.2 a failed `.` under an EXIT trap exits
0. See stacs' NEWS.

## Exploration (plan mode, 2026-10-06)

Registration/verification was extracted into `stacs` (stacs#1). `v0.1.0` is tagged and the
repo is public. Until this repo switches, there are two live copies and they will drift.
Parity was already measured (issue body), so this is a cutover: config + pin, swap callers,
delete the old copies, keep what is source-specific.

What exploration found that shapes the phases:

- `register_manifest.py` (921 lines) — only `item_ids_from_urls` / `ids-from-urls` is
  source-specific. No script imports anything else from it; the importers are tests
  (`test_register_manifest.py`, `test_item_migrate.py` for `audit_items`/`ndjson_write`).
- `update.yml` lines ~331–363: two `audit-items` calls, which derive `DEM`/`OLD` from
  `stac_utils.ASSET_DEM` / `item_migrate.ASSET_RENAMES` at run time. The rewrite-run audit
  only adds asset rules on `rename`; with `--config stacs.toml` they apply on every run. That
  is correct now — every item is post-rename (`dem`, no `image`) — and is the stacs design.
- `stacs.toml` spells `collection_id`, `bucket_url`, `require`, `forbid` as literals. That is
  a second definition of values `test_asset_key.py` / `test_collection_identity.py` exist to
  keep single — so a test must pin the toml to the modules.
- Transport values today (`item_register.sh`): `root@geopro`, db `stac`, env
  `/opt/geoserv/.env`, workdir `/opt/geoserv/scripts`, PATH `/root/.local/bin`,
  `localhost:5432`, user `stac`, `POSTGRES_PASSWORD`, `uv run pypgstac`.
- stacs needs Python ≥3.11. CI already uses 3.12 (`uv pip install` into `.venv`); local
  `.venv` is 3.12; `environment.yml` says `python>=3.9`.
- The encoder (`stac_utils.url_to_item_id`/href encoding) stays here and the decoder
  (`stacs.catalogue.collection_item_links`) is now in stacs: that inverse is a
  cross-boundary contract worth keeping as a test.

Kept, by name: `register_manifest.py` (reduced to `ids-from-urls`; filename kept because the
issue and docs name it — say if you'd rather rename), `collection_unregister.sh`,
`item_validate.py`, item creation.


## Errors Encountered

| Error | Resolution |
|-------|------------|

## `ids-from-urls` refused every stored URL (pre-existing, fixed in Phase 3)

Found 2026-10-06 while smoke-testing the reduced `register_manifest.py`. Every line of
`data/urls_list.txt` (102,416) and `data/urls_dsm.txt` (95,889) stores the scheme as
`https:/` — one slash — and `item_ids_from_urls` checked `startswith(PATH_S3)` on the raw
line, so `ids-from-urls --urls-file data/urls_*.txt` raised on line 1. `stac_utils.fix_url`
already existed for exactly this and was never applied here. Now applied; all 102,416
lines map. Test: `test_item_ids_from_urls_reads_the_lists_as_stored`.

## Live check (Phase 5, 2026-10-06)

Config: a frozen copy of `stacs.toml` (values identical to the committed file, checked by
parsing both). Logs: `logs/20261006_14*_stacs_*_49.log` (gitignored).

| run | result | wall time |
|---|---|---|
| `stacs verify --config stacs.toml --out-dir …` | IN SYNC — 102,460 published, 102,460 registered, missing 0, orphaned 0, changed 0, collection `same`; 0 fetch failures | 16m29s |
| `stacs register --config stacs.toml --mode drift` (no `--dryrun`) | `to register: 0` / `nothing to register -- already in sync`, exit 0 | 16m37s |

The drift ran `_api_probe` and the ssh `probe` (`writes = mode != "verify"`), both silent on
success, so the API and `root@geopro` over BatchMode ssh are confirmed reachable with this
config. **Not exercised:** the remote load script (`env_file`, `workdir`, `path_prepend`,
PG* environment, `uv run pypgstac`, the `STACS_LOADED` confirmation). That runs only when
there is something to write; the first month with changes is its first live use, and a
`stacs load collection` of the identical published body would exercise it on demand (a
write — the user's call).

Within the 12–26 min range measured for the old scripts on 2026-09-29.
