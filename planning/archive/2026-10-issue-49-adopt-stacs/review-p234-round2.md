# Review: phases 2-4, round 2 (#49)

Scope: staged diff (`git diff --cached`), with the five round-1 fixes read first and checked
against stacs v0.1.0 (`git -C ~/Projects/repo/stacs show v0.1.0:src/stacs/{cli,register,catalogue,validate,verify}.py`).

## Findings

- **[low]** NEWS.md (Unreleased, `ids-from-urls` bullet) and scripts/register_manifest.py
  (`item_ids_from_urls` docstring): "every URL list in `data/` stores the scheme as
  `https:/`" is false. `data/urls_invalid_items.txt` (2,245 lines, written by
  `scripts/item_extract_invalid.py`, read by `stac_create_item.qmd`) uses `https://` on
  every line. `urls_list.txt` (102,416), `urls_dsm.txt` (95,889), `urls_deleted.txt` (43)
  and the last committed `urls_new.txt` do all use one slash, so the fix is correct and
  `fix_url` is a no-op on the two-slash file. Only the sentence is wrong. findings.md gets
  it right because it names the two files. Suggest "the URL lists in `data/`
  (`urls_list`, `urls_dsm`, `urls_new`, `urls_deleted`)", or "the lists change detection
  writes".

- **[low]** planning/active/task_plan.md:44: "(no `--dryrun`, which returns before the
  API/ssh probe ...)". For `--mode drift`, `--dryrun` does not return before the probe.
  It skips the probe (`if writes and not dryrun:` in `_run`), then fetches the whole
  catalogue, pages every registered body from the API, and returns only in the drift
  dryrun branch after the comparison. The conclusion (a dryrun never exercises the probe)
  still holds. NEWS.md states this correctly ("probes neither, and still fetches the
  whole catalogue"). The plan's wording is the only wrong one. "Returns before the probe"
  is true only for `--mode all|ids --dryrun`.

## Round-1 fixes: checked and sound

1. index.html / README.html: the patched sentence matches README.md/README.Rmd. None of
   the four files mentions a deleted script any more.
2. CLAUDE.md:54-57 and scripts/README.md:248 match register.py. `audit_items` runs on
   `todo_paths` only, after the diff and before `load(... "collections" ...)`. A `verify`
   returns before that point, and so does a drift with an empty `todo` and collection
   `same` ("nothing to register"). Neither audits what is already registered.
3. NEWS.md Unreleased, each claim checked against v0.1.0:
   - Drift probes the API and ssh before its fetch: `_api_probe` + `probe` sit under
     `if writes and not dryrun`, before `fetch_bodies`. True.
   - Drift with `--dryrun` probes neither and still fetches everything. True; the dryrun
     return in the drift branch comes after the fetch and the compare.
   - Item linked twice: `collection_item_links` raises on a repeated id, which becomes
     `RegisterError` before any write.
   - A body that names another id: `published_digests` raises this, over the whole fetch
     in drift and over `todo_links` in all/ids, before `load`.
   - Audit failure: refused before the collection upsert, unlike v2.0.0, where the
     collection row was upserted first.
   - Served bodies checked against the bodies sent: `bodies_serving` + `content_diff`
     against `sent`. After `all`, or a drift whose collection changed, the whole
     catalogue is re-compared.
   - Parity "same sets, same verdict on every item": matches the stacs archive README
     (byte-identical lists for both collections; 0 disagreements in 112,560 items).
   - 160 / 29 / 102,460 / 10,100: the 2026-09-29 measurement, presented as the first
     full run's finding. Fine. The stacs archive notes that the 29 integral-float items
     no longer differ, which does not contradict the NEWS wording.
   - "Before, rules applied only on monthly and rename runs": confirmed against the
     v2.0.0 update.yml.
   - "at v2.0.0 ... verified IN SYNC": #45 merged after the v2.0.0 tag. True.
4. The `--mode ids` caveat (scripts/README.md, register_manifest.py docstring) matches
   `_run`'s "have no item link in the published collection" refusal.
5. task_plan.md is correct apart from the line above.

## General re-check

- No file outside planning/, logs and the accepted research continuation names
  `catalogue_register.sh`, `item_register.sh`, `collection_register.sh`, `audit-items`,
  `_canonical`, `STAC_REQUIRE_ASSET` / `STAC_FORBID_ASSET` or `same-bucket`. Two exceptions,
  both fine: CLAUDE.md:54 and CLAUDE.md:60 are explicitly historical, and
  tests/test_asset_key.py:220 is a scanner fixture string.
- No `.py`, `.R`, `.qmd`, `.sh` or `.yml` imports a removed function. Every `scripts/*.py`
  imports cleanly.
- Every documented command parses against v0.1.0 cli.py:
  - `verify --config --out-dir`
  - `register --config --mode drift|all|ids [--ids-file] [--dryrun]`
  - `audit --config --dir --expect`
  - flags-only `verify --api --collection-id --bucket-url` (with no config, `read_config`
    returns {} and verify needs no transport)
- `.venv/bin/stacs` exists in CI because the install step uses `[project.scripts]`.
  Locally it reports 0.1.0.
- `.venv/bin/python -m pytest tests -q`: 199 passed.

Clean apart from the two low findings above.
