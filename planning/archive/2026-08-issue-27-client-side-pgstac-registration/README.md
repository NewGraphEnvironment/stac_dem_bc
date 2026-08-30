# Issue #27 — client-side pgstac registration + STAC Version Extension

**Branch:** `27-client-side-pgstac-registration` · **Tests:** 51 → 90

## Outcome

Registration into pgstac now lives in this repo and never deletes:

```bash
scripts/catalogue_register.sh --verify   # is the API behind S3? changes nothing
scripts/catalogue_register.sh --drift    # register whatever it is missing
```

`--drift` asks the API which items it holds, diffs against what `collection.json`
publishes, and registers the difference. It is stateless, so the condition that
put the API 38k items behind for a month — someone forgetting — is now
self-correcting rather than something to remember.

Everything upserts. The previous path DELETEd the collection before reloading it
and on 2026-08-29 failed in between, leaving the public API serving zero items.
`pgstac.items.collection` is `ON DELETE CASCADE`, so dropping the collection row
takes every item with it — which is also why the collection registers *before*
its items, the deliberate inverse of the S3 sync order.

Plus the STAC Version Extension the collection never carried: `--version` stamps,
`--clear-version` removes. The monthly run clears it, because once items are
appended the previous version is *false* rather than stale.

## The issue's premise was wrong, and the body now says so

#27 claimed it blocked #34: "renaming forces a full re-registration… which means
taking the API down." It does not. `stac-elevation-bc` is a **new** collection
id, so loading it leaves `stac-dem-bc` serving untouched until dropped
deliberately — the rename was always zero-downtime, under the delete-then-load
tool too. What #27 actually buys is the monthly lag and making same-id
re-registration safe.

## Four defects fixed rather than inherited from `stac_uav_bc`

| inherited | why it fails here |
|---|---|
| item paths as argv | 102,460 filenames is 7.8 MB against a 1 MB ARG_MAX |
| fixed remote temp path | concurrent runs clobber; cleanup skipped on failure |
| items before collection | only worked because the collection pre-existed |
| password in argv | visible in `ps aux`; now via the PG* environment |

`--method upsert` is explicit every time, because the default is `insert`.

## What the review was worth

| round | findings | real bugs |
|---|---|---|
| 1 | 8 | 3 |
| 2 | 4 | 1 |
| 3 | 4 | 1 |
| 4 | 0 | 0 — **Clean** |

The headline bug: **verification failed on every run of more than 10 items.**
The API's default `limit` is 10, so the verify saw 10 of 600 ids and reported the
rest missing — every real run would have upserted correctly and then exited 1
claiming failure. It shipped because the only exercise was a 3-item upsert, and
**3 < 10**: a fixture structurally incapable of reaching the failure.

**Round 3 is the one worth re-reading.** Asked what *mechanism* kept producing
the same shape rather than for more instances, it found that of nine counts in
the pipeline eight are structural, and `N_TODO` vs `N_FETCHED` was the only pair
that could disagree — all three shipped bugs landed on exactly it. The inputs had
been patched three times while the comparison was never touched. The expectation
now derives from `urls.txt`, the artifact the fetch loop iterates.

Also found: `--clear-version` was never wired into `update.yml`, so the whole
invalidate-rather-than-go-stale design was inert — 31 tests passed because they
tested the function, not the wiring. And `STAC_COLLECTION` / `STAC_BUCKET_URL`
are two knobs over one fact that were never reconciled, while the API answers an
*unknown* collection with `200` and zero features.

## Things that only turned up by measuring

- The API has no `/aggregate` (404) and returns `numberMatched: null`, and a
  `/search` on a list of ids **silently omits** ids that do not exist. So
  verification is set equality in both directions, never a count.
- `wc -l` misses a final unterminated line; `grep -c ''` counts it but exits 1 on
  an empty file, killing the routine zero-drift path under `set -e`. Opposite
  failure directions, one helper.
- The URL encoding is **lossy**: a filename containing the literal text `%20`
  encodes to itself, indistinguishable from an encoded space. Unreachable today,
  asserted as a known limitation.
- `146.190.12.8` and `geopro` are the same machine — a DigitalOcean reserved IP
  and the tailnet name.

## Not in scope

- **Registration from CI** — no runner can reach the host (no Tailscale action,
  no SSH deploy key in any of these repos). An infrastructure decision that
  unblocks every catalogue repo at once.
- **The Python package extraction** — filed as #37, sequenced after #29.
