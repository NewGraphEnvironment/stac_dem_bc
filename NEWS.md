# stac_dem_bc

Versions describe the **published catalogue** — the state of `s3://stac-dem-bc`
and, once registered, the API at <https://images.a11s.one>. They do not track the
scripts independently: a tag means "the catalogue is in this state". Same
convention as [`stac_uav_bc`](https://github.com/NewGraphEnvironment/stac_uav_bc).

`DESCRIPTION` is a dependency manifest for the GitHub Actions runner
(`Type: Project`, pinned at `0.0.0.9000`) and is deliberately **not** versioned —
matching `water-temp-bc`. Releases live here and in git tags.

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
