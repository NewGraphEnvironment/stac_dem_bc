# Code-check round 3 (#45)

Reviewer: subagent, 2026-09-29. Scope: `git diff --cached` (catalogue_register.sh,
register_manifest.py, both test files, NEWS.md, CLAUDE.md), plus item_register.sh
and collection_register.sh. All mutation/probing done in `/tmp/cc45_r3`; nothing in
the repo was touched except this file.

## Findings

- **[severity: bug]** scripts/catalogue_register.sh:500-523 (fetched-paths → audit-items → item_register)
  with scripts/register_manifest.py:447-450 — **a fetched body whose `id` differs
  from its link's id is only refused AFTER the write under `--all` and `--ids-file`.**
  The check lives only in `published_digests`, and in those two modes
  `published_digests` first runs post-write (the FULL_RECHECK `diff` for `--all`,
  `verify-serving` for `--ids-file`). Nothing pre-write compares body id with link
  id: `fetch_bodies` only requires a JSON object, `fetched-paths` only checks the
  file exists, `audit-items` checks collection and assets, and `ndjson_write`
  checks collection. `item_register.sh` then upserts by the body's own `id`.
  Reproduced in the tmp copy (stub API, `SSH_STUB_OK=1`, link `a1.json` serving a
  body that names `a0`):
    - `--ids-file` (ids: `a1`): loads `[collections 1, items 1]` — i.e. **overwrites
      the registered `a0` with a1's content** — then tracebacks in verify-serving
      (`ValueError: link for 'a1' fetched a body that names id 'a0'`). `a1` itself
      was never updated.
    - `--all`: loads `[collections 1, items 3]` (two NDJSON rows for `a0`), then
      tracebacks in the post-write `diff`.
    - `--drift`: refused before any write (the pre-write `diff` runs
      `published_digests` over every link). Correct.
  This is the mechanism named in the brief as (b), and the same shape round 2 fixed
  for duplicate links: a guard that exists, but reads its artifact only after the
  consumer (the loader) has already read it. Enumerating every raise in
  `published_digests` against what runs pre-write in `--all`/`--ids-file`:
  not-fetched (covered by N_FETCHED + fetched-paths), not-JSON (covered by
  fetch_bodies + audit), duplicate link (covered by N_HREF_ROWS), **id mismatch
  (not covered)**. That is the only uncovered one. Fix direction: make the pre-write
  step that already maps id → path (`fetched-paths`) parse the body and require
  `doc["id"] == item_id`, so it holds in every mode before `collection_register.sh`.
  (Pre-existing for the write itself — before #45 the same body loaded and
  verify-serving passed silently — but #45 now reports a successful write as a
  crash on every rerun, which is what round 2 treated as a defect.)

## Checked and clean

Mechanism (a) — "published and served are the same object modulo X":
- Every comparison site goes through `body_digest` → `_canonical`:
  `bodies_registered`, `bodies_serving`, `published_digests`, `collection_state`.
  No site compares raw bodies.
- Live collection probe (one GET + one S3 fetch): `body_digest(collection.json) ==
  body_digest(/collections/stac-elevation-bc)` → True; same key set. The live
  collection has **no `item_assets`**, so hydration base is only
  type/stac_version/collection today.
- The loader's own re-serialisation (`ndjson_write` json.load/json.dumps) is a
  further X between "fetched" and "sent", but it is lossless modulo float parse,
  which the digest also performs, so verify-serving is unaffected.
- Canonicalisation is symmetric and only collapses value-preserving differences
  (null member ↔ absent, integral float ↔ int); a published null replacing a real
  value, or a fractional change, still differs (pinned by the new tests).
- pgstac `update_collection_extent`-style recomputation would be an X for the
  collection, but the served extent currently equals the published one after
  102k item loads, so it is not active.

Mechanism (b) — count guards vs consumed artifacts:
- N_URLS and N_HREF_ROWS vs N_FETCH_IDS: both derived from hrefs.tsv, which is
  what urls.txt and fetched-paths consume.
- N_FETCHED counts `*.json` in a fresh mktemp FETCH_DIR; `.json.part` does not
  match; files are md5(href) of the same strings the diff and fetched-paths hash.
- audit `--expect N_TODO` vs todo_paths.txt (one line per todo id, or a hard exit).
- item_register EXPECTED vs WRITTEN from the same PATHS file; remote line count.
- FULL_RECHECK `diff` reads collection.json links + FETCH_DIR; in both modes that
  trigger it (`--all`, `--drift`) FETCH_DIR holds every published body.
- `sort -u` without `LC_ALL=C` in `--all`/`--ids-file` (the drift branch's own
  comment warns about collation merges): tested on all 102,460 live ids under GNU
  sort en_US.UTF-8 — no merge. Not reported.

Shell: missing-list guards, `|| VAR=""` + case on stdout answers, empty-array
`+` form, stdin-not-argv for 102k paths, ssh probe placement — all fine.
