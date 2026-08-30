# Task: Client-side pgstac registration + STAC Version Extension (#27)

## Problem

Catalogue updates end at S3. Loading them into pgstac is a manual step on another
machine, documented in five places as "someone does this by hand" — so it gets
forgotten. It was forgotten for a month: the API served **60,126** items against
**98,040** published, discovered while working #31.

The path that exists today is rtj's `stac_register-pypgstac.sh`, and it is worse
than manual — it **DELETEs the collection before loading it**. On 2026-08-29 an
ARG_MAX failure between those two steps left `images.a11s.one` serving **zero
items** until repaired by hand. That fix is still on an unmerged branch (rtj PR
#238), so a clone of rtj's `main` reproduces the outage today.

Outcome: one command, from any tailnet machine, that upserts a month's new items
or the whole catalogue into pgstac, never deleting, and verifies what it did.

## Correction to the issue's premise (measured during planning)

The issue says this blocks #34 because "renaming forces a full re-registration…
which means taking the API down deliberately." **It does not.**
`stac-elevation-bc` is a *new* collection id — loading it leaves `stac-dem-bc`
serving untouched until dropped deliberately. The rename is already zero-downtime
under any tool.

The real value is the other two: the monthly lag stops depending on memory, and
re-registering the *same* id stops being an outage. #34 is made tidier by this
(it gets `collection_unregister.sh`), not unblocked. **Issue body to be edited.**

## Measured facts this rests on

| fact | evidence |
|---|---|
| API is read-only | `/conformance` advertises no transaction class; `/aggregate` 404s |
| Host is `geopro` | tailnet `100.94.58.22`, eth0 `146.190.168.112`; DO **reserved** IP `146.190.12.8` is what `images.a11s.one` resolves to — same machine, second name |
| Root-only SSH | by design; cloud-init has no `users:` block (rtj#193) |
| DB is `stac` | Caddy `images.a11s.one` → `stac-api` → `PGDATABASE: stac` |
| pypgstac 0.9.9 | `cd /opt/geoserv/scripts && uv run pypgstac`, `. /opt/geoserv/.env` |
| `--method` defaults to `insert` | ran `--help` on the host: `Default: <Methods.insert: 'insert'>` — upsert is never the default |
| Items public, **flat keys** | `https://stac-dem-bc.s3.amazonaws.com/<id>.json` → 200; `/stac/<id>.json` → 403 |
| Collection has no version | live: `stac_extensions` absent, `version` absent |
| No CI path to the host | zero Tailscale actions / SSH deploy keys / `secrets.*` across every repo's `.github/` |
| Id enumeration works | `POST /search` + `fields:{include:["id"]}` + `rel:next` — 102,460 ids in 11 requests, 212 s |
| **A missing id is silently absent** | requested 2 ids (1 bogus), got 1, no error — never assert on returned *count* |

## Review findings folded in (Plan agent, pre-baseline)

- **B1 — collection must load BEFORE items.** `pgstac.items.collection` is
  `NOT NULL REFERENCES collections(id) ON DELETE CASCADE`. The reference
  implementation does items-first and only works because the collection
  pre-exists. That CASCADE is also the *mechanism* behind rtj's outage.
  Note the deliberate inversion: `s3_sync-ci.sh` is items-first by design.
- **B2 — stdin is spoken for.** `python3 - "$@" <<'PYEOF'` consumes stdin for the
  heredoc, and `ssh ... < "$TMP"` needs it for the payload. The assembler must be
  a real Python **file**, so Phase 1 lands first as a correctness constraint.
- **B3 — a truncated transfer loads clean and exits 0.** Remote `cat` sees EOF,
  pypgstac loads valid-but-short NDJSON and succeeds. A *local* count guard cannot
  see this. The guard must run **on the host**, against an expected count passed in.
- **B4 — `version` is not an item check.** It would have been green throughout the
  38k drift. Version never gates items.
- **G1 — `--new` from `urls_new.txt` is the wrong set** (misses pairing rebuilds
  and backfills; over-counts because CREATED < NEW is normal). Use a **drift**
  mode: enumerate registered ids, diff against published, register the difference.
  Self-healing and stateless — it would have caught the 38k gap the day it opened.
- **A6 — the repo's flag convention is `--dryrun`**, not `--dry-run`.

## Phase 1: `register_manifest.py` — the pure core (lands first, per B2)

- [x] `item_ids_from_urls()` reusing `stac_utils.url_to_item_id`; raises on
      unparseable input rather than warning
- [x] `collection_item_links()` — item-link hrefs → ids from the published
      `collection.json`
- [x] `ndjson_write()` — compact one JSON per line, no embedded newlines
- [x] `ids_diff()` — missing / orphaned, reported in **both** directions
- [x] `tests/test_register_manifest.py` against the existing real-listing fixtures

## Phase 2: `collection_register.sh` then `item_register.sh`

- [x] `collection_register.sh` first (B1 — the FK ordering)
- [x] `item_register.sh` reads item paths on stdin, never argv (102k filenames is
      ~6 MB against a ~2 MB ARG_MAX)
- [x] `HOST="${STAC_HOST:-root@geopro}"`, `DB="${STAC_DB:-stac}"`; header records
      the reserved IP `146.190.12.8` as fallback (geopro is untagged on the
      tailnet, so it carries a 180-day key expiry — rtj#208)
- [x] Remote `mktemp` + remote trap; the reference uses a fixed path whose `rm` is
      skipped on failure
- [x] **Remote** count guard against an expected count (B3)
- [x] `--method upsert` explicitly, every time; no delete path in this repo
- [x] `--dryrun` stops before the SSH; preflight `ssh -o ConnectTimeout` probe
      before any expensive stage (A1)
- [x] Empty input exits 0 with "nothing to register" — never hands pypgstac an
      empty file, never passes a vacuous `0 -eq 0`

## Phase 3: `catalogue_register.sh` — the orchestrator

- [x] `--all` (every published item link) and `--drift` (register only what the
      API is missing) share one code path, so the 3-item case exercises the 102k code
- [x] `--ids-file <f>` for explicit lists
- [x] Parallel S3 fetch, each worker to its **own** file, then
      `find -exec cat {} +` — the matched trap pair that has bitten rtj twice
- [x] `--max-time` on every curl + a periodic progress line (G5)
- [x] Per-file retry before any error reaches the exit code (G6; the backfill
      precedent — 98,040 successes nearly discarded over 2 transient failures)
- [x] Verify by **set equality**, both directions, never a returned-count

## Phase 4: `collection_unregister.sh`

- [x] pgstac SQL delete of a collection + its items, for #34's cutover
- [x] Guarded: id must match an allowlist pattern, explicit `--yes`, prints the
      item count it is about to destroy first
- [x] rtj's `stac_unregister.sh` is dead code (405s) — this must not reach for it

## Phase 5: STAC Version Extension

- [ ] Opt-in `--version` stamp: append the v1.2.0 schema URL to `stac_extensions`,
      set `version`. Live collection has **no `stac_extensions` key at all**
      (`None`, not `[]`) — this is a schema change, so re-validate after
- [ ] Kept **outside** `collection_patch()`'s changed-list contract, or `--check`
      starts failing after every release
- [ ] **Invalidate rather than go stale**: when the monthly patch appends items,
      *remove* `version`. A wrong version is worse than an absent one — absent says
      "check yourself", wrong says "you already have this"
- [ ] Never call `git describe` from `collection_patch.py` — CI's checkout is
      shallow with no tags; take `--version` explicitly and hard-fail on no-tag

## Phase 6: Docs, NEWS, follow-up issue

- [ ] Five stale registration claims: `scripts/README.md` (4 spots incl. the
      data-flow diagram), `README.Rmd`, `NEWS.md`, `update.yml` header, `CLAUDE.md`
- [ ] `README.Rmd` re-knit in its **own** commit — `README.md`/`index.html` are
      tracked build outputs and will bury a functional diff
- [ ] Record measured runtimes, not estimates
- [ ] File the **Python package extraction** issue (~1,618 Python vs ~500 R lines;
      three deps with no R equivalent), sequenced after #29

## Validation

- [ ] `python -m pytest tests/ -q` green
- [ ] **Restore-the-bug**: feed 102k lines on stdin and confirm it completes where
      an argv version fails — a guard nobody has seen fail is decoration
- [ ] `--dryrun` touches nothing (`git status` clean immediately after)
- [ ] Live end-to-end on ~5 real ids: set equality holds, re-run is a no-op
- [ ] API serves the full catalogue throughout — no window where it does not
- [ ] `/code-check` clean on each commit
- [ ] `/planning-archive` on completion
