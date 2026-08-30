# Progress — Client-side pgstac registration (#27)

## Session 2026-08-30

- Archived and closed #31 (v1.0.0 shipped); reconciled 6 stale checkboxes
- Plan-mode exploration: 3 Explore agents (stac_uav_bc reference, this repo's
  surface, rtj's host + server-side script) plus direct probes of the live API
  and host
- Corrected the issue's premise: #27 does **not** unblock #34 — a rename to a new
  collection id is already zero-downtime. The real value is the monthly lag and
  making same-id re-registration safe
- Plan approved by user; scope set to registration + version stamp, with `--all`
  and drift both owned here, plus `collection_unregister.sh` for #34
- Plan agent review returned 4 blockers folded in pre-baseline: FK ordering
  (collection before items), stdin already spoken for, remote-side count guard,
  and version-is-not-an-item-check
- Next: Phase 1 (`register_manifest.py`) — lands first because the shell cannot
  be correct without it

### Implementation (2026-08-30)

All six phases landed. Commits, in order: `register_manifest.py` + tests →
the two register scripts → `catalogue_register.sh` + `collection_unregister.sh`
→ version extension → docs → README regeneration → `count_lines` fix → review
fixes.

**Measured against the live catalogue** (not inferred from a green run):

| | |
|---|---|
| published / registered | 102,460 / 102,460 — 0 missing, 0 orphaned |
| `--verify` runtime | 3m40s, almost all of it API id enumeration (11 requests) |
| real 3-item upsert | `loaded 3 item(s)`, ids returned as an exact set match |
| stamped collection | validates against STAC 1.1.0 + version ext, 17.6s, all links preserved |
| tests | 51 → 87 |

**Guards proven to fire rather than asserted:** remote truncation (sent 2 lines
claiming 3 — refused, loaded nothing); foreign-host URL (`url_to_item_id`
returns `'f-2022-dem-x'`, plausible and matching nothing); collection-id
injection; unknown id in `--ids-file`.

### Two bugs I introduced and caught by testing

- Fixing `wc -l` (misses a final unterminated line) with `grep -c ''` introduced
  a worse bug: `grep -c` exits 1 on an *empty* file, which under `set -e` kills
  the routine zero-drift case. Reproduced before believing it. `count_lines`
  handles all four inputs.
- My first "the guard exits 0" reading was wrong: `$?` after `cmd | tail` is
  tail's status. Re-measured without the pipe — the guard was correct all along.

### Round 1 review: three real bugs, all silent

1. **Verification failed on any run of >10 items.** `/search` default limit is
   10, and the inline verify body had none — 600 registered ids returned 10.
   Every real run would upsert correctly then exit 1. It shipped because the
   only exercise was 3 items, and 3 < 10: a fixture that structurally could not
   reach the failure.
2. **`--clear-version` was never wired into `update.yml`**, so the whole
   invalidate-rather-than-go-stale design was inert. 31 tests passed because
   they tested the function, not the wiring.
3. **`--verify` gated on `missing` only** while three documents promised both
   directions — it would have reported IN SYNC over any number of orphans.

Plus five fragile items fixed. The riskiest fix (raising when paging stops
early) was checked against the live API rather than reasoned about: the last
page carries no `next` link at all, so the raise cannot fire on a normal
enumeration.

### Next

- Round 2 review of the fixes (a fix written under a wrong assumption
  reproduces the defect)
- `/planning-archive`, then the PR

### Review converged after four rounds

| round | findings | real bugs | outcome |
|---|---|---|---|
| 1 | 8 | 3 | all fixed |
| 2 | 4 | 1 | all fixed; confirmed round 1's fixes correct |
| 3 | 4 | 1 | all fixed; **answered the mechanism question** |
| 4 | 0 | 0 | **Clean** — no defect in round 3's fixes |

Round 3 is the one that paid. Asked what *mechanism* kept producing the same
shape rather than for more instances, it found that of nine counts in the
pipeline, eight are structural and `N_TODO` vs `N_FETCHED` was the only pair that
could disagree — and all three shipped bugs landed on exactly it. The inputs had
been patched three times (`sort -u` twice, a set once); the comparison never was.
The expectation now derives from `urls.txt`, the artifact the fetch loop
iterates, so the count and the counted thing share a producer.

Round 4 ran the check that mattered: both decoders compared across all 102,460
published links, **0 differences**, and 0 duplicate hrefs or ids — so the new
`N_URLS != N_TODO` guard cannot fire on healthy data.

### Restore-the-bug, done properly

Nearly checked this box without earning it. Ran for real against 102,460 files:

| form | result |
|---|---|
| argv (the reference implementation's) | `[Errno 7] Argument list too long` — 7.8 MB against a 1 MB ARG_MAX |
| stdin (this repo's) | 102,460 items, 16.3 MB NDJSON, 4.7 s |

`xargs` split the same list into 5000 + 2460 — the "silently splits one atomic
load into many, with no failure aggregation" problem in the reference's
`catalogue_release.sh`, visible in passing.

Final state: 90 tests, `--verify` green (102,460 / 102,460 / 0 / 0).
