# stac_dem_bc

Versions describe the **published catalogue** — the state of `s3://stac-dem-bc`
and, once registered, the API at <https://images.a11s.one>. They do not track the
scripts independently: a tag means "the catalogue is in this state". Same
convention as [`stac_uav_bc`](https://github.com/NewGraphEnvironment/stac_uav_bc).

`DESCRIPTION` is a dependency manifest for the GitHub Actions runner
(`Type: Project`, pinned at `0.0.0.9000`) and is deliberately **not** versioned —
matching `water-temp-bc`. Releases live here and in git tags.

## v1.0.0 (unreleased — tag is cut after the publish is verified)

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
  catalogue. It lagged by ~38k items before this release.
