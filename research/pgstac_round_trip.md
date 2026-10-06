# What pgstac changes about a STAC body between load and serve

> **Continued in [stacs](https://github.com/NewGraphEnvironment/stacs/blob/main/research/pgstac_round_trip.md)**
> since #49, which moved the comparison there and re-verified these rules on
> 2026-10-06 (RFC 8785 digest; both live collections). This file is the original
> 2026-09-29 measurement and is kept as that record: the scripts and functions it
> names were removed by #49.

**Verified:** 2026-09-29 · **Issues:** #45 · **Produced by:** `scripts/catalogue_register.sh --verify`
over the live catalogue (logs `logs/20260929_*_verify_content_45*.log`, gitignored; numbers
in `planning/archive/2026-09-issue-45-content-verify/`)

A body published to S3 and the body `images.a11s.one` serves for the same id are
**not byte-identical**, and they're not identical as parsed JSON either. Anything that
compares the two must canonicalise these differences first, or it reports items as
changed forever. `scripts/register_manifest.py` `_canonical` / `body_digest` is the
implementation.

| difference | how it shows up | scale (stac-elevation-bc, 102,460 items) |
|---|---|---|
| `links` rewritten | the published `collection` link is replaced by the API's own self/root/parent/collection set, on its host | every item |
| null object members dropped | `"proj:epsg": null` is served with the key absent (Postgres `jsonb_strip_nulls` semantics: object fields only, never array elements) | 160 items |
| integral floats served as integers | PostGIS rebuilds geometry, so `-126.0` comes back as `-126`. The same follows for `-0.0` and floats ≥ 1e16 (Postgres numeric) | 29 items |

After canonicalising those three, all 102,460 elevation items and all 10,100
stac-airphoto-bc items compare equal, and so do both collections.

**The sample did not find any of this.** 2,000 items compared equal with only `links`
removed; both later differences showed up only in the full-population run.

**Hydration.** pgstac stores items dehydrated against a base built from the collection
(its `item_assets` and `stac_version`) and rehydrates them on read. So a collection
upsert can change how items that were never touched read back. Neither live collection
has `item_assets` today. This is why `--all`, and a `--drift` whose collection changed,
re-compare the whole catalogue after writing.
