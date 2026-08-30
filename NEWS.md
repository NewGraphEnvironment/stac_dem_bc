# stac_dem_bc

Versions describe the **published catalogue** — the state of `s3://stac-dem-bc`
and, once registered, the API at <https://images.a11s.one>. They do not track the
scripts independently: a tag means "the catalogue is in this state". Same
convention as [`stac_uav_bc`](https://github.com/NewGraphEnvironment/stac_uav_bc).

`DESCRIPTION` is a dependency manifest for the GitHub Actions runner
(`Type: Project`, pinned at `0.0.0.9000`) and is deliberately **not** versioned —
matching `water-temp-bc`. Releases live here and in git tags.

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
