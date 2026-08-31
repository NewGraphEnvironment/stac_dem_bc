# Task: Rename collection to stac-elevation-bc, and rename the image asset to dem (#34)

## Problem

The collection is `stac-dem-bc`, but as of v1.0.0 it no longer holds only DEMs —
every item carries a `dsm` asset alongside the bare-earth `image` (#31). The name
describes one of two assets, and will describe less still as further products land
here (#35).

More importantly, **#31 deferred two breaking changes to "the point a collection
rename happens"**, on the reasoning that consumers should absorb one break rather
than two. With no issue for the rename, those deferrals have no home:

1. **Asset key `image` should be `dem`.** `image` was never descriptive and is now
   actively ambiguous beside `dsm`.
2. ~~**Item ids embed `-dem-`**~~ — **decided 2026-08-30: keep them.** Ids are
   identifiers rather than descriptions, and the `-dem-` segment is the source
   product directory, which turns out to be load-bearing (it keeps the
   DEM/DSM/CHM tiling apart from the finer `pointcloud` tiling that #35 must add
   as separate items).

**Item ids and therefore S3 keys are unchanged**, so item JSONs are rewritten in
place: no new objects, no orphans, no cleanup pass, asset hrefs stay valid. The
**S3 bucket keeps its name** `stac-dem-bc` — IaC-managed in `rtj`, a separate and
larger decision.

Outcome: `images.a11s.one/collections/stac-elevation-bc` serves 102,460 items,
each carrying `dem` and (where paired) `dsm`; `stac-dem-bc` is gone.

## Decisions taken (2026-08-31, with the user)

| | |
|---|---|
| Repo rename (`stac_dem_bc` → …) | **Separate issue.** Different blast radius — it breaks AWS OIDC (`rtj/env/prod/main.tf:103` feeds `sub = repo:NewGraphEnvironment/stac_dem_bc:ref:refs/heads/main`), not consumers. File it in Phase 8. |
| Sep 3 cron (`23 9 3 * *`, workflow **active**) | **Finish before it fires.** Target dispatch Sep 1. |
| Dropping `stac-dem-bc` | **Immediately after verify**, per the issue. |

### The risk that last decision carries — stated once, not re-litigated

Once S3 carries only the new bodies, pgstac's `stac-dem-bc` rows are **the last
copy anywhere** of the old-shaped catalogue. `--all` cannot rebuild them; it
fetches from S3. `collection_unregister.sh --yes stac-dem-bc` is therefore the
point of no return, irreversible the moment it runs. Phase 7 gates it on a *real
consumer query* succeeding, not on schema validation. Nothing forces the drop on
a schedule; it can be deferred at any time at the cost of ~205k rows in pgstac.

---

## Phase 0 — Pre-flight fixes and baseline

Independent of the rename. Merge first so the cutover's own verification is
trustworthy.

- [x] `register_manifest.py`: `search_body(ids, collection_id)` sets
      `"collections": [collection_id]`; thread it through `ids_serving` and
      `verify-serving`. Test: `test_search_body_scopes_to_the_collection`
- [x] `catalogue_qa.py:72-75`: compare `sorted(assets.keys())`, not `len(assets)`
      (one line; *not* a cutover gate — it has no tests and a hardcoded personal
      `--local-dir` default)
- [ ] Baseline, on a tailnet machine — must be clean before anything moves, or
      "the rename lost items" and "we were already behind" become
      indistinguishable:
      `scripts/catalogue_register.sh --verify` must print IN SYNC, then
      `register_manifest.py ids-registered --collection-id stac-dem-bc`
      saved to `~/stac-dem-bc-baseline-ids.txt`
- [x] Confirm `data/urls_pairing_changed.txt` is empty (see Phase 5 clobber guard)

## Phase 1 — Extract the rewrite harness (no behaviour change)

`item_backfill.py`'s fetch/retry/manifest/threadpool/error-gate block is ~120
lines where each behaviour has a named CI incident behind it. Fork it and the
policy forks with it.

- [x] New `scripts/item_rewrite.py`: move `published_item_ids`, `item_fetch`,
      `process_one`, `ERROR_RATE_MAX`, `ERROR_ABS_MAX`, plus
      `error_tolerable(errors, processed)` and `manifest_load(path, migration)` —
      the latter reading a `# migration: <name>` header line and **refusing a
      manifest from another migration**
- [x] `verify_rewrite(...)`: re-fetch published, apply the edit to a *fresh*
      copy, DeepDiff that against the file on disk, expect empty. This replaces
      the allowlist rather than widening it — an allowlist that grows a branch
      per migration is the exemption-list failure in `CLAUDE.md`
- [x] `item_backfill.py` becomes a thin caller; **`item_edit` keeps its name**
- [x] `tests/test_item_backfill.py` passes with **zero edits** — that is the
      proof the extraction was faithful, and it only works if the file does not
      change in this commit
- [ ] Delete the `_tolerable()` mirror at `tests/test_item_backfill.py:139-142`
      in favour of importing the real gate (one fact derived twice)

## Phase 2 — Asset key `image` → `dem` for newly created items

- [ ] `stac_utils.py`: `ASSET_DEM = "dem"`, `ASSET_DSM = "dsm"`; point all four
      writers at them (`stac_utils.py:358`, `item_create.py:148,155`,
      `item_reprocess.py:103,110`)
- [ ] `item_backfill.py:120`'s read of `assets["image"]` — it inherits media type
      onto a new `dsm` and **silently downgrades COG → plain tiff** if missed
- [ ] `tests/test_asset_key.py::test_no_script_writes_a_literal_asset_key` —
      `ast.parse` every `scripts/*.py`; no `add_asset()` first arg, no
      `asset_name=`, no `.assets[...]` subscript may be an `ast.Constant`. A
      structural invariant, because the `rio_stac` fallback reads a remote raster
      and no runtime test can compare the two paths
- [ ] **Restore the bug:** put `'image'` back at `item_create.py:148`, confirm
      red, revert

## Phase 3 — The collection id becomes one constant

Inside `collection_patch()`'s idempotence contract, alongside `PROVIDERS` /
`KEYWORDS` / `DESCRIPTION` — *not* outside it like `version_stamp`. A version's
correct value changes every release; a collection id is a constant fact. Inside
the contract, the monthly run carries it and `--check` can see a regression.

- [ ] `collection_patch.py`: `COLLECTION_ID`, `COLLECTION_TITLE`, and
      `links_retitle(collection, title)` for the **root link's title** — the
      fourth spelling a two-field patch would leave contradicting the other three
- [ ] Rewrite `DESCRIPTION` (`:65-72`): name `dem`, delete the backward-compat
      sentence, which expires with this change
- [ ] Strengthen the item-link invariant at `:223-229`: assert the item link
      **hrefs** round-trip (`== [encode_url_for_gdal(h) for h in before]`), not
      just the count — renaming the id introduces an href-mutation failure the
      count cannot see
- [ ] `collection_create.py:54,109` import the constants; no literal id left
- [ ] `catalogue_register.sh:42` reads the constant and **aborts if the read
      fails** rather than falling back to a literal. Keep the `:97-105` guard
      verbatim — it is the cutover's primary safety. Rewrite the `:25-27` header:
      "two knobs over one fact" is now **false**, since the bucket keeps its name
- [ ] Tests: the new id is defined in exactly one place under `scripts/`; every
      surviving `stac-dem-bc` in `scripts/` is inside a bucket URL (that *is*
      "the bucket is out of scope", stated as a test). Strip comments before
      matching, so a live literal cannot hide as one

## Phase 4 — The migration tool and the homogeneity gate

- [ ] `scripts/item_migrate.py` — named for what it does; item ids do **not**
      change, so `item_rename.py` would mislead. Own manifest
      `data/migrate_done.txt`, own errors log,
      `MIGRATION = "34-collection-rename"`.
      `item_migrate(item, collection_id, asset_renames)` applies three edits in
      place, returns what changed (`[]` means do not rewrite), is idempotent, and
      **raises if an item carries BOTH keys** — that is a half-done previous run
      and continuing would bury it. Asset hrefs are NOT touched: they contain the
      literal segment `/dem/`, and `links[rel=collection]` points at the bucket,
      which keeps its name
- [ ] Rebuild the assets dict by comprehension so `dem` sits where `image` sat —
      a byte diff then shows one key changed, not one removed and one appended
- [ ] Exit gate: `len(manifest_after) == len(published_item_ids())`, **both
      derived from the published collection's item links** (one producer). A
      sample can never make that statement
- [ ] `register_manifest.py audit-items --dir DIR --collection-id ID
      --require-asset dem --forbid-asset image --expect N` — pure function plus
      subcommand, so tests can reach it
- [ ] `ndjson_write(paths, out, expect_collection=None)` raises on a mismatch;
      thread from `item_register.sh`. Last checkpoint before pgstac
- [ ] `tests/test_item_migrate.py`: produces `dem` / removes `image`; never
      leaves both; idempotent (second call `[]`); preserves asset order; sets the
      item `collection` field; **does not touch asset hrefs**; leaves
      `links[rel=collection]` on the old bucket; malformed items do not raise;
      refuses a foreign manifest. The href fixtures must carry `/dem/` and a
      `stac-dem-bc.s3.amazonaws.com` link — **assert those premises inline**, or
      the fixture cannot reach the text-substitution failure it exists to catch
- [ ] `tests/test_register_manifest.py`: `_write_item(collection=...)` +
      `test_ndjson_write_refuses_an_item_from_another_collection`. Leave the
      bucket-URL fixtures at `:76,82,89` alone with a comment saying why
- [ ] `test_item_migrate_and_collection_patch_agree_on_the_id`

## Phase 5 — Wire the migration into CI

Runs in CI, not from a laptop: same OIDC credentials as every other publish, and
run logs as evidence. Mirrors the existing `backfill` dispatch precedent.

- [ ] `rename` boolean `workflow_dispatch` input + step, mutually exclusive with
      `backfill`
- [ ] **Clobber guard:** the `Rebuild items whose DSM pairing changed` step runs
      first into the same `$STAC_OUTPUT_DIR`; the migration must skip any id
      already present there, or it fetches the *published* body and overwrites a
      fresh rebuild
- [ ] Full item validation into `$RUNNER_TEMP/validation_rename.csv` —
      `--incremental` would validate nothing, since every id is already in the
      ledger
- [ ] `audit-items` gate **between validate and sync**, with `--expect 102460`.
      This is what catches a reused-manifest run that produced 4,420 files and
      exited 0 — before a byte reaches S3
- [ ] Same audit, sampled, on the **monthly** path after `item_create` (tens to
      thousands of items, so cheap). Makes the dangerous state permanently
      self-reporting
- [ ] Manifest + errors log in the `always()` cache commit and the artifact list

## Phase 6 — Execute the cutover

Target **Sep 1**. Do not dispatch Sep 2–4: a failure queued behind the cron is
the messiest state to read.

- [ ] Rehearsal, laptop, scratch only:
      `item_migrate.py --limit 20 --dry-run`, then `--limit 200 --verify 20`
- [ ] Dispatch `gh workflow run update.yml -f rename=true` (~55–65 min; the
      2026-08-29 backfill over 98,040 items took 47m01s)
- [ ] Verify S3: `collection.json` has the new `id`, the new `title` **in both
      places**, 102,460 item links, no `version`
- [ ] Register, tailnet (~45 min): `catalogue_register.sh --all --dryrun`, then
      `--all`. Collection first (FK), then items. **Add `audit-items` between the
      fetch and the register** — the script has already fetched all 102,460
      bodies into `$FETCH_DIR`, so the full-population check is free and aborts
      before anything reaches pgstac. This closes the gap that the script
      reconciles `STAC_COLLECTION` against `collection.json` but never against
      item bodies
- [ ] `catalogue_register.sh --verify` → `IN SYNC: 102460`, no orphans
- [ ] pgstac audit on the host — one row per collection, grouping
      `pgstac.items` by `collection` with counts filtered on whether
      `content->'assets'` holds `dem` / `image`. Expect
      `stac-elevation-bc | 102460 | 102460 | 0`. Check `\d pgstac.items` first —
      content may be dehydrated in this schema version; if so the `audit-items`
      run above is the authoritative version

## Phase 7 — Downstream, then drop the old collection

- [ ] `rtj/scripts/dem/_shared.R` — `:12` `COLLECTION <- "stac-dem-bc"` and
      `:95` `f$assets$image$href`. **The one live downstream code consumer**;
      separate PR in `rtj`, its own worktree
- [ ] `rtj/docs/stac-endpoints.md:12,28`; `fly/R/fly_footprint.R:342` + its
      `.Rd`; `stac_floodplains_bc/README.md:15` (all prose)
- [ ] `README.Rmd:80` (`collections =`) **and** `:104`
      (`pluck(..., "image", ...)`) in one pass, then regenerate
      `data/stac_result.rds` with `update_query=TRUE`. They fail differently —
      the `pluck` loudly, the `collections=` **silently** as an empty table — so
      only a real regeneration proves both
- [ ] `scripts/README.md:231`; a "superseded" header note on
      `stac_create_item.qmd` rather than editing its `image` literals
- [ ] **Gate:** the regenerated README query actually returned tiles and a
      download href resolves. Schema validation does not prove a consumer works
- [ ] `collection_unregister.sh stac-dem-bc` (reports, deletes nothing) — the
      count must read **102,460**; anything else means something is still
      mutating the old collection. Only then `--yes`

## Phase 8 — Release and spin-out

- [ ] `collection_patch.py --version 2.0.0`, upload the one file, re-register it
      (Phase 5's `--clear-version` leaves it unversioned in between)
- [ ] `NEWS.md` v2.0.0 — a major break; tag
- [ ] File the **repo rename** issue: `stac_dem_bc` → `stac_elevation_bc`, noting
      that `rtj/env/prod/main.tf:103` must be applied in the same window or the
      monthly workflow loses AWS auth (the role *ARN* is safe — `role_gha_*` is
      derived from the bucket, not the repo)
- [ ] `/planning-archive`

## Validation

- [ ] `pytest tests/ -q` green at every commit (it is a hard CI gate at
      `update.yml:81`)
- [ ] `/code-check` clean on each commit
- [ ] Every new guard tested against **both** answers — one input that must fire
      it, one that must not
- [ ] PWF checkboxes match landed work
- [ ] `/planning-archive` on completion
