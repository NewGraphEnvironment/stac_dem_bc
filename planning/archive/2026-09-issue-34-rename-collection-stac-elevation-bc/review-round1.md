# Review round 1 — #34 collection/asset rename

Reviewed at working-tree state on 2026-08-31 (branch
`34-rename-collection-to-stac-elevation-bc-a`, commits `97b311b..f9b199b` plus
the uncommitted edits to `update.yml`, `item_migrate.py`, `item_rewrite.py`,
`test_item_migrate.py`). The tree moved during the review — the `--expect
102460` audit gate, the manifest-discard-on-failed-sync logic, the zero-byte
manifest case and the `--limit 0` case were all fixed mid-read, so the findings
below are against the **current** files, not the committed diff.

Read in full: `scripts/item_rewrite.py`, `scripts/item_migrate.py`,
`scripts/item_backfill.py`, `scripts/register_manifest.py`,
`scripts/collection_patch.py`, `scripts/catalogue_register.sh`,
`scripts/item_register.sh`, `scripts/collection_register.sh`,
`scripts/collection_unregister.sh`, `scripts/stac_utils.py`,
`.github/workflows/update.yml`, `tests/test_item_migrate.py`,
`tests/test_asset_key.py`, `tests/test_collection_identity.py`.

---

## Findings

### 1. **[bug]** `scripts/item_migrate.py:225-236` — the `--verify` empty-sample gate makes a re-run of `rename=true` fail on a healthy, fully-migrated catalogue

```python
if args.verify:
    sample = [i for i in todo
              if os.path.exists(os.path.join(out_dir, f"{i}.json"))][: args.verify]
    ...
    if checked == 0:
        logger.error("VERIFY checked 0 items -- an empty sample proves nothing")
        return 1
```

`sample` is drawn from `todo` filtered by "a file was written". It is empty in
exactly two situations, and **both of them are success**:

- `todo` is empty because the manifest is complete (a finished migration), and
- every item in `todo` came back already correct, so `edit` returned `[]`,
  `process_one` returned `unchanged`, and nothing was written.

The workflow passes `--verify 40` unconditionally
(`.github/workflows/update.yml:251`), so two ordinary operator actions fail:

- **Re-dispatching `rename=true` after a successful migration.** `todo = 0` →
  `checked == 0` → exit 1. The input description at `update.yml:42` says
  *"Safe to re-run -- already-migrated items are detected and not re-uploaded"*.
  It is not; it fails.
- **A run whose S3 sync succeeded but whose cache commit did not.** `git pull
  --rebase` / `git push` at `update.yml:356-357` can fail on a concurrent push.
  The manifest is then never committed, S3 *is* migrated, and every subsequent
  dispatch fetches 102,460 already-correct items, writes nothing, and exits 1.

This is a guard failing toward abort in the "nothing to do" branch, which is the
routine branch after success. It also returns **before** the completeness
reconciliation, so the run never gets to print the statement that would have
said the catalogue is fine.

`item_backfill.py:209-214` has no equivalent gate — it logs `Verify passed on 0
items` and exits 0 — so the two callers of the same harness now disagree about
what an empty sample means.

Fix shape: only treat `checked == 0` as a failure when the run actually wrote
something (`counts["written"] > 0`), or run the completeness reconciliation
first and let that be the authority on whether the population is complete.

---

### 2. **[bug]** `.github/workflows/update.yml:221-245` — the rename/backfill mutual exclusion runs *after* the backfill has already written

Step order is `Backfill published items` (line 221, `if: inputs.backfill`) and
then `Migrate published items` (line 238, `if: inputs.rename`). The exclusion
check lives inside the *migrate* step's `run:`:

```yaml
- name: Migrate published items to the renamed collection (dispatch only)
  if: inputs.rename
  run: |
    if [ "${{ inputs.backfill }}" = "true" ]; then
      echo "::error::rename and backfill both set ..."
      exit 1
```

Dispatching with both set runs the **entire backfill first** — up to ~102k
fetches and writes into `$STAC_OUTPUT_DIR`, potentially tens of minutes of
runner time — and only then aborts. Both the step comment (`:235-237`) and the
`rename` input description (`:43-45`) state it "refuses to run alongside
backfill". It refuses to run alongside a backfill that has already run.

The check has to be either its own step placed before `Backfill`, or duplicated
into the backfill step's `run:`, or expressed as a job-level condition. Note
that expressing it only as `if: inputs.rename && !inputs.backfill` would be
worse — the migrate step would silently *skip* rather than fail.

---

### 3. **[fragile]** `scripts/item_migrate.py:249` — completeness now counts `staged` as migrated without checking any of them

```python
migrated = manifest_load(args.manifest, MIGRATION) | set(staged)
```

`staged` is defined by `item_rewrite.skip_already_staged` as *"a file with this
id already exists in `out_dir`"* — nothing more. Nothing in `item_migrate`
verifies those bodies carry the new collection id or the new asset key. The
comment asserts they were built by `item_create`, which is true in CI and is a
proxy, not a property.

Concrete false pass, entirely outside CI:

```bash
python scripts/item_backfill.py --out-dir D     # writes ~102k files into D
python scripts/item_migrate.py  --out-dir D     # todo == 0, staged == 102460
# -> "Complete: all 102460 published items are migrated"  exit 0
```

Every one of those staged files still carries `"collection": "stac-dem-bc"` and
`assets.image`. The reconciliation is described in the module docstring
(`:33-35`) as *"the reconciliation at the end of main() -- over the full
population"* and as the one statement that can see a mixed population; in this
sequence it blesses one.

In CI this is covered by `Audit staged items against the collection`
(`update.yml:300-313`, which passes `--require-asset dem --forbid-asset image`
on a rename run) — so the exposure is local runs and any future caller that
skips that step. Cheap fix: run `audit_items` over the staged paths before
folding them into `migrated`, or count an id as staged only when the on-disk
body already satisfies `item_migrate(body) == []`.

---

### 4. **[fragile]** `scripts/item_rewrite.py:174-195` — a deterministic `edit` failure is retried as though it were transient

```python
for attempt in range(attempts):
    try:
        item = item_fetch(item_id)
        changed = edit(item_id, item)
        ...
    except Exception as e:
        last = str(e)
        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))
```

`edit` is inside the retry. `item_migrate` deliberately raises `ValueError` on
an item carrying both `image` and `dem` (`item_migrate.py:97-102`) — a
deterministic condition that cannot improve on retry. Each such item costs 3
fetches plus 4.5 s of backoff on its worker. A catalogue left in that state by a
bad partial run turns a ~20-minute pass into hours on 16 workers and can exceed
`timeout-minutes: 330`, at which point the manifest is discarded and nothing has
been learned except which item failed first.

The retry exists for network failures (the comment says so). Keep it around
`item_fetch` and let an exception from `edit` fail the item immediately.

---

### 5. **[fragile]** `scripts/item_rewrite.py:145-151` — an explicitly-supplied collection path that does not exist silently becomes a network fetch

```python
if collection_path and os.path.exists(collection_path):
    ... open(collection_path)
else:
    logger.info("Fetching published collection from %s", COLLECTION_URL)
```

CI passes `--collection "$STAC_OUTPUT_DIR/collection.json"`
(`update.yml:247`). If that file is missing for any reason — a wrong
`STAC_OUTPUT_DIR`, a fetch step that was skipped by an `if:` nobody re-checked —
the run does not fail. It fetches the **published, unpatched** collection over
the network and proceeds against a different artifact from the one every other
step in the job reads. Caller-supplied path absent is an error; only `None`
should mean "fetch it".

---

### 6. **[fragile]** `.github/workflows/update.yml:164-176` vs `:198-212` — the "standing guard" never sees rebuilt items, and does not run at all on a pairing-only month

The step comment calls itself *"The standing guard, and the reason #34 cannot
silently recur"*. Its actual scope is narrower than that claim in two ways:

- It is placed **before** `Rebuild items whose DSM pairing changed` (line 198),
  by design (`:178-181` explains the count must be taken before the rebuild).
  So items rebuilt by `item_create.py --urls-file
  data/urls_pairing_changed.txt` are published without ever being audited.
- It is gated on `steps.detect.outputs.new_urls == 'true'`. A pairing-only month
  is `changes == 'true'`, `new_urls == 'false'`: `Fetch current collection`,
  `Patch collection metadata`, `Create STAC items` and the audit are **all**
  skipped, the rebuild step fetches and patches the collection itself
  (`:203-208`), and `Count items to publish` → `Validate` → `Sync` then publish
  those items with no homogeneity check anywhere in the job.

Both close by moving the audit after the rebuild and gating it on
`steps.publish.outputs.count` instead of `new_urls`. The shortfall count at
`:182-192` must stay where it is (its comment is correct about why), but it is a
different statement and does not need to share the audit's position.

---

### 7. **[fragile]** `.github/workflows/update.yml:176` and `:311` — the asset key is spelled literally in the workflow, outside every guard that exists to prevent that

```yaml
--require-asset dem --forbid-asset image
```

in two places. These duplicate `stac_utils.ASSET_DEM` and
`item_migrate.ASSET_RENAMES`. `tests/test_asset_key.py::_script_files` scans
only `scripts/*.py`; `tests/test_collection_identity.py::_source_files` does
include the workflows but only searches them for the collection-id strings. So
neither guard covers these two lines, and this is the one file the identity test
went out of its way to bring into scope (`test_collection_identity.py:53-58`
argues exactly that `scripts/` is not the boundary).

The `rename` input description at `:39-41` states the target names are *"named
here rather than spelled out, so this description cannot go stale"* — true of
the description, and not of the two `run:` blocks below it.

Fails loudly rather than silently (a changed `ASSET_DEM` makes `--require-asset
dem` reject every item), so this is low severity — but it is the same "one fact,
two definitions" the whole change is built to remove, and extending the asset
scanner to the workflows is a few lines.

---

### 8. **[fragile]** `.github/workflows/update.yml:350-351` — stderr suppressed on the mutating manifest discard

```bash
git reset -q -- "$f" 2>/dev/null || true
git checkout -q -- "$f" 2>/dev/null || true
```

`git checkout --` destroys working-tree content; `-q` already silences success,
so the redirect can only ever hide a failure, and `|| true` then continues. The
consequence is contained — `git reset` runs first, so a failed `checkout` still
leaves the file unstaged and uncommitted — but if the ordering is ever changed
the discard becomes a silent no-op and finding 1's stranding failure returns
with no diagnostic. The reset is also expected to "fail" harmlessly on the first
run, when `data/migrate_done.txt` is not in HEAD; branching on that explicitly
would let the real failures speak.

---

### 9. **[fragile]** `scripts/item_migrate.py:252-259` — the completeness check aborts on `extra`, which an upstream deletion produces

```python
if migrated != published:
    missing = published - migrated
    extra   = migrated - published
    ... return 1
```

`extra` is non-empty whenever the manifest holds an id that has since left
`collection.json` — which is exactly what an upstream deletion does, and
upstream-deletion pruning is open (#28). A resumed migration across a month in
which items were deleted then fails with "not in the published set" although
every published item is migrated. `missing` is the direction the check is for;
`extra` is worth logging and is not worth failing on.

---

## Checked and sound

Recording these because several were specifically asked about, and "we looked"
is worth as much as a finding here.

- **`item_migrate()` asset handling.** `list(renamed) != list(assets)` is a
  sound rename detector in both directions: the comprehension only ever changes
  keys through `renames`, so the key lists differ *iff* at least one old key was
  present, and they cannot be equal after a real rename. `changed` is computed
  against the original `assets` object (not the replacement), so it never
  reports a rename that did not happen — which is what keeps an unchanged object
  off S3. Asset bodies, key order, both hrefs (including the literal `/dem/`
  segment) and `links[rel=collection]` on the `stac-dem-bc` bucket are all
  preserved; `assets` missing or non-dict cannot raise.
  - *Latent, not reachable today:* with a multi-entry `asset_renames` where two
    old keys map to one new key, `renamed` silently drops one asset — the
    both-keys guard only checks each `old`/`new` pair against its own target,
    not for collisions among the new keys. `ASSET_RENAMES` has one entry.
- **`manifest_load` / `manifest_open`.** Verified against: missing file (empty
  set), zero-byte (empty set, after this session's fix — and `manifest_open`
  agrees, treating it as fresh), header-only (empty set), header line with no
  trailing newline (accepted), wrong owner (raises and names both migrations),
  no header (raises). A `--manifest data/backfill_done.txt` handed to
  `item_migrate` raises rather than skipping 98,040 ids. Concurrent append is
  safe — every write is under `run_rewrite`'s lock and flushed. The
  post-run `manifest_load` reflects what was written: `manifest_fh.close()` is
  in the `finally`, before the reconciliation reads the file back.
- **`search_body` / `ids_serving` / `verify-serving`.** Every call site passes
  `collection_id`: `ids_serving` (`register_manifest.py:211`), the
  `verify-serving` CLI (`:467`), and `catalogue_register.sh:369-370`. No caller
  is left on the old signature. `limit` is derived from the batch and floored at
  1, and the empty-`collection_id` refusal is asserted for `""` and `None`.
- **`item_register.sh` `NDJSON_ARGS` under `set -u` on bash 3.2.** The array is
  initialised with two elements and only ever grows, so `"${NDJSON_ARGS[@]}"`
  can never be an empty-array expansion; `+=` on arrays is bash 3.1+. Safe.
  `WRITTEN=$(...)` propagates the Python exit status under `set -e`, so an
  `--expect-collection` rejection aborts before the count guard is reached.
- **`catalogue_register.sh`.** `count_lines` behaves correctly on all four
  inputs (empty → 0 via the `||` branch, unterminated → counted, terminated →
  counted, missing → 0), and the missing-file case is explicitly re-guarded
  where it would disarm the orphan check (`:185-188`). `audit-items --expect
  "$N_TODO"` is reconciled to `$FETCH_DIR` through `N_URLS`/`N_FETCHED`
  (`:231-244`, `:315-328`), so both sides of that comparison trace back to
  `urls.txt` — the artifact the fetch loop iterates. The audit runs after
  `collection_register.sh` and before `item_register.sh` (correct FK order,
  correct catch point), and does not run under `--dryrun` because `--dryrun`
  exits at `:246-251` before the fetch and registers nothing.
  A `--drift` run against a half-migrated S3 will fetch stale bodies and fail
  the audit rather than upserting them into the old collection — the intended
  catch, and it works.
- **`collection_patch.py` item-link invariant.** Compares the full ordered href
  list against `[encode_url_for_gdal(h) for h in before]`, with a separate
  length-change branch. A lost, added, reordered or mutated item link cannot
  pass; only `links_encode`'s space encoding can. `item_links_before` is a list
  of immutable strings captured before the in-place patch, so it is a real
  snapshot. Memory at 102,460 links: the parsed collection plus a full second
  parse of the temp file — a few hundred MB peak on a 7/16 GB runner, and the
  job's high-water mark, but not a failure. (`json.load(open(tmp))` at `:281`
  leaks the handle until GC; cosmetic.)
- **`audit_items` / the `audit-items` CLI.** Empty path list returns 1 with an
  explicit message rather than a vacuous pass; `--dir` on a missing directory
  raises out of `os.listdir`; unreadable files are reported *and* excluded from
  `checked`, so `--expect` fires alongside the unreadable report rather than
  masking it; `collection.json` is excluded from the listing and matches what
  the sibling `find` counts.
- **`--dry-run`** in `item_migrate` genuinely previews: it returns before
  `manifest_open`, writes no item files, and `edit` mutates only a local dict.
  (It does `os.makedirs(out_dir)` first — harmless.)
- **`item_create` / `item_reprocess` / `collection_create`** all read
  `ASSET_DEM` / `ASSET_DSM` / `COLLECTION_ID` rather than literals, and the
  `test_asset_key.py` AST scanner is validated against a positive control in all
  three positions it checks.

## Notes (not defects)

- `planning/active/task_plan.md:179` still records the audit gate as
  `--expect 102460`; the workflow no longer passes `--expect` at all. `:176`
  says `validation_rename.csv`; the workflow writes `validation_rewrite.csv`.
- Dropping the old `stac-dem-bc` rows from pgstac is Phase 7 in the task plan
  and `collection_unregister.sh` exists for it, so the two collections coexisting
  on the endpoint after the cutover is scoped, not missed. Worth noting that
  until that happens `catalogue_register.sh --verify` reports `IN SYNC` with
  102,460 rows still served under the old id, because both the diff and the
  orphan direction are scoped to one collection.
