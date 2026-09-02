# Findings — Rename collection to stac-elevation-bc, asset image to dem (#34)

## Verified facts this plan rests on

Each probed directly on 2026-08-30/31, not inferred from reading.

### The manifest reuse trap

`data/backfill_done.txt` holds **98,040** ids against **102,460** published
(`wc -l`, and the live `collection.json` item-link count). `item_backfill.py`
computes `todo = published - done`, so reusing that manifest for the migration
would rewrite **4,420 items, skip 98,040, and exit 0** — producing exactly the
mixed population this change exists to avoid. The migration needs its own
manifest, and the manifest loader must refuse one belonging to another migration.

### `item_backfill.verify()` cannot be reused as-is

`scripts/item_backfill.py:200`:

```python
removed = diff.get("dictionary_item_removed", [])
if (added - allowed) or bad_values or removed:
```

There is no allowlist for removals at all, and a rename removes
`assets['image']`. So `verify()` would fail **100%** of a rename sample, not
merely need a wider allowlist. Replaced by re-derivation (`verify_rewrite`)
rather than a widened allowlist — an allowlist that grows a branch per migration
is the exemption-list failure in `CLAUDE.md`.

### `search_body` is unscoped — a real blocker, found while planning

`scripts/register_manifest.py:163`:

```python
def search_body(ids) -> dict:
    return {"ids": ids, "limit": max(len(ids), 1), "fields": {"include": ["id"]}}
```

No `collections` filter. `verify-serving` — the last step of
`catalogue_register.sh` — therefore asks "is this id served *anywhere*". Harmless
today with one collection. **#34 creates two by design**, and every id is already
served by `stac-dem-bc`, so `catalogue_register.sh --all` against
`stac-elevation-bc` would verify **green with zero items registered**. Fixed in
Phase 0, before the cutover can rely on it.

Note `ids_registered()` (used by `--verify` / `--drift`) *does* scope correctly —
only `verify-serving` is affected.

### The collection id is spelled four times

Confirmed in the published `collection.json`:

| where | value |
|---|---|
| `collection["id"]` | `stac-dem-bc` |
| `collection["title"]` | `Digital Elevation Models from British Columbia - stac-dem-bc` |
| `links[rel=root]["title"]` | same string again |
| `collection_create.py:109` | derives a fourth from the literal at `:54` |

A patch that sets only `id` and `title` leaves the root link contradicting them.

### The asset key has four writers, not three

| file:line | path |
|---|---|
| `stac_utils.py:358` | `item.add_asset("image", ...)` in `item_create_from_cache` — **the dominant, cache-hit path** |
| `item_create.py:148` | `asset_name='image'` to `rio_stac` — cache-miss fallback |
| `item_create.py:155` | `item.assets['image'].href = ...` |
| `item_reprocess.py:103,110` | the same pair again |

`stac_utils.py:356` also writes `item.collection_id`. And `item_backfill.py:120`
*reads* `assets["image"]` to inherit media type onto a new `dsm` — missing it
silently downgrades every DSM from COG to plain tiff.

### The item body carries the collection id

A live item JSON's top-level keys: `assets, bbox, collection, geometry, id,
links, properties, stac_extensions, stac_version, type`. Exactly two fields
change per item — `"collection"` and the asset key. Its single link is
`rel: collection -> https://stac-dem-bc.s3.amazonaws.com/collection.json`, which
is **unchanged**, because the bucket keeps its name.

`item_register.sh` routes each item by its **own** `collection` field, so a stale
body upserts back into the old collection *successfully*, with no error anywhere.

### The workflow is active

`gh workflow list` → `Monthly incremental update  active`. Last scheduled run
2026-08-03, success. So the Sep 3 cron **will** fire.

### Live state at planning time

- API `https://images.a11s.one` serves `stac-dem-bc` v1.1.0 alongside
  `stac-airphoto-bc`, `stac-floodplains-bc`, `imagery-uav-bc-prod`. No
  `stac-elevation-bc` exists — no name conflict.
- `s3://stac-dem-bc` holds 102,469 objects. Local AWS creds
  (`user/airvine`) reach it.
- `data/urls_pairing_changed.txt` is empty.

## The asymmetry that sets the schedule

`item_create.py` reads the **collection id from data** (`collection.id` off the
fetched `collection.json` — self-healing) but the **asset key from code** (not
self-healing).

So from the moment the asset-key change merges, any monthly run publishes new
items keyed `dem` into a catalogue of 102,460 keyed `image`. **Merging the code
without running the migration is the dangerous state**, and no choice about how
the collection id is handled avoids it — which is what settles the sequencing
question: merge and dispatch the same day.

## Why nothing that exists today catches a half-done rename

Item ids are unchanged **by design**, so set equality reports `IN SYNC` over a
fully mixed catalogue. Every other check is blind for its own reason:

| check | why blind |
|---|---|
| any count | 102,460 either way |
| `catalogue_register.sh` set equality | ids unchanged by design |
| its id-mismatch guard (`:97-105`) | one field in one file, of 102,461 files |
| `item_register.sh` | routes by each item's own `collection` field — a stale body registers **successfully** |
| `item_validate.py` | `image` and `dem` are both legal STAC |
| `item_backfill.verify()` | ~40 of 102,460, and only file-vs-published |
| `catalogue_qa.py:72` | compares `len(assets)` — `{image,dsm}` and `{dem,dsm}` both count 2 |

The property needed is **population homogeneity**, not population size:

- every item body's `collection` equals the collection's own `id`
- every item body's assets contain `dem` and not `image`

Added as `register_manifest.py audit-items`. Its highest-value placement is
inside `catalogue_register.sh --all`, between the fetch and the register: the
script has *already* fetched all 102,460 bodies into `$FETCH_DIR`, so the
full-population check is free and aborts before anything reaches pgstac.

## Downstream consumers — outside the repo boundary

An inventory is only complete relative to a boundary. Swept `~/Projects/repo`:

| repo | site | kind |
|---|---|---|
| `rtj` | `scripts/dem/_shared.R:12` `COLLECTION <- "stac-dem-bc"` | **live code** |
| `rtj` | `scripts/dem/_shared.R:95` `f$assets$image$href` | **live code** — breaks on *both* renames |
| `rtj` | `docs/stac-endpoints.md:12,28` | docs |
| `fly` | `R/fly_footprint.R:342` + `man/fly_footprint.Rd:182` | roxygen prose |
| `stac_floodplains_bc` | `README.md:15` | prose |

`rtj/scripts/dem/_shared.R` is the only live downstream code consumer, and it
reads both the collection id and the asset key.

## Repo rename — why it is a separate issue

`rtj/modules/gha_s3_role/main.tf:3`:

```
subs = [for b in var.branches : "repo:${var.repo}:ref:refs/heads/${b}"]
```

fed by `rtj/env/prod/main.tf:103` `repo = "NewGraphEnvironment/stac_dem_bc"`.
Renaming the GitHub repo breaks the OIDC trust condition, so the monthly workflow
cannot assume its role until `rtj` is applied.

The role *name* is safe: `modules/gha_s3_role/main.tf:2` derives it from the
**bucket** (`role_gha_${replace(var.bucket, "-", "_")}`), so
`update.yml:85`'s ARN is unaffected by a repo rename. It *would* be affected by a
bucket rename, which is a third and separate decision.

Nothing a data consumer sees changes on a repo rename, and GitHub redirects old
URLs — so it has a different blast radius and no ordering dependency on #34.

## Defects found while building this, and what they generalize to

### A progress manifest is a claim about a step that may not have run

**The most serious defect on this branch was in this branch's own machinery, and
it was the exact failure the branch exists to prevent.**

`run_rewrite` appends an id to the manifest when the LOCAL WRITE succeeds. The CI
cache commit is `always()`, deliberately — a run once completed 98,040 items,
exited non-zero on 2 transient failures, and the lost manifest would have
restarted it from zero. But the sync step is skipped on failure. So a run failing
anywhere between the migration and the publish committed a manifest asserting
items were done that never reached S3, and because `todo = published - manifest`,
every later run skipped them. Permanently. Silently. With the completeness check
and the audit both passing while S3 stayed mixed.

**Generalizes to:** a resumability ledger records the step that wrote it, not the
step the reader cares about. Before persisting one, ask *what does an entry in
this file claim, and did the thing it claims actually happen?* Where the answer
depends on a later step, the ledger must be gated on that step, not on the one
that produced it. Sibling of "a cache written before the work succeeds strands
its inputs permanently" in `code-check.md`, one level meaner: there the cache is
written too early within one process; here it is written at the right moment and
persisted by a *different* mechanism whose condition is wrong.

### A guard suppressed on exactly the runs that need it

`item_migrate`'s completeness reconciliation was skipped whenever anything had
been staged by an earlier step. That reads as caution. It is the CI shape: the
pairing rebuild writes into the same directory, so a real cutover in a month with
pairing changes would have asserted nothing at all. Staged ids now *count* as
migrated, which they are — `item_create` built them with the current id and key.

**Generalizes to:** when a guard has a "cannot assert this here" branch, work out
which real runs take it. If the answer is "the ones that matter", the branch is
the bug.

### One fact derived twice — inside the step written to catch that class

The CI audit compared the staged file count against the *published* total. That
is correct only on a first complete run; on any resumed run the staged set is
legitimately the remainder. Homogeneity and completeness are now one statement
each, with one owner each.

### A guard that fires correctly and then misdirects

The collection-id mismatch guard fires between merge and cutover — correctly, and
by design. Its message said to point `STAC_BUCKET_URL` at "the bucket for
stac-elevation-bc", which is the bucket the operator already has, because the
bucket keeps its old name. The obvious way to make it pass would have registered
the old catalogue under a name the code no longer uses.

**Generalizes to:** a diagnostic is part of the guard. Check what someone would
*do* on reading it, not just that the guard fired.

### Smaller, same session

| defect | why it mattered |
|---|---|
| `if args.limit:` read `--limit 0` as "no limit" | a rehearsal flag would have run all 102,460 items |
| non-atomic item write | a truncated JSON syncs to S3, and satisfies both resumability checks so it is never revisited |
| zero-byte manifest refused | `touch` would lock the tool out, protecting against nothing |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| `test_search_body_*` failed after adding a required arg | every call site in tests updated; the parameter is required rather than defaulted, because a default is what would have gone unnoticed |
| identity scan flagged `item_migrate.py`'s docstring | strip docstrings via the AST (prose the interpreter discards), not by text; every other string stays in scope |
| identity scan flagged `update.yml`'s dispatch description | reworded to name the constants — a hardcoded name in a UI string goes stale anyway |
| `--limit 0` test appeared to show a completeness bug | the test was wrong, not the code; but it exposed that `--limit 0` was read as "no limit" |
