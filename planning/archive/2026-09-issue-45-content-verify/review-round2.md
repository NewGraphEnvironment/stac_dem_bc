# Review round 2 — #45 staged diff

Scope: `git diff --cached` (catalogue_register.sh, register_manifest.py, the two test
files, NEWS.md, CLAUDE.md), plus collection_register.sh / item_register.sh. Mutation
probes ran in a copy at /tmp/cc45_r2. Full suite passes there (124 passed). Live probes
were single requests only.

## Findings

- **[fragile]** scripts/catalogue_register.sh:320-333 with scripts/register_manifest.py:866-870
  (`verify-serving` → `published_digests`): under `--all` and `--ids-file`, a
  collection.json whose item link is duplicated **with an identical href** passes every
  pre-write guard. `hrefs-published` emits both rows, `cut -f2 | sort -u` collapses them,
  so `N_URLS == N_FETCH_IDS`, and `fetched-paths` builds a dict, which dedupes. The run
  registers the collection and every item, and only then does `verify-serving` call
  `published_digests()`, which raises `ValueError: id 'a0' has more than one item link`
  as an uncaught traceback, exit 1. Reproduced in the copy: 3 items plus one duplicated
  link, `--all`, ssh stub accepting writes. Result: `writes 2` (collections + items), then
  the traceback, rc 1. Before this diff the same input registered and verified cleanly.
  So a write that succeeded is reported as a crash, and it happens on every `--all` rerun
  (stac_airphoto_bc's routine path, `run_pipeline.sh:71`). `--drift`/`--verify` refuse
  the same input before any write, because `diff` calls `published_digests`
  first. That is the order NEWS promises ("refused … instead of being deduped"), and for
  `--all`/`--ids-file` it holds only after the write. Neither live collection has a
  duplicate today (DEM 102,460 links / 102,460 distinct hrefs / 102,460 distinct ids;
  airphoto 10,100 / 10,100 / 10,100, checked 2026-09-29), so this is latent. The cheap
  pre-write guard is to also require `count_lines hrefs.tsv == N_FETCH_IDS` beside the
  existing `N_URLS` check.

- **[fragile]** scripts/catalogue_register.sh:519-557: the post-write check does not
  cover items whose *served* body changes because the **collection** was upserted.
  stac-fastapi-pgstac hydrates each stored item against the collection's current
  `base_item` when it reads the item. pgstac builds `base_item` from the collection's
  `item_assets` **and its `stac_version`** (`collection_base_item()`), and item
  dehydration strips values equal to the base at load time. So when `COLL_STATE=changed`,
  `diff` has already compared items hydrated against the OLD base. After
  `collection_register.sh`, `verify-serving` checks only the todo ids, and
  `collection-state` checks only the collection. Suppose the collection gains or changes
  `item_assets`, or bumps `stac_version` while the items do not. Then every untouched
  item is served differently, and the run still prints `OK … DONE`. The next `--verify`
  reports up to ~102k `changed`, and the following `--drift` re-registers them and
  converges, so no data is lost. CLAUDE.md's new bullet names the `item_assets` case, but
  says it "surfaces as a run that reports `changed`". That is the *next* run: the run
  that caused it passes its own post-write check. It also does not name `stac_version`.
  Latent today: neither live collection has `item_assets`, and collection and items are
  both at `stac_version` 1.1.0. The `base_item` composition comes from pgstac's source as
  I know it; I did not measure it, because a probe would need a write.

## Checked and not a finding

- **Digest canonicalisation across value types.** The API path is jsonb (numeric), then
  orjson. A Python shortest-repr float is stored exactly by numeric and parses back to
  the same double, `e-` exponents included (`1e-05` → `0.00001` → `1e-05`). Only two
  forms would not round-trip: `-0.0`, which numeric normalises to `0.0`, and a float
  with `|x| >= 1e16`, which Python writes as `1e+16` and numeric emits as an integer, so
  it reads back as an int. Neither occurs in the 10,101 local airphoto bodies
  (`stac_airphoto_bc/data/stac`, scanned). The DEM claim is measured on all 102,460.
  Either one would fail **loudly** in `verify-serving` after the first write, not
  silently. Duplicate keys: last wins on both sides. Unicode: neither side normalises.
  Null handling: `_strip_nulls` recurses into arrays' objects and keeps array elements,
  the same as `jsonb_strip_nulls`.
- **Live round-trip spot checks (single requests).** airphoto item 2327503: published
  vs served digest equal. Airphoto collection: equal. DEM collection, 16 MB with 102,460
  links: equal. No CDN or cache on `/collections/{id}` (`via: Caddy`, no cache headers),
  so the post-write `collection-state` reads fresh.
- **Callers of the changed CLI.** `diff` now requires `--fetch-dir` and
  `verify-serving` requires `--hrefs-file`/`--fetch-dir`. `ids_serving` is removed. No
  caller outside `catalogue_register.sh` and the tests: I grepped this repo, the
  workflows and all of `~/Projects/repo`. stac_airphoto_bc invokes only
  `catalogue_register.sh --all && --verify`, and its collection and a sampled item
  round-trip, so its post-write collection check and new content `--verify` pass.
- **`--all`/`--ids-file` behaviour.** The dryrun output is unchanged. `audit-items` on
  stdin paths reads the same set `--dir` did, with `--expect` tied to `N_TODO`. The
  collection re-check after the write holds, because collection_register.sh loads
  collection.json verbatim.
- **Resources at 102,460 items.** `diff` keeps about 30 MB of digests. One 10k page is
  ~18 MB / 29 s against the 180 s per-read timeout. `verify-serving` makes 205 POSTs of
  500 full bodies each. The fetcher submits 102k futures at once, which is fine, and
  keeps one Session per thread. `$TMPDIR` holds roughly 200 MB of bodies plus the 16 MB
  collection. `.json.part` never matches `find -name '*.json'`.
- **Tests.** The new string assertions are specific (`"changed:  a (2) b"`,
  `"to register: 3"` with only 3 items). `_loads` pins what was loaded, not only that
  something was. `test_the_harness_is_network_proof` would fail if the fetch reached
  the network. None of these passes for the wrong reason.
