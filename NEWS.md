# stac_dem_bc

Versions describe the **published catalogue** — the state of `s3://stac-dem-bc`
and, once registered, the API at <https://images.a11s.one>. They do not track the
scripts independently: a tag means "the catalogue is in this state". Same
convention as [`stac_uav_bc`](https://github.com/NewGraphEnvironment/stac_uav_bc).

`DESCRIPTION` is a dependency manifest for the GitHub Actions runner
(`Type: Project`, pinned at `0.0.0.9000`) and is deliberately **not** versioned —
matching `water-temp-bc`. Releases live here and in git tags.

## Unreleased

**Catalogue change (#2, #55)**, published by dispatching `update.yml` with `footprint: true`
after this merges. Until that dispatch runs, the published catalogue is unchanged.

- **Item geometry is the tile's footprint**, not its raster extent. For tiles named on the BCGS
  1:2,500 grid it is the grid cell minus any no-data inside it; for the rest (no-quadrant
  sheet ids, `albers10k2m`) it is the valid data. Delivered rasters are the UTM rectangle
  around the cell plus ~22 m of overlap, so a full tile's footprint is exactly its cell and
  neighbours no longer overlap. A search over an area a tile has no data for no longer returns
  it — in a sample of 144 tiles, 21 of 112 interior and 8 of 32 delivery-edge tiles had gaps
  inside their cell, up to 48% of it. `bbox` is the bounds of the published geometry, and
  `proj:geometry` the same footprint in the item's CRS. QGIS draws `bbox`, so a partly empty
  tile's box shrinks; a diagonal strip of data still draws as a rectangle.
- The `dem` asset carries `raster:bands[0].statistics.valid_percent` and, where the whole file
  was read (every tile up to 64 MB), `file:checksum` (sha256 multihash) and `file:size`.
  Larger files are read at their coarsest overview and carry no checksum. The DSM is assumed
  to cover what the DEM covers.
- `lidarbc:delivery` (`"092/092g/2016"`) on every item from a LidarBC delivery — every item but
  the `albers10k2m` ones — the key stac-pointcloud-bc items carry, plus one `related` link from
  the collection to stac-pointcloud-bc (#55). The `lidarbc:` schema is crate's (`lidarbc`
  v1.1.0). Declaring the key as a queryable is pgstac-side and waits on
  NewGraphEnvironment/stacs#8.
- The `albers10k2m` items' unprefixed `datetime_unknown` is now `lidarbc:datetime_unknown`.
- Not repaired, reported: two tiles named `…_utm11_…` declare UTM zone 14 (EPSG:6657), so their
  published items sit in Manitoba — `bc_082e003_3_2_2_xli1m_utm11_20240721_20240726` and
  `bc_082f010_1_1_1_xli1m_utm11_20241008_20250813`. The footprint step refuses them
  (`crs_mismatch`) and they keep their published geometry.
- Monthly runs compute footprints for tiles not yet cached before building items, time-boxed
  and non-fatal: a tile it does not reach is built with its extent and rebuilt the next month
  from `data/urls_footprint_changed.txt`. `item_fields.py audit` checks every staged set for
  the new fields.

Tooling — the published catalogue is unchanged by these.

- Registration and verification are now the
  [`stacs`](https://github.com/NewGraphEnvironment/stacs) package, pinned at
  `v0.1.0` (#49), in place of `scripts/catalogue_register.sh`. This catalogue
  declares itself in `stacs.toml`: the API, the collection id, the bucket, the asset
  rules (`dem` required, `image` forbidden), and the transport.
  `tests/test_stacs_config.py` pins each value to the module that defines it.
  `--verify|--drift|--all|--ids-file` became `stacs verify` and
  `stacs register --mode drift|all|ids`. The three registration scripts were deleted,
  along with everything in `register_manifest.py` except `ids-from-urls`. Before the
  switch, parity was measured over both live collections: the same missing / changed /
  orphaned sets, and the same verdict on every item.
- Verification compares **content**, not just ids (#45, carried into stacs). A
  rebuild keeps every id, so at v2.0.0 a catalogue whose bodies had all been rewritten
  verified `IN SYNC`, and a drift run never refreshed it. Every published body is now
  compared with the API's by digest over canonical JSON without `links`, the one
  member the API rewrites. Two things pgstac's round trip does not preserve are
  canonicalised on both sides, each found by the first full run: null members (160
  items carry `"proj:epsg": null` and are served without the key) and integral floats
  (PostGIS serves `-126.0` as `-126`, 29 items). After that, all 102,460 items and all
  10,100 stac-airphoto-bc items compared equal. `verify` reports `changed` beside
  `missing` and `orphaned`, and drift registers missing ∪ changed. The collection's
  own body is compared too, so a version bump with no item change is no longer
  invisible. A body that cannot be fetched or read fails the run; it is never counted
  as unchanged.
- After any registration, the served bodies are checked against the ones sent. After
  `--mode all`, or a drift whose collection body changed, the whole catalogue is
  re-compared, because pgstac serves items hydrated against their collection.
- Refused before anything is written: an item linked twice in `collection.json`; a
  body that names another id; a body about to be sent that fails the audit (wrong
  collection, missing `dem`, or still keyed `image`).
- The audit's asset rules are **declared** in `stacs.toml`. A flag can add to them and
  never loosen them, whatever collection id it names. Another collection on the
  endpoint registers from its own repo's `stacs.toml`.
- The monthly workflow audits items with `stacs audit --config stacs.toml`, against
  the declared collection id rather than the one read from the fetched
  `collection.json`. The asset rules now apply on **every** run, backfill included;
  before, they applied only on monthly and rename runs. An item still keyed `image`
  would recreate #34's mixed catalogue, so it now fails before reaching S3.
- `register_manifest.py ids-from-urls` refused every real line of the source lists,
  because `urls_list.txt`, `urls_dsm.txt`, `urls_new.txt` and `urls_deleted.txt`
  store the scheme as `https:/`. It now normalises with `stac_utils.fix_url`, as every
  other reader does.
- Behaviour changes: a drift probes the API and ssh **before** its fetch, so it needs
  the tailnet even in a month with nothing to register. A drift with `--dryrun` probes
  neither, and still fetches the whole catalogue, because what changed is a question
  about the bodies.

## v2.0.0 (2026-09-01)

**Breaking, in two ways at once.** The collection is renamed and the bare-earth
asset key with it, in one break rather than two — which is the whole reason #31
deferred them to here.

| | before | after |
|---|---|---|
| collection | `stac-dem-bc` | **`stac-elevation-bc`** |
| bare-earth asset | `image` | **`dem`** |
| item ids | unchanged | unchanged |
| S3 bucket | `stac-dem-bc` | **unchanged** |

```r
rstac::stac_search(collections = "stac-elevation-bc", ...)
purrr::pluck(feature, "assets", "dem", "href")
```

### Why (#34)

The collection stopped holding only DEMs at v1.0.0, when every item gained a
`dsm` asset. The name described one of two products, and `image` was never
descriptive — it became actively ambiguous beside `dsm`, to the point where the
published description had to spend a sentence saying which was which. That
sentence is gone.

**Item ids do not change.** The `-dem-` segment is the source product directory,
and it is what keeps the DEM/DSM/CHM tiling apart from the finer `pointcloud`
tiling. Dropping it would break every external item reference and buy nothing.

**The S3 bucket keeps its name.** Renaming it is a separate and larger decision:
it is IaC-managed, and it appears in all 102,460 item link hrefs. So every asset
href and every download link is byte-identical to v1.1.0.

### How it was done

All 102,460 published item JSONs were rewritten **in place** — fetch, edit two
fields, write back — rather than rebuilt. 60,324 of 100,345 metadata-cache rows
predate spatial-metadata caching, so a rebuild would have silently swapped ~60k
items from one code path to another, invisibly in any spot check.

`scripts/item_migrate.py`, on the harness extracted as `scripts/item_rewrite.py`:
102,460 written, 0 unchanged, **0 errors**, in 12m35s.

### The thing that made this hard

**A half-done rename is invisible to every check this repo had.** Item ids do not
change, so set equality reports `IN SYNC` over a fully mixed catalogue;
`item_register.sh` routes each item by its *own* `collection` field, so a stale
body upserts into the old collection successfully with no error; both asset keys
are legal STAC; and a count of assets cannot tell `{image,dsm}` from `{dem,dsm}`.

The property that breaks is **homogeneity, not size**. `register_manifest.py
audit-items` now checks it, and `catalogue_register.sh` runs it over every fetched
body before anything reaches the database — the files are already on disk there,
so the full-population check is free.

Also fixed, and it would have made the cutover's own verification worthless:
`search_body` had no `collections` filter, so `verify-serving` asked "is this id
served *anywhere*". Harmless with one collection; this release created two by
design, sharing all 102,460 ids.

### Verified

- Set equality both directions, twice: `IN SYNC: 102460 published, all
  registered, no orphans`
- pgstac, exact and homogeneous: 102,460 items, 102,460 with `dem`, **0** with
  `image`, 95,888 with `dsm`
- A real client query returning tiles, with the download resolving `HTTP 200`
- The one live downstream consumer (`rtj/scripts/dem/_shared.R`) run against the
  live API before its change was merged

The old collection served alongside the new one throughout and was dropped only
after all of the above. There was no window in which the API served less than it
did before.

## v1.1.0 (2026-08-30)

The catalogue is unchanged from v1.0.0 — same 102,460 items, same content. What
this release marks is that the catalogue can now *say* which version it is, and
that getting it into the API is a command rather than a memory.

### The collection carries a version (#27)

`https://images.a11s.one/collections/stac-dem-bc` now serves
`"version": "1.1.0"` and declares the STAC Version Extension. It had carried no
version at all.

`--version` stamps; `--clear-version` removes. The monthly run clears it,
because once items are appended the previous version is *false* rather than
stale, and a wrong version ("you already have this one") is worse than an
absent one ("go and check").

### Registration is client-side, and never deletes (#27)

Loading the catalogue into pgstac now lives in this repo instead of being a
manual step on the STAC host that someone has to remember:

```bash
scripts/catalogue_register.sh --verify   # is the API behind S3?
scripts/catalogue_register.sh --drift    # register whatever it is missing
```

`--drift` asks the API which items it holds, diffs against what `collection.json`
publishes, and registers the difference. It is stateless, so a month nobody
registered is simply picked up by the next run — the condition that put the API
38k items behind for a month is now self-correcting rather than something to
remember.

**Nothing in the routine path deletes.** Every load is `pypgstac --method upsert`,
so there is no window where the API serves less than it did. The previous path
DELETEd the collection before reloading it, and on 2026-08-29 it failed in
between and left the public API serving zero items until it was repaired by hand.
`pgstac.items.collection` is `ON DELETE CASCADE`, so dropping the collection row
takes every item with it — which is also why the collection must be registered
before its items.

- `catalogue_register.sh` — `--verify`, `--drift`, `--all`, `--ids-file`
- `collection_register.sh`, `item_register.sh` — the two upsert halves
- `collection_unregister.sh` — the one guarded destructive path, for #34
- `register_manifest.py` — id and NDJSON logic, testable from `tests/`

Item paths reach `item_register.sh` on **stdin**, never as arguments: 102,460
filenames is roughly 6 MB of argv against a ~2 MB limit, and that failure mode
strikes only after the expensive stage has already succeeded. The count guard
runs on the *receiving* side, because a truncated transfer otherwise loads clean
and reports success.

Verification is set equality in both directions and never a count — the API has
no aggregation extension and returns `numberMatched: null`, and a search on a
list of ids silently omits the ones that do not exist.

### Known imperfections shipping deliberately

- **Registration still runs from a laptop.** No GitHub Actions runner can reach
  the STAC host — there is no Tailscale action or SSH deploy key in any of these
  repos. That is an infrastructure decision and it unblocks every catalogue repo
  at once, so it is not made here.
- **102,460 items are published against 102,416 current source URLs.** The 44
  extra have no upstream URL any more; `--all` keeps them alive. That is #28's
  deletion-pruning debt, now visible rather than merely present.

## v1.0.0 (2026-08-29)

First versioned release of the catalogue. Every item now carries the digital
surface model alongside the bare-earth DEM.

- **`dsm` is a second asset on each item** (#31). A DEM and its DSM are the same
  flight over the same footprint at the same time, so they belong on one item.
  95,888 of 102,416 DEM tiles pair. `image` remains the bare-earth DEM — named
  for backward compatibility, and now stated as such in the collection
  description.
- **Pairing is on parsed tile id, acquisition date and utm zone**, not on a
  filename transform. The naming convention is recorded afterwards as an
  observation, so a delivery using a convention nobody has seen still pairs and
  shows up as `convention=unknown` rather than silently losing its DSM.
  Reconciles with the #29 inventory exactly: 95,768 `suffix`, 117 `identical`.
- **Coverage gaps are declared, not hidden** (`data/dsm_pairing_report.md`):
  1,211 tiles across 11 mapsheet-years whose `dsm/` directory holds only `.laz`;
  2,245 `albers10k2m` tiles that carry no NTS tile id; 172 unpaired.
- **`providers` and `keywords` on the collection** (#30). CC-BY-4.0 obliges
  attribution the metadata did not carry. Roles split producer/licensor/host
  (Province of British Columbia) from processor (New Graph Environment).
- **90 legacy items with unusable download links repaired** (#25's tail). Their
  `href`s carried literal spaces — an HTTP request cannot even be formed from
  one. The code was fixed in v0 but the published catalogue never was, and the
  monthly run re-published the broken links every time it appended to the
  collection.
- **Catalogue caught up to the bucket**: 4,420 DEM tiles that had arrived since
  the 2026-08 run.

Published state: **102,460 items** on `s3://stac-dem-bc` and registered in pgstac,
of which **95,888 carry a `dsm` asset**. The API served 60,126 before this release
— registration had not been re-run since the July catch-up (#27).

### Known imperfections shipping deliberately

- **The collection still declares `CC-BY-4.0`, and that is probably wrong.** The
  BC Data Catalogue records LidarBC as *"Access Only"* — no redistribution
  without written permission. We do not redistribute the rasters (asset hrefs
  point at the province's objectstore), but STAC's `license` field describes the
  data, so the claim needs the province's answer rather than ours. Tracked in
  #30; attribution shipped now because it is strictly better than none.
- **3 tiles pair under `convention=unknown`** — `083d/2019` ships a re-issued DEM
  beside its original (`..._2019.tif` and `..._2019_1.tif`) sharing one DSM.
  Both carry the asset; the sharing is reported rather than assumed.
- **Item ids still embed `-dem-`** even though items now carry two assets. Ids
  are identifiers, not descriptions; changing them would invalidate every
  external reference. Revisit at a collection rename, so consumers absorb one
  break rather than two.
- **pgstac registration remains a manual step** (#27), so the API can lag the
  catalogue. It lagged by ~38k items before this release. **Fixed in v1.1.0**
  (#27): `scripts/catalogue_register.sh --drift`.
