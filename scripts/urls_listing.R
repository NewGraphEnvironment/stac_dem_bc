#!/usr/bin/env Rscript
# Shared objectstore listing: one bucket walk, three derived artifacts.
#
# Sourced by scripts/urls_fetch.R and scripts/detect_changes.R so both derive
# DEM keys, DSM keys and dsm/ directory membership from a single walk rather
# than paying for the listing three times.
#
# Guards, in order of how badly each fails if it is wrong:
#
#   1. ngr::ngr_s3_keys_get() aborts on a non-200 response, so a failed walk
#      raises rather than returning an empty character vector. That distinction
#      is the whole point: an empty DSM set means "this bucket has no surface
#      models", and acting on that when the call merely failed would strip the
#      dsm asset off every item in the catalog.
#   2. A walk that succeeds but returns implausibly few keys is treated as
#      truncated and refuses to return. The bucket has only ever grown.
#   3. Zero DEM or zero DSM keys from a successful, plausible walk is still an
#      error -- both products are known to exist, so zero means the filter
#      broke, not that the province deleted 200k rasters.

URL_BUCKET <- "https://nrs.objectstore.gov.bc.ca/gdwuts"

# Floor for the whole-bucket walk. The bucket held 575,411 keys on 2026-08-28;
# anything under half that is a truncated listing, not a real shrink.
KEYS_MIN <- 280000

#' Walk the objectstore once and derive the DEM/DSM listing artifacts
#'
#' @return list with `dem` (character URLs), `dsm` (character URLs),
#'   `dsm_groups` (character mapsheet-year paths) and `n_keys` (total walked)
urls_listing_fetch <- function(url_bucket = URL_BUCKET, keys_min = KEYS_MIN) {

  # No pattern: one full walk, filtered below. ngr aborts on a non-200, so a
  # failed call raises here rather than yielding character(0).
  all_urls <- ngr::ngr_s3_keys_get(
    url_bucket = url_bucket,
    prefix = "",
    pattern = NULL
  )
  all_urls <- as.character(all_urls)

  if (length(all_urls) < keys_min) {
    stop(sprintf(
      "bucket walk returned %d keys, under the %d floor - refusing to use a truncated listing",
      length(all_urls), keys_min
    ))
  }

  # DEM: unchanged from the original pattern = c("dem", "*.tif") filter, on
  # purpose. It matches both the mapsheet deliveries under <block>/<sheet>/
  # <year>/dem/ and the 2,245-tile albers10k2m/_completed_dem/ set, and every
  # one of those is already an item in the published catalog. Narrowing the
  # filter here would present 2,245 live items to change detection as deletions.
  dem <- all_urls[grepl("dem", all_urls, fixed = TRUE) & grepl("\\.tif$", all_urls)]

  # DSM: anchored on the product directory rather than a substring, so a DEM
  # that happens to be named *_dsm.tif could never be indexed as its own DSM.
  dsm <- all_urls[grepl("/dsm/", all_urls, fixed = TRUE) & grepl("\\.tif$", all_urls)]

  # Every mapsheet-year with a dsm/ directory, whatever it holds -- including
  # the 11 that hold only .laz. Derived from ALL keys, not just rasters; that
  # is what makes "published as point cloud only" a declared coverage gap
  # rather than something indistinguishable from "no DSM was delivered".
  dsm_any <- all_urls[grepl("/dsm/", all_urls, fixed = TRUE)]
  dsm_groups <- sort(unique(sub("/dsm/.*$", "", sub("^.*/gdwuts/", "", dsm_any))))

  # sub() returns its input UNCHANGED when the pattern does not match, so a
  # change to the URL shape would silently emit full URLs as group names. Those
  # match no DEM group, which flips every .laz-only tile from "no raster DSM"
  # to "no dsm/ directory" — a wrong answer, with nothing to signal it. Assert
  # the derived shape instead of trusting the substitution.
  bad_groups <- dsm_groups[!grepl("^[0-9]{3}/[0-9]{3}[a-z]/[0-9]{4}$", dsm_groups)]
  if (length(bad_groups) > 0) {
    stop(sprintf(
      "%d dsm/ group names are not <block>/<mapsheet>/<year> (e.g. '%s') - URL shape changed?",
      length(bad_groups), bad_groups[1]
    ))
  }

  if (length(dem) == 0) {
    stop("bucket walk succeeded but yielded 0 DEM .tif keys - filter is broken")
  }
  if (length(dsm) == 0) {
    stop("bucket walk succeeded but yielded 0 DSM .tif keys - filter is broken")
  }

  cat(sprintf("  Walked %d keys: %d DEM .tif, %d DSM .tif, %d dsm/ directories\n",
              length(all_urls), length(dem), length(dsm), length(dsm_groups)))

  list(dem = dem, dsm = dsm, dsm_groups = dsm_groups, n_keys = length(all_urls))
}
