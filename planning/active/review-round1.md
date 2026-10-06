# Code-check round 1 — #49 Phase 1 (staged diff)

Scope: `.github/workflows/update.yml`, `environment.yml`, `stacs.toml`,
`tests/test_stacs_config.py` (+ planning files), as staged. Reviewed against stacs
`v0.1.0` (d4934dd; `git diff v0.1.0 HEAD` in stacs touches only CLAUDE.md).

## Findings

- **[severity: fragile, low]** tests/test_stacs_config.py:83-88 (`test_no_setting_is_a_secret`)
  — the test cannot fail on the property it is named for. `read_config` returns the
  `tomllib.load` dict unchanged (stacs `cli.py` `read_config` only validates table/key
  names), so `assert raw == cfg` is true by construction — both sides come from one
  producer. The only other assertion pins `password_env`'s name. A literal secret placed
  in any allowed value (`pg_user`, `env_file`, `host`, ...) passes. The docstring calls
  it "the second fence, on the values", which nothing in the body checks. Not a live
  defect (no secret is in stacs.toml today, and stacs's key allowlist does refuse a
  `password` key), but the name and docstring claim coverage that does not exist.
  Either drop the claim or make it assert something about values.

## Checked and clean

- stacs.toml values against their sources: `collection_id` = `collection_patch.COLLECTION_ID`
  (`stac-elevation-bc`), `bucket_url` = `stac_utils.PATH_S3_STAC`, `require` = `ASSET_DEM`,
  `forbid` = keys of `item_migrate.ASSET_RENAMES`; `api` = `register_manifest.API_DEFAULT` /
  `catalogue_register.sh` default. `[transport]` matches HEAD's `item_register.sh` remote
  script and `catalogue_register.sh` defaults exactly (host `root@geopro`, db `stac`,
  `. /opt/geoserv/.env`, `PATH=/root/.local/bin`, `PGHOST=localhost PGPORT=5432 PGUSER=stac`,
  `PGPASSWORD="$POSTGRES_PASSWORD"`, `cd /opt/geoserv/scripts`, `uv run pypgstac`).
  `pg_port` is an int, which `Transport.check()` requires (`type(...) is int`).
- Every key is in stacs `CONFIG_KEYS`; `pypgstac` is a list, as `build_transport` requires.
- Pin: tag v0.1.0 exists on the public remote (`git ls-remote` -> d4934dd); repo is PUBLIC,
  so the unauthenticated `git+https` install works on the runner. `requires-python >=3.11`
  is satisfied by the workflow's `uv venv --python 3.12` and by environment.yml's new floor;
  `tomllib` in the test also needs 3.11.
- `stacs.__version__` is `importlib.metadata.version("stacs")`, so the import-check line in
  update.yml resolves.
- `_audit` locates the console script beside `sys.executable`; correct for `.venv/bin` and a
  conda env.
- Mutation (in a `git checkout-index` copy of the index, not the repo): deleting `forbid` ->
  4 red (matches progress.md); `require = "dsm"` -> 2 red; an unknown table -> read_config
  refuses, suite red.
- Full suite on the staged snapshot: 337 passed (`.venv`, Python 3.12.13). Note: the live
  working tree has unstaged later-phase edits (register_manifest.py rewritten, scripts
  removed) under which `tests/test_catalogue_register.py` and `tests/test_item_migrate.py`
  fail to import; that is not part of this commit — make sure it is not swept into it
  (stage by path).
