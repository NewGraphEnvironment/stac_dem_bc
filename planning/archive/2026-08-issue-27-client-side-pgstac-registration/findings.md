# Findings — Client-side pgstac registration (#27)

## Host and transport (measured 2026-08-30)

`geopro` is the STAC host. Tailnet `100.94.58.22`, eth0 `146.190.168.112`, and the
DigitalOcean **reserved** IP `146.190.12.8` — which is what `images.a11s.one`
resolves to. Two addresses, one machine; the reference implementation's hardcoded
`root@146.190.12.8` is therefore correct-but-brittle, not wrong.

Containers: `geoserv-stac` (8000), `geoserv-stac-ortho` (8002), `geoserv-db`
(pgstac v0.9.8), `geoserv-titiler`, `geoserv-caddy`. Caddy maps
`images.a11s.one` -> `stac-api` -> `PGDATABASE: stac`, so this collection is in
the `stac` DB, not `stac_ortho`.

`pypgstac` 0.9.9 at `/opt/geoserv/scripts/.venv/bin/pypgstac`, invoked
`cd /opt/geoserv/scripts && uv run pypgstac`, `PATH=/root/.local/bin:$PATH`,
`. /opt/geoserv/.env` for `POSTGRES_PASSWORD`.

**`--method` defaults to `insert`** — confirmed by running `--help` on the host:
`Default: <Methods.insert: 'insert'>`. Upsert is never implicit.

## The API cannot count

- No transaction conformance (read-only by design — today's config, not a protocol
  guarantee).
- `/aggregate?aggregations=total_count` -> **404**, no aggregation extension.
- `numberMatched` is **null** in search responses.
- Item properties carry `datetime` and `proj:*` only — **no `updated`/`created`**.

So there is no count and no timestamp available client-side. What *does* work:
`POST /search` with `fields:{include:["id"]}` and `rel:"next"` keyset paging —
102,460 ids in 11 requests, ~212 s.

**And a missing id is silently absent.** Requesting 2 ids where 1 is bogus returns
1 feature and no error. Any verification asserting on the *count* of returned
features passes vacuously; only **set equality** works, reported in both
directions (missing / orphaned).

The only real write-timestamp anywhere is server-side:
`select count(*), max(updated_at) from pgstac.items where collection = ...`.

## S3 key layout

Items are publicly readable at **flat** keys:
`https://stac-dem-bc.s3.amazonaws.com/<item-id>.json` -> 200.
`.../stac/<item-id>.json` -> 403. There is no `stac/` prefix; an early probe that
guessed one read as "the bucket is private", which it is not.

`collection.json` is 19,434,562 bytes and carries 102,460 `rel:"item"` links. The
live API response for the same collection is 1,546 bytes with 5 generated links —
pgstac stores item links the API never serves.

## Known drift already accrued

Collection item links: **102,460**. `data/urls_list.txt`: **102,416**. So 44 items
have no current upstream URL — #28's deletion-pruning debt, already visible. A
`--all` register keeps those 44 alive; the drift report should surface them rather
than launder them.

## Why the reference implementation cannot be adopted verbatim

`stac_uav_bc/scripts/config/item_register.sh`:

| inherited | why it fails here |
|---|---|
| `python3 - "$@"` (argv) | 102k filenames ~6 MB vs ~2 MB ARG_MAX |
| fixed `/tmp/stac_items.ndjson` | two concurrent runs clobber each other |
| remote `rm` after the load | skipped on failure; no trap |
| items registered before collection | works only because the collection pre-exists |
| called via `xargs` in `catalogue_release.sh` | silently splits one atomic load into many ssh+load calls with no failure aggregation |

## Ordering constraint discovered in review

`pgstac.items.collection` is `NOT NULL REFERENCES collections(id) ON DELETE
CASCADE`. **Collection must load before items.** That CASCADE is also the
mechanism behind rtj's 2026-08-29 outage — deleting the collection took the items
with it.

Note the deliberate inversion against `s3_sync-ci.sh`, which is items-first *by
design* (so a mid-run failure leaves unreferenced items rather than dangling links).
Two transports, opposite orders, same operator. Written down here so nobody
"fixes" one to match the other.

## A truncated transfer succeeds

If the local side dies mid-stream, the remote `cat` sees EOF and pypgstac loads a
syntactically valid, truncated NDJSON — exit 0, no error. A local `wc -l` guard
cannot see this. The count guard has to run **on the host**, against an expected
count passed in as an argument.

## Errors Encountered

| Error | Resolution |
|-------|------------|
| `curl https://stac-dem-bc.s3.us-west-2.amazonaws.com/stac/<id>.json` -> 403 | Wrong key path invented, not a permissions problem. Keys are flat: `https://stac-dem-bc.s3.amazonaws.com/<id>.json` |
| `docker exec geoserv-db psql ...` over ssh blocked by the permission classifier | Interactive DB reads need a Bash permission rule; scripted reads inside a registration script are a separate path. Not worked around. |
