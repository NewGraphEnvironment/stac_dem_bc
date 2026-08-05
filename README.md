stac_dem_bc
================

<!-- README.md is generated from README.Rmd. Please edit that file -->

![status](https://img.shields.io/badge/status-functional-green)
![updates](https://img.shields.io/badge/updates-monthly-blue)
![api](https://img.shields.io/badge/api-images.a11s.one-orange)

[`stac_dem_bc`](https://github.com/NewGraphEnvironment/stac_dem_bc)
serves British Columbia’s [LidarBC](https://lidar.gov.bc.ca/) digital
elevation model collection — nearly 100,000 GeoTIFFs on the provincial
objectstore as of July 2026 — as a [SpatioTemporal Asset Catalog
(STAC)](https://stacspec.org/), searchable by location and time from the
[`rstac` R package](https://brazil-data-cube.github.io/rstac/), QGIS
(v3.42+), or any STAC-compliant client. The API endpoint is
<https://images.a11s.one>.

<br>

The catalog refreshes monthly: a scheduled [GitHub Actions
workflow](.github/workflows/update.yml) detects new tiles on the
objectstore and appends them incrementally, with each month’s changes
landing as a commit to [`data/`](data/) — a git audit trail of what was
added, removed, and validated. How the automation works, its failure
modes, and where the evidence lives are documented in
[`scripts/`](scripts/README.md), alongside a plain-language guide to the
concepts behind the pipeline (COG, STAC, pgstac, validation caching).

<br>

<img src="fig/cover.JPG" alt="" width="100%" style="display: block; margin: auto;" />

<br>

## Query the collection

Use [`bcdata`](https://github.com/bcgov/bcdata) to define an area of
interest, then query the `stac-dem-bc` collection for DEM tiles
intersecting it. Below: all DEMs covering the Bulkley River watershed
group between 2018 and 2020.

``` r
aoi <- bcdata::bcdc_query_geodata("freshwater-atlas-watershed-groups") |>
  bcdata::filter(WATERSHED_GROUP_NAME == "Bulkley River") |>
  bcdata::collect() |>
  sf::st_transform(crs = 4326)

date_start <- "2018-01-01T00:00:00Z"
date_end <- "2020-12-31T00:00:00Z"

# use rstac to query the collection
q <- rstac::stac("https://images.a11s.one/") |>
  rstac::stac_search(
    collections = "stac-dem-bc",
    intersects = jsonlite::fromJSON(
      geojsonsf::sf_geojson(
        aoi, atomise = TRUE, simplify = FALSE
      ),
      simplifyVector = FALSE
    ) |> (\(x) x$geometry)(),
    datetime = paste0(date_start, "/", date_end)
  ) |>
  rstac::post_request()

# get details of the items
r <- q |>
  rstac::items_fetch()

# burn the results locally so we can serve it instantly on index.html builds
saveRDS(r, "data/stac_result.rds")
```

``` r
r <- readRDS("data/stac_result.rds")
# build the table to display the info
tab <- tibble::tibble(
  url_download = purrr::map_chr(r$features, ~ purrr::pluck(.x, "assets", "image", "href"))
)
```

<br>

Please see <http://www.newgraphenvironment.com/stac_dem_bc> for the
published table of collection links.

## QGIS Data Source Manager (v3.42+)

QGIS 3.42 added native STAC support — connect directly to the catalog
and filter by the current map view. See [Lutra Consulting’s STAC-in-QGIS
blog post](https://www.lutraconsulting.co.uk/blogs/stac-in-qgis) for a
walk-through.

<div class="figure">

<img src="fig/a11sone01.png" alt="Connecting to https://images.a11s.one" width="100%" />
<p class="caption">

Connecting to <https://images.a11s.one>
</p>

</div>

<div class="figure">

<img src="fig/a11sone02.png" alt="Using the field of view in QGIS to filter results" width="100%" />
<p class="caption">

Using the field of view in QGIS to filter results
</p>

</div>

## Sister collections on the same endpoint

The same `images.a11s.one` STAC API serves several complementary BC
collections:

- [`stac_uav_bc`](https://github.com/NewGraphEnvironment/stac_uav_bc) —
  UAV imagery, organized by watershed
- [`stac_airphoto_bc`](https://github.com/NewGraphEnvironment/stac_airphoto_bc)
  — historic airphoto thumbnails (1963–2019)

## Roadmap

The big one landed in 2026: the catalog is now **self-updating**
([\#23](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/23)) —
the goal open since the first build — and the July catch-up grew the
collection from 58k to ~98k fully-validated items. Still ahead:

- **Incremental registration** — the post-sync pgstac registration is a
  full reload today (~80 minutes at current scale); upserting only each
  month’s new items brings it to seconds. Tracked in the infrastructure
  repo.
- **Upstream-deletion handling**
  ([\#28](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/28))
  — propagate objectstore removals to the catalog; on hold until the
  September run shows whether recent removals were renames.
- **URL-encoded item hrefs**
  ([\#25](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/25),
  fix in review as [PR
  \#26](https://github.com/NewGraphEnvironment/stac_dem_bc/pull/26)) —
  90 legacy items carry literal spaces in their download links, which
  strict HTTP clients reject.
- **True footprint geometry**
  ([\#2](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/2)) —
  recalculate per-item footprints to exclude no-data pixels rather than
  using bounding boxes; gives accurate spatial-overlap queries.
- **Structured logging + performance benchmarking**
  ([\#6](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/6)) —
  instrument the pipeline so build performance is quantifiable across
  runs.
- **uv-based Python dependency management**
  ([\#16](https://github.com/NewGraphEnvironment/stac_dem_bc/issues/16))
  — the CI workflow already installs with uv; migrate the local conda
  environment to match.

Browse [open
issues](https://github.com/NewGraphEnvironment/stac_dem_bc/issues) for
the full backlog.

## License

[MIT](LICENSE).
